"""
Management command: python manage.py poblar_datos
Crea datos de prueba (usuarios, recintos, eventos) para evitar problemas de demostración.
"""
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from entradas.models import Evento, Recinto, Sector, Usuario


class Command(BaseCommand):
    help = "Crea datos de prueba: usuarios, recintos, eventos y sectores."

    def handle(self, *args, **kwargs):
        if not Usuario.objects.filter(username="admin").exists():
            Usuario.objects.create_superuser("admin", "admin@ticketera.cl", "admin1234")
            self.stdout.write("Superusuario 'admin' / 'admin1234' creado.")

        organizador, creado1 = Usuario.objects.get_or_create(
            username="organizador1", defaults={"email": "organizador1@ticketera.cl", "rol": Usuario.Rol.ORGANIZADOR})
        if creado1:
            organizador.set_password("organizador123")
            organizador.save()

        # Segundo organizador: sirve para demostrar que solo el dueño puede editar/eliminar su evento.
        organizador2, creado_o2 = Usuario.objects.get_or_create(
            username="organizador2", defaults={"email": "organizador2@ticketera.cl", "rol": Usuario.Rol.ORGANIZADOR})
        if creado_o2:
            organizador2.set_password("organizador123")
            organizador2.save()

        espectador, creado2 = Usuario.objects.get_or_create(
            username="espectador1", defaults={"email": "espectador1@ticketera.cl", "rol": Usuario.Rol.ESPECTADOR, "rut": "111111111"})
        if creado2:
            espectador.set_password("espectador123")
            espectador.save()

        r1, _ = Recinto.objects.get_or_create(nombre="Movistar Arena",
                                              defaults={"direccion": "Av. Beaucheff 1204", "ciudad": "Santiago", "capacidad": 12000})
        r2, _ = Recinto.objects.get_or_create(nombre="Parque O'Higgins",
                                              defaults={"direccion": "Blanco Encalada", "ciudad": "Santiago", "capacidad": 25000})

        ahora = timezone.now()
        e1, _ = Evento.objects.get_or_create(
            recinto=r1, nombre="Noche de Rock Nacional", artista="Los Tres",
            defaults={"descripcion": "Un clásico del rock chileno en vivo.", "fecha": ahora + timedelta(days=30),
                      "creado_por": organizador})
        e2, _ = Evento.objects.get_or_create(
            recinto=r2, nombre="Festival Sur 2026", artista="Varios artistas",
            defaults={"descripcion": "Dos días de música en vivo al aire libre.", "fecha": ahora + timedelta(days=60),
                      "creado_por": organizador2})

        for evento, sectores in [
            (e1, [("Cancha General", 25000, 3000), ("VIP", 55000, 500)]),
            (e2, [("Tribuna", 18000, 5000), ("Cancha", 30000, 8000), ("Palco", 90000, 100)]),
        ]:
            for nombre, precio, cap in sectores:
                Sector.objects.get_or_create(evento=evento, nombre=nombre,
                                             defaults={"precio": precio, "capacidad_total": cap, "stock_disponible": cap})

        self.stdout.write(self.style.SUCCESS(
            "Datos de prueba listos.\n"
            "  admin / admin1234 (superusuario, solo para crear datos, no tiene panel propio)\n"
            "  organizador1 / organizador123 (dueño de 'Noche de Rock Nacional')\n"
            "  organizador2 / organizador123 (dueño de 'Festival Sur 2026')\n"
            "  espectador1 / espectador123 (Espectador)"))
