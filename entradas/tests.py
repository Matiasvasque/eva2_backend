"""
Suite de pruebas automatizadas del backend.

Se corre con:
    python manage.py test

Cubre: JWT con roles, permisos RBAC, carro persistente 1 a 1, checkout atómico
(descuento de stock, tickets UUID, congelamiento de precio), cancelación con
reposición de stock, transiciones de estado inválidas, validadores de modelo
(RUT, fecha futura) y el 404 controlado.
"""
from datetime import timedelta

from django.core import mail
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.exceptions import ValidationError
from django.utils.dateparse import parse_datetime
from rest_framework.test import APITestCase

from . import services
from .models import Entrada, Evento, Notificacion, Orden, OrdenItem, Recinto, Sector, Usuario
from .validators import validar_fecha_futura, validar_rut


def crear_catalogo():
    """Crea un recinto, un evento a futuro y un sector con stock=5. Reutilizado por varios tests."""
    recinto = Recinto.objects.create(nombre="Movistar Arena", direccion="Beaucheff 1204",
                                     ciudad="Santiago", capacidad=12000)
    evento = Evento.objects.create(recinto=recinto, nombre="Rock en Vivo", artista="Los Tres",
                                   fecha=timezone.now() + timedelta(days=30))
    sector = Sector.objects.create(evento=evento, nombre="Cancha", precio=25000,
                                   capacidad_total=5, stock_disponible=5)
    return recinto, evento, sector


class AutenticacionJWTTest(APITestCase):
    """Registro, login y claim de rol en el token."""

    def test_registro_crea_espectador_por_defecto(self):
        resp = self.client.post("/api/registro/", {
            "username": "juan", "email": "juan@correo.cl", "password": "clave123",
        })
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertEqual(Usuario.objects.get(username="juan").rol, Usuario.Rol.ESPECTADOR)

    def test_login_devuelve_tokens_y_claim_de_rol(self):
        Usuario.objects.create_user(username="ana", email="ana@correo.cl",
                                    password="clave123", rol=Usuario.Rol.ORGANIZADOR)
        resp = self.client.post("/api/token/", {"username": "ana", "password": "clave123"})
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertIn("access", resp.data)
        self.assertIn("refresh", resp.data)
        self.assertEqual(resp.data["rol"], "ORGANIZADOR")

    def test_login_con_password_incorrecto_falla(self):
        Usuario.objects.create_user(username="ana", email="ana@correo.cl", password="clave123")
        resp = self.client.post("/api/token/", {"username": "ana", "password": "mala"})
        self.assertEqual(resp.status_code, status.HTTP_401_UNAUTHORIZED)


class PermisosRBACTest(APITestCase):
    """Catálogo público vs endpoints protegidos por rol."""

    def setUp(self):
        self.recinto, self.evento, self.sector = crear_catalogo()
        self.espectador = Usuario.objects.create_user(
            username="espectador", email="e@correo.cl", password="clave123", rol=Usuario.Rol.ESPECTADOR)
        self.organizador = Usuario.objects.create_user(
            username="organizador", email="o@correo.cl", password="clave123", rol=Usuario.Rol.ORGANIZADOR)

    def _login(self, username):
        resp = self.client.post("/api/token/", {"username": username, "password": "clave123"})
        return resp.data["access"]

    def test_catalogo_es_publico_sin_login(self):
        resp = self.client.get("/api/eventos/")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(len(resp.data["results"]), 1)

    def test_espectador_no_puede_crear_evento(self):
        token = self._login("espectador")
        resp = self.client.post("/api/eventos/", {
            "recinto": self.recinto.id, "nombre": "x", "artista": "y",
            "fecha": (timezone.now() + timedelta(days=10)).isoformat(),
        }, HTTP_AUTHORIZATION=f"Bearer {token}")
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_organizador_puede_crear_evento(self):
        token = self._login("organizador")
        resp = self.client.post("/api/eventos/", {
            "recinto": self.recinto.id, "nombre": "Nuevo show", "artista": "y",
            "fecha": (timezone.now() + timedelta(days=10)).isoformat(),
        }, HTTP_AUTHORIZATION=f"Bearer {token}")
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)

    def test_espectador_no_puede_ver_ventas_de_organizador(self):
        token = self._login("espectador")
        resp = self.client.get("/api/compras/", HTTP_AUTHORIZATION=f"Bearer {token}")
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_endpoint_protegido_sin_token_devuelve_401(self):
        resp = self.client.get("/api/carro-tickets/")
        self.assertEqual(resp.status_code, status.HTTP_401_UNAUTHORIZED)


class CarroPersistenteTest(APITestCase):
    """Carro 1 a 1 con el usuario: agregar, acumular cantidad, no descuenta stock."""

    def setUp(self):
        self.recinto, self.evento, self.sector = crear_catalogo()
        self.user = Usuario.objects.create_user(username="cliente", email="c@correo.cl",
                                                 password="clave123", rol=Usuario.Rol.ESPECTADOR)
        token = self.client.post("/api/token/", {"username": "cliente", "password": "clave123"}).data["access"]
        self.auth = {"HTTP_AUTHORIZATION": f"Bearer {token}"}

    def test_agregar_al_carro_no_descuenta_stock(self):
        self.client.post("/api/carro-tickets/", {"sector": self.sector.id, "cantidad": 2}, **self.auth)
        self.sector.refresh_from_db()
        self.assertEqual(self.sector.stock_disponible, 5)  # intacto

    def test_agregar_el_mismo_sector_dos_veces_acumula_cantidad(self):
        self.client.post("/api/carro-tickets/", {"sector": self.sector.id, "cantidad": 1}, **self.auth)
        self.client.post("/api/carro-tickets/", {"sector": self.sector.id, "cantidad": 2}, **self.auth)
        resp = self.client.get("/api/carro-tickets/", **self.auth)
        self.assertEqual(len(resp.data["items"]), 1)
        self.assertEqual(resp.data["items"][0]["cantidad"], 3)

    def test_carro_es_el_mismo_tras_relogin(self):
        """Persistencia post-logout: el carro vive en BD, no en sesión."""
        self.client.post("/api/carro-tickets/", {"sector": self.sector.id, "cantidad": 1}, **self.auth)
        # Simula un logout: se pide un token NUEVO (otra "sesión") para el mismo usuario.
        nuevo_token = self.client.post("/api/token/", {"username": "cliente", "password": "clave123"}).data["access"]
        resp = self.client.get("/api/carro-tickets/", HTTP_AUTHORIZATION=f"Bearer {nuevo_token}")
        self.assertEqual(len(resp.data["items"]), 1)

    def test_quitar_item_del_carro(self):
        self.client.post("/api/carro-tickets/", {"sector": self.sector.id, "cantidad": 1}, **self.auth)
        resp = self.client.delete(f"/api/carro-tickets/?sector={self.sector.id}", **self.auth)
        self.assertEqual(resp.status_code, status.HTTP_204_NO_CONTENT)


class CheckoutTransaccionalTest(APITestCase):
    """Reserva (PENDIENTE) -> pago (PAGADO) decididos por el espectador: stock, precio congelado y tickets UUID."""

    def setUp(self):
        self.recinto, self.evento, self.sector = crear_catalogo()  # stock=5, precio=25000
        self.user = Usuario.objects.create_user(username="cliente", email="c@correo.cl",
                                                 password="clave123", rol=Usuario.Rol.ESPECTADOR)
        token = self.client.post("/api/token/", {"username": "cliente", "password": "clave123"}).data["access"]
        self.auth = {"HTTP_AUTHORIZATION": f"Bearer {token}"}
        self.client.post("/api/carro-tickets/", {"sector": self.sector.id, "cantidad": 3}, **self.auth)

    def _reservar(self):
        return self.client.post("/api/mis-compras/reservar/", {}, **self.auth)

    def test_reservar_crea_orden_pendiente_sin_descontar_stock_y_congela_precio(self):
        resp = self._reservar()
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertEqual(resp.data["estado"], "PENDIENTE")
        self.assertEqual(resp.data["total"], "75000")  # 3 x 25000, congelado en la orden
        self.assertTrue(0 < resp.data["segundos_restantes"] <= 300)  # 5 minutos para pagar
        self.sector.refresh_from_db()
        self.assertEqual(self.sector.stock_disponible, 5)  # reservar NO descuenta stock

    def test_stock_se_descuenta_recien_al_pasar_a_pagado(self):
        orden = self._reservar().data
        self.sector.refresh_from_db()
        self.assertEqual(self.sector.stock_disponible, 5)  # PENDIENTE: intacto
        self.client.post(f"/api/mis-compras/{orden['id']}/pagar/", {}, **self.auth)
        self.sector.refresh_from_db()
        self.assertEqual(self.sector.stock_disponible, 2)  # PAGADO: 5 - 3
        self.assertTrue(Orden.objects.get(pk=orden["id"]).stock_descontado)

    def test_no_hay_tickets_hasta_que_el_espectador_paga(self):
        self._reservar()
        entradas = self.client.get("/api/mis-entradas/", **self.auth)
        self.assertEqual(len(entradas.data["results"]), 0)

    def test_pagar_emite_un_ticket_uuid_por_entrada(self):
        orden = self._reservar().data
        resp = self.client.post(f"/api/mis-compras/{orden['id']}/pagar/", {}, **self.auth)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data["estado"], "PAGADO")
        entradas_resp = self.client.get("/api/mis-entradas/", **self.auth)
        codigos = [e["codigo"] for e in entradas_resp.data["results"]]
        self.assertEqual(len(codigos), 3)       # una entrada por unidad comprada
        self.assertEqual(len(set(codigos)), 3)  # todos únicos

    def test_reservar_vacia_el_carro(self):
        self._reservar()
        resp = self.client.get("/api/carro-tickets/", **self.auth)
        self.assertEqual(len(resp.data["items"]), 0)

    def test_reservar_con_stock_insuficiente_es_rechazado(self):
        self.client.post("/api/carro-tickets/", {"sector": self.sector.id, "cantidad": 3}, **self.auth)  # 6 > 5
        with self.assertRaises(ValidationError):
            services.reservar_carro(self.user)
        self.sector.refresh_from_db()
        self.assertEqual(self.sector.stock_disponible, 5)

    def test_dos_pendientes_por_las_ultimas_entradas_solo_una_se_paga(self):
        """Las reservas no bloquean stock: quien paga primero se lo queda; la otra orden se cancela."""
        otro = Usuario.objects.create_user(username="otro", email="o2@correo.cl",
                                           password="clave123", rol=Usuario.Rol.ESPECTADOR)
        from .models import Carro, CarroItem
        carro_otro = Carro.objects.create(usuario=otro)
        CarroItem.objects.create(carro=carro_otro, sector=self.sector, cantidad=3)
        orden_otro = services.reservar_carro(otro)       # ambos reservan 3 de 5: permitido
        orden_yo = services.reservar_carro(self.user)
        services.pagar_orden(otro, orden_otro.pk)         # paga primero: stock 5 -> 2
        with self.assertRaises(ValidationError):          # yo pido 3 pero quedan 2
            services.pagar_orden(self.user, orden_yo.pk)
        self.sector.refresh_from_db()
        self.assertEqual(self.sector.stock_disponible, 2)
        self.assertEqual(Orden.objects.get(pk=orden_yo.pk).estado, Orden.Estado.CANCELADO)
        self.assertEqual(Entrada.objects.filter(orden_item__orden=orden_yo).count(), 0)


class CancelacionYReposicionTest(APITestCase):
    """El espectador cancela (pendiente o pagada) y el stock se repone; no se puede cancelar dos veces."""

    def setUp(self):
        self.recinto, self.evento, self.sector = crear_catalogo()
        self.user = Usuario.objects.create_user(username="cliente", email="c@correo.cl",
                                                 password="clave123", rol=Usuario.Rol.ESPECTADOR)
        self.otro = Usuario.objects.create_user(username="otro", email="o3@correo.cl",
                                                password="clave123", rol=Usuario.Rol.ESPECTADOR)
        self.organizador = Usuario.objects.create_user(
            username="organizador", email="o@correo.cl", password="clave123", rol=Usuario.Rol.ORGANIZADOR)
        tok = lambda u: self.client.post("/api/token/", {"username": u, "password": "clave123"}).data["access"]
        self.auth = {"HTTP_AUTHORIZATION": f"Bearer {tok('cliente')}"}
        self.auth_otro = {"HTTP_AUTHORIZATION": f"Bearer {tok('otro')}"}
        self.auth_org = {"HTTP_AUTHORIZATION": f"Bearer {tok('organizador')}"}
        self.client.post("/api/carro-tickets/", {"sector": self.sector.id, "cantidad": 2}, **self.auth)
        self.orden = self.client.post("/api/mis-compras/reservar/", {}, **self.auth).data

    def _stock(self):
        self.sector.refresh_from_db()
        return self.sector.stock_disponible

    def test_cancelar_orden_pendiente_repone_stock(self):
        self.assertEqual(self._stock(), 5)  # PENDIENTE no descontó nada
        resp = self.client.post(f"/api/mis-compras/{self.orden['id']}/cancelar/", {}, **self.auth)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data["estado"], "CANCELADO")
        self.assertEqual(self._stock(), 5)  # y cancelarla tampoco suma stock de más

    def test_cancelar_orden_pagada_repone_stock(self):
        self.client.post(f"/api/mis-compras/{self.orden['id']}/pagar/", {}, **self.auth)
        self.assertEqual(self._stock(), 3)  # PAGADO: 5 - 2
        resp = self.client.post(f"/api/mis-compras/{self.orden['id']}/cancelar/", {}, **self.auth)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(self._stock(), 5)

    def test_no_se_puede_cancelar_dos_veces(self):
        self.client.post(f"/api/mis-compras/{self.orden['id']}/cancelar/", {}, **self.auth)
        resp = self.client.post(f"/api/mis-compras/{self.orden['id']}/cancelar/", {}, **self.auth)
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(self._stock(), 5)  # no se repone dos veces

    def test_no_se_puede_pagar_dos_veces(self):
        self.client.post(f"/api/mis-compras/{self.orden['id']}/pagar/", {}, **self.auth)
        resp = self.client.post(f"/api/mis-compras/{self.orden['id']}/pagar/", {}, **self.auth)
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_otro_espectador_no_puede_pagar_ni_cancelar_mi_orden(self):
        for accion in ("pagar", "cancelar"):
            resp = self.client.post(f"/api/mis-compras/{self.orden['id']}/{accion}/", {}, **self.auth_otro)
            self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    def test_el_organizador_ya_no_aprueba_ni_cambia_estados(self):
        resp = self.client.patch(f"/api/compras/{self.orden['id']}/estado/", {"estado": "PAGADO"},
                                 format="json", **self.auth_org)
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)  # el endpoint ya no existe
        resp = self.client.post(f"/api/mis-compras/{self.orden['id']}/pagar/", {}, **self.auth_org)
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)  # tampoco puede pagar por otro
        self.assertEqual(self.client.get("/api/compras/", **self.auth_org).status_code, status.HTTP_200_OK)  # solo ve


class ExpiracionReservaTest(APITestCase):
    """Una orden PENDIENTE que no se paga en 5 minutos se cancela sola (nunca descontó stock)."""

    def setUp(self):
        self.recinto, self.evento, self.sector = crear_catalogo()
        self.user = Usuario.objects.create_user(username="cliente", email="c@correo.cl",
                                                 password="clave123", rol=Usuario.Rol.ESPECTADOR)
        token = self.client.post("/api/token/", {"username": "cliente", "password": "clave123"}).data["access"]
        self.auth = {"HTTP_AUTHORIZATION": f"Bearer {token}"}
        self.client.post("/api/carro-tickets/", {"sector": self.sector.id, "cantidad": 2}, **self.auth)
        self.orden = self.client.post("/api/mis-compras/reservar/", {}, **self.auth).data

    def _vencer(self, minutos=6):
        Orden.objects.filter(pk=self.orden["id"]).update(creada=timezone.now() - timedelta(minutes=minutos))

    def test_orden_vencida_se_cancela_y_repone_stock(self):
        self._vencer()
        self.assertEqual(services.expirar_pendientes(), 1)
        self.assertEqual(Orden.objects.get(pk=self.orden["id"]).estado, Orden.Estado.CANCELADO)
        self.sector.refresh_from_db()
        self.assertEqual(self.sector.stock_disponible, 5)

    def test_orden_dentro_de_los_5_minutos_no_se_toca(self):
        self._vencer(minutos=4)
        self.assertEqual(services.expirar_pendientes(), 0)
        self.assertEqual(Orden.objects.get(pk=self.orden["id"]).estado, Orden.Estado.PENDIENTE)

    def test_cualquier_llamada_a_la_api_expira_las_vencidas(self):
        self._vencer()
        self.client.get("/api/eventos/")  # el middleware limpia antes de responder
        self.assertEqual(Orden.objects.get(pk=self.orden["id"]).estado, Orden.Estado.CANCELADO)
        self.sector.refresh_from_db()
        self.assertEqual(self.sector.stock_disponible, 5)

    def test_no_se_puede_pagar_una_reserva_vencida(self):
        self._vencer()
        resp = self.client.post(f"/api/mis-compras/{self.orden['id']}/pagar/", {}, **self.auth)
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(Orden.objects.get(pk=self.orden["id"]).estado, Orden.Estado.CANCELADO)
        self.assertEqual(self.client.get("/api/mis-entradas/", **self.auth).data["count"], 0)  # sin tickets

    def test_una_orden_pagada_nunca_expira(self):
        self.client.post(f"/api/mis-compras/{self.orden['id']}/pagar/", {}, **self.auth)
        self._vencer(minutos=60)
        self.assertEqual(services.expirar_pendientes(), 0)
        self.assertEqual(Orden.objects.get(pk=self.orden["id"]).estado, Orden.Estado.PAGADO)


class ValidadoresTest(APITestCase):
    """Validadores a nivel de modelo: RUT chileno y fecha de evento futura."""

    def test_rut_valido_no_lanza_error(self):
        validar_rut("11111111-1")  # no debe lanzar excepción

    def test_rut_con_digito_verificador_incorrecto_falla(self):
        with self.assertRaises(Exception):
            validar_rut("11111111-9")

    def test_rut_con_formato_invalido_falla(self):
        with self.assertRaises(Exception):
            validar_rut("no-es-un-rut")

    def test_fecha_evento_en_el_pasado_falla(self):
        with self.assertRaises(Exception):
            validar_fecha_futura(timezone.now() - timedelta(days=1))

    def test_fecha_evento_futura_no_lanza_error(self):
        validar_fecha_futura(timezone.now() + timedelta(days=1))


class Error404Test(APITestCase):
    """El 404 debe ser el template propio, no la pantalla por defecto de Django."""

    def test_ruta_inexistente_devuelve_404_controlado(self):
        resp = self.client.get("/esta-ruta-no-existe/")
        self.assertEqual(resp.status_code, 404)
        self.assertTemplateUsed(resp, "404.html")


class DocumentacionAPITest(APITestCase):
    """Swagger/OpenAPI debe estar operativo en /api/docs/."""

    def test_swagger_responde_200(self):
        resp = self.client.get("/api/docs/")
        self.assertEqual(resp.status_code, 200)

    def test_schema_openapi_responde_200(self):
        resp = self.client.get("/api/schema/")
        self.assertEqual(resp.status_code, 200)


class CRUDEventoDuenioTest(APITestCase):
    """Solo el Organizador dueño del evento puede editarlo o eliminarlo."""

    def setUp(self):
        self.recinto, self.evento, self.sector = crear_catalogo()
        self.dueno = Usuario.objects.create_user(username="dueno", email="d@correo.cl",
                                                 password="clave123", rol=Usuario.Rol.ORGANIZADOR)
        self.otro_organizador = Usuario.objects.create_user(username="otro_org", email="oo@correo.cl",
                                                            password="clave123", rol=Usuario.Rol.ORGANIZADOR)
        self.evento.creado_por = self.dueno
        self.evento.save()
        self.token_dueno = self.client.post("/api/token/", {"username": "dueno", "password": "clave123"}).data["access"]
        self.token_otro = self.client.post("/api/token/", {"username": "otro_org", "password": "clave123"}).data["access"]

    def test_crear_evento_asigna_al_creador_como_dueno(self):
        resp = self.client.post("/api/eventos/", {
            "recinto": self.recinto.id, "nombre": "Nuevo show", "artista": "y",
            "fecha": (timezone.now() + timedelta(days=10)).isoformat(),
        }, HTTP_AUTHORIZATION=f"Bearer {self.token_dueno}")
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertEqual(resp.data["creado_por"], self.dueno.id)

    def test_dueno_puede_editar_su_evento(self):
        resp = self.client.patch(f"/api/eventos/{self.evento.id}/", {"nombre": "Nombre editado"},
                                 HTTP_AUTHORIZATION=f"Bearer {self.token_dueno}", format="json")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data["nombre"], "Nombre editado")

    def test_otro_organizador_no_puede_editar_evento_ajeno(self):
        resp = self.client.patch(f"/api/eventos/{self.evento.id}/", {"nombre": "Hackeado"},
                                 HTTP_AUTHORIZATION=f"Bearer {self.token_otro}", format="json")
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)
        self.evento.refresh_from_db()
        self.assertNotEqual(self.evento.nombre, "Hackeado")  # no se modificó nada

    def test_otro_organizador_no_puede_eliminar_evento_ajeno(self):
        resp = self.client.delete(f"/api/eventos/{self.evento.id}/", HTTP_AUTHORIZATION=f"Bearer {self.token_otro}")
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)
        self.assertTrue(Evento.objects.filter(pk=self.evento.id).exists())

    def test_dueno_puede_eliminar_su_evento(self):
        resp = self.client.delete(f"/api/eventos/{self.evento.id}/", HTTP_AUTHORIZATION=f"Bearer {self.token_dueno}")
        self.assertEqual(resp.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(Evento.objects.filter(pk=self.evento.id).exists())

    def test_espectador_no_puede_editar_ningun_evento(self):
        espectador = Usuario.objects.create_user(username="espectador2", email="e2@correo.cl",
                                                  password="clave123", rol=Usuario.Rol.ESPECTADOR)
        token = self.client.post("/api/token/", {"username": "espectador2", "password": "clave123"}).data["access"]
        resp = self.client.patch(f"/api/eventos/{self.evento.id}/", {"nombre": "x"},
                                 HTTP_AUTHORIZATION=f"Bearer {token}", format="json")
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)



class SectorCRUDOrganizadorTest(APITestCase):
    """El organizador dueño administra las zonas (precio y cantidad de entradas) de su evento."""

    def setUp(self):
        self.recinto, self.evento, self.sector = crear_catalogo()
        self.dueno = Usuario.objects.create_user(username="dueno_z", email="dz@correo.cl",
                                                 password="clave123", rol=Usuario.Rol.ORGANIZADOR)
        self.otro = Usuario.objects.create_user(username="otro_z", email="oz@correo.cl",
                                                password="clave123", rol=Usuario.Rol.ORGANIZADOR)
        self.evento.creado_por = self.dueno
        self.evento.save()
        tok = lambda u: self.client.post("/api/token/", {"username": u, "password": "clave123"}).data["access"]
        self.auth_dueno = f"Bearer {tok('dueno_z')}"
        self.auth_otro = f"Bearer {tok('otro_z')}"

    def test_dueno_crea_zona_con_stock_igual_a_capacidad(self):
        resp = self.client.post("/api/sectores/", {"evento": self.evento.id, "nombre": "Palco",
                                                    "precio": 90000, "capacidad_total": 100},
                                HTTP_AUTHORIZATION=self.auth_dueno, format="json")
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertEqual(resp.data["stock_disponible"], 100)

    def test_otro_organizador_no_puede_crear_zona_en_evento_ajeno(self):
        resp = self.client.post("/api/sectores/", {"evento": self.evento.id, "nombre": "Palco",
                                                    "precio": 90000, "capacidad_total": 100},
                                HTTP_AUTHORIZATION=self.auth_otro, format="json")
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_otro_organizador_no_puede_editar_zona_ajena(self):
        resp = self.client.patch(f"/api/sectores/{self.sector.id}/", {"precio": 1},
                                 HTTP_AUTHORIZATION=self.auth_otro, format="json")
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_cambiar_capacidad_recalcula_stock_y_respeta_lo_vendido(self):
        Sector.objects.filter(pk=self.sector.pk).update(stock_disponible=3)  # 2 vendidas de 5
        resp = self.client.patch(f"/api/sectores/{self.sector.id}/", {"capacidad_total": 10},
                                 HTTP_AUTHORIZATION=self.auth_dueno, format="json")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data["stock_disponible"], 8)
        resp = self.client.patch(f"/api/sectores/{self.sector.id}/", {"capacidad_total": 1},
                                 HTTP_AUTHORIZATION=self.auth_dueno, format="json")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_no_se_puede_eliminar_zona_ni_evento_con_ventas(self):
        orden = Orden.objects.create(usuario=self.dueno, estado=Orden.Estado.PAGADO, total=25000)
        OrdenItem.objects.create(orden=orden, sector=self.sector, cantidad=1, precio_unitario=25000)
        resp = self.client.delete(f"/api/sectores/{self.sector.id}/", HTTP_AUTHORIZATION=self.auth_dueno)
        self.assertEqual(resp.status_code, status.HTTP_409_CONFLICT)
        resp = self.client.delete(f"/api/eventos/{self.evento.id}/", HTTP_AUTHORIZATION=self.auth_dueno)
        self.assertEqual(resp.status_code, status.HTTP_409_CONFLICT)
        self.assertTrue(Evento.objects.filter(pk=self.evento.id).exists())

    def test_dueno_elimina_zona_sin_ventas(self):
        resp = self.client.delete(f"/api/sectores/{self.sector.id}/", HTTP_AUTHORIZATION=self.auth_dueno)
        self.assertEqual(resp.status_code, status.HTTP_204_NO_CONTENT)


class NotificacionCambioEventoTest(APITestCase):
    """Si el organizador cambia la fecha (o el recinto), los compradores son avisados y su ticket se actualiza."""

    def setUp(self):
        self.recinto, self.evento, self.sector = crear_catalogo()
        self.dueno = Usuario.objects.create_user(username="org_n", email="on@correo.cl",
                                                 password="clave123", rol=Usuario.Rol.ORGANIZADOR)
        self.comprador = Usuario.objects.create_user(username="comprador_n", email="cn@correo.cl",
                                                     password="clave123", rol=Usuario.Rol.ESPECTADOR)
        self.evento.creado_por = self.dueno
        self.evento.save()
        self.orden = Orden.objects.create(usuario=self.comprador, estado=Orden.Estado.PAGADO,
                                          total=25000, stock_descontado=True)
        oi = OrdenItem.objects.create(orden=self.orden, sector=self.sector, cantidad=1, precio_unitario=25000)
        Entrada.objects.create(orden_item=oi)
        Sector.objects.filter(pk=self.sector.pk).update(stock_disponible=4)
        tok = lambda u: self.client.post("/api/token/", {"username": u, "password": "clave123"}).data["access"]
        self.auth_dueno = f"Bearer {tok('org_n')}"
        self.auth_comprador = f"Bearer {tok('comprador_n')}"

    def _cambiar_fecha(self):
        nueva = timezone.now() + timedelta(days=45)
        return self.client.patch(f"/api/eventos/{self.evento.id}/", {"fecha": nueva.isoformat()},
                                 HTTP_AUTHORIZATION=self.auth_dueno, format="json")

    def test_cambiar_fecha_notifica_al_comprador_y_el_ticket_muestra_la_nueva(self):
        resp = self._cambiar_fecha()
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data["notificados"], 1)
        self.assertEqual(Notificacion.objects.filter(usuario=self.comprador).count(), 1)
        self.assertEqual(len(mail.outbox), 1)
        ticket = self.client.get("/api/mis-entradas/", HTTP_AUTHORIZATION=self.auth_comprador).data["results"][0]
        self.evento.refresh_from_db()
        self.assertEqual(parse_datetime(ticket["fecha"]), self.evento.fecha)

    def test_editar_otro_campo_no_notifica(self):
        resp = self.client.patch(f"/api/eventos/{self.evento.id}/", {"nombre": "Otro nombre"},
                                 HTTP_AUTHORIZATION=self.auth_dueno, format="json")
        self.assertEqual(resp.data["notificados"], 0)
        self.assertEqual(Notificacion.objects.count(), 0)

    def test_compras_canceladas_no_reciben_aviso(self):
        self.orden.estado = Orden.Estado.CANCELADO
        self.orden.save()
        self.assertEqual(self._cambiar_fecha().data["notificados"], 0)

    def test_eliminar_evento_con_entradas_vendidas_responde_409_con_mensaje(self):
        resp = self.client.delete(f"/api/eventos/{self.evento.id}/", HTTP_AUTHORIZATION=self.auth_dueno)
        self.assertEqual(resp.status_code, status.HTTP_409_CONFLICT)
        self.assertIn("entrada", resp.data["detail"])
        self.assertTrue(Evento.objects.filter(pk=self.evento.id).exists())

    def test_comprador_ve_y_marca_leidas_sus_notificaciones(self):
        self._cambiar_fecha()
        d = self.client.get("/api/notificaciones/", HTTP_AUTHORIZATION=self.auth_comprador).data
        self.assertEqual(d["no_leidas"], 1)
        self.client.post("/api/notificaciones/", HTTP_AUTHORIZATION=self.auth_comprador)
        d = self.client.get("/api/notificaciones/", HTTP_AUTHORIZATION=self.auth_comprador).data
        self.assertEqual(d["no_leidas"], 0)
