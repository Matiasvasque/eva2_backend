"""
Lógica transaccional de compras.

Flujo (decide el espectador, el organizador no aprueba nada):
  1. reservar_carro  -> Orden PENDIENTE: verifica que haya stock y congela los precios,
                        pero NO descuenta stock (ni agregar al carro ni reservar lo tocan).
  2. pagar_orden     -> PENDIENTE -> PAGADO: en ese instante exacto, y dentro de la misma transacción,
                        se bloquea el sector, se revalida y se descuenta el stock y se emite una Entrada (UUID).
     cancelar_orden  -> PENDIENTE -> CANCELADO (no hay stock que reponer) o
                        PAGADO -> CANCELADO (repone el stock descontado al pagar).
  3. Si una orden PENDIENTE no se paga en ORDEN_MINUTOS_EXPIRACION (5 min) se cancela sola
     (expirar_pendientes); como nunca descontó stock, no hay nada que reponer.
Todo se ejecuta dentro de transaction.atomic(): si algo falla, se revierte completo.
"""
from datetime import timedelta

from django.conf import settings
from django.core.mail import send_mass_mail
from django.db import transaction
from django.db.models import F, Sum
from django.utils import timezone
from rest_framework.exceptions import NotFound, ValidationError

from .models import Carro, Entrada, Notificacion, Orden, OrdenItem, Sector, Usuario

E = Orden.Estado


def minutos_reserva():
    return getattr(settings, "ORDEN_MINUTOS_EXPIRACION", 5)


def limite_expiracion():
    """Órdenes PENDIENTES creadas antes de este instante ya vencieron."""
    return timezone.now() - timedelta(minutes=minutos_reserva())


def segundos_restantes(orden):
    """Segundos que le quedan a una orden PENDIENTE para pagarse (None si no está pendiente)."""
    if orden.estado != E.PENDIENTE:
        return None
    vence = orden.creada + timedelta(minutes=minutos_reserva())
    return max(0, int((vence - timezone.now()).total_seconds()))


def entradas_comprometidas(evento):
    """Entradas vendidas o reservadas (órdenes no canceladas) de un evento: impiden eliminarlo."""
    return OrdenItem.objects.filter(sector__evento=evento) \
        .exclude(orden__estado=E.CANCELADO).aggregate(n=Sum("cantidad"))["n"] or 0


def _bloquear_sectores(ids):
    """SELECT ... FOR UPDATE ordenado por id (evita carreras y deadlocks)."""
    return {s.pk: s for s in Sector.objects.select_for_update().select_related("evento").filter(pk__in=ids).order_by("pk")}


def _validar_disponibilidad(items, sectores):
    """Verifica evento vigente y stock suficiente por sector. NO modifica el stock."""
    for it in items:
        s = sectores[it.sector_id]
        if s.evento.fecha <= timezone.now():
            raise ValidationError(f"El evento '{s.evento.nombre}' ya ocurrió.")
        if not s.evento.activo:
            raise ValidationError(f"El evento '{s.evento.nombre}' no está disponible.")
        if s.stock_disponible < it.cantidad:
            raise ValidationError(
                f"Stock insuficiente en '{s}': quedan {s.stock_disponible}, pediste {it.cantidad}.")


def _descontar_stock(items, sectores):
    """Valida y descuenta el stock de forma atómica (F expression). Solo se llama al pasar a PAGADO."""
    _validar_disponibilidad(items, sectores)
    for it in items:
        Sector.objects.filter(pk=it.sector_id).update(stock_disponible=F("stock_disponible") - it.cantidad)


def _cancelar(orden):
    """Pasa la orden a CANCELADO. Repone stock solo si ya se había descontado (orden pagada).
    Una orden PENDIENTE nunca descontó stock. La orden debe venir bloqueada."""
    if orden.stock_descontado:
        items = list(orden.items.all())
        _bloquear_sectores([i.sector_id for i in items])
        for it in items:
            Sector.objects.filter(pk=it.sector_id).update(stock_disponible=F("stock_disponible") + it.cantidad)
        orden.stock_descontado = False
    orden.estado = E.CANCELADO
    orden.save()


def _orden_del_usuario(usuario, orden_id):
    orden = Orden.objects.select_for_update().filter(pk=orden_id, usuario=usuario).first()
    if orden is None:
        raise NotFound("Orden no encontrada.")
    return orden


@transaction.atomic
def reservar_carro(usuario):
    """
    Paso 1: verifica que haya stock (sin descontarlo) -> crea la Orden PENDIENTE con precios congelados ->
    vacía el carro. El stock se descuenta y las entradas (tickets) se emiten recién al pagar.
    """
    try:
        carro = Carro.objects.select_for_update().get(usuario=usuario)
    except Carro.DoesNotExist:
        raise ValidationError("No tienes carro todavía.")
    items = list(carro.items.select_related("sector"))
    if not items:
        raise ValidationError("El carro está vacío.")
    sectores = _bloquear_sectores([i.sector_id for i in items])
    _validar_disponibilidad(items, sectores)  # solo verifica: el stock NO se toca hasta el pago

    orden = Orden.objects.create(usuario=usuario, estado=E.PENDIENTE, stock_descontado=False)
    total = 0
    for it in items:
        precio = sectores[it.sector_id].precio
        OrdenItem.objects.create(orden=orden, sector_id=it.sector_id, cantidad=it.cantidad, precio_unitario=precio)
        total += precio * it.cantidad
    orden.total = total
    orden.save(update_fields=["total"])
    carro.items.all().delete()
    return orden


def pagar_orden(usuario, orden_id):
    """
    Paso 2a: el espectador confirma el pago. PENDIENTE -> PAGADO.
    Es el ÚNICO momento en que se descuenta el stock: se bloquean los sectores (SELECT ... FOR UPDATE),
    se revalida la disponibilidad, se descuenta, se marca PAGADO y se emite un ticket UUID por entrada,
    todo en una sola transacción.
    """
    error = None
    with transaction.atomic():
        orden = _orden_del_usuario(usuario, orden_id)
        if orden.estado != E.PENDIENTE:
            raise ValidationError("Solo se pueden pagar órdenes pendientes.")
        if orden.creada < limite_expiracion():
            _cancelar(orden)  # venció justo ahora
            error = (f"La reserva venció (pasaron más de {minutos_reserva()} minutos) y se canceló. "
                     "Vuelve a agregar las entradas al carro.")
        else:
            items = list(orden.items.select_related("sector"))
            sectores = _bloquear_sectores([i.sector_id for i in items])
            try:
                _descontar_stock(items, sectores)
            except ValidationError as exc:
                # Otro espectador pagó antes y ya no alcanza el stock: la orden se cancela.
                _cancelar(orden)
                error = f"No se pudo completar el pago y la orden se canceló. {' '.join(map(str, exc.detail))}"
            else:
                orden.estado = E.PAGADO
                orden.stock_descontado = True
                orden.save()
                for oi in items:
                    Entrada.objects.bulk_create([Entrada(orden_item=oi) for _ in range(oi.cantidad)])  # UUID único c/u
    if error:
        raise ValidationError(error)
    return orden


@transaction.atomic
def cancelar_orden(usuario, orden_id):
    """Paso 2b: el espectador cancela. Sirve para una orden PENDIENTE o PAGADA (si el evento aún no ocurrió)."""
    orden = _orden_del_usuario(usuario, orden_id)
    if orden.estado == E.CANCELADO:
        raise ValidationError("La orden ya está cancelada.")
    if orden.estado not in (E.PENDIENTE, E.PAGADO):
        raise ValidationError(f"No se puede cancelar una orden en estado {orden.estado}.")
    if orden.estado == E.PAGADO and orden.items.filter(sector__evento__fecha__lte=timezone.now()).exists():
        raise ValidationError("No puedes cancelar entradas de un evento que ya ocurrió.")
    _cancelar(orden)
    return orden


def expirar_pendientes():
    """Cancela las órdenes PENDIENTES que superaron el tiempo de reserva y repone su stock. Devuelve cuántas."""
    limite = limite_expiracion()
    ids = list(Orden.objects.filter(estado=E.PENDIENTE, creada__lt=limite).values_list("pk", flat=True))
    canceladas = 0
    for pk in ids:
        with transaction.atomic():
            orden = Orden.objects.select_for_update().filter(pk=pk, estado=E.PENDIENTE, creada__lt=limite).first()
            if orden:  # si se pagó justo antes, ya no está PENDIENTE y se respeta
                _cancelar(orden)
                canceladas += 1
    return canceladas


# ───────────── Notificaciones al cambiar un evento ─────────────
def _fmt(dt):
    return timezone.localtime(dt).strftime("%d/%m/%Y a las %H:%M")


def compradores_del_evento(evento):
    """Usuarios con entradas vigentes (orden PAGADA o ENTREGADA) para este evento."""
    return Usuario.objects.filter(
        ordenes__estado__in=[Orden.Estado.PAGADO, Orden.Estado.ENTREGADO],
        ordenes__items__sector__evento=evento,
    ).distinct()


def notificar_cambio_evento(evento, fecha_antes, recinto_antes):
    """
    Si cambió la fecha y/o el recinto, avisa a cada comprador: crea una Notificacion (se ve en
    'Mis entradas') y envía un email. Los tickets leen la fecha del evento, por lo que ya
    muestran el dato nuevo. Devuelve cuántos compradores fueron avisados.
    """
    cambio_fecha = (evento.fecha.replace(second=0, microsecond=0) != fecha_antes.replace(second=0, microsecond=0))
    cambio_recinto = evento.recinto_id != recinto_antes.id
    if not (cambio_fecha or cambio_recinto):
        return 0
    lineas = []
    if cambio_fecha:
        lineas.append(f"Fecha: antes {_fmt(fecha_antes)}, ahora {_fmt(evento.fecha)}.")
    if cambio_recinto:
        lineas.append(f"Recinto: antes {recinto_antes.nombre} ({recinto_antes.ciudad}), "
                      f"ahora {evento.recinto.nombre} ({evento.recinto.ciudad}).")
    titulo = (f"Cambió la fecha de «{evento.nombre}»" if cambio_fecha and not cambio_recinto
              else f"Cambió el recinto de «{evento.nombre}»" if cambio_recinto and not cambio_fecha
              else f"Cambios en «{evento.nombre}»: fecha y recinto")
    mensaje = ("El organizador actualizó tu evento. " + " ".join(lineas) +
               " Tus entradas ya muestran los datos nuevos; no necesitas hacer nada.")
    usuarios = list(compradores_del_evento(evento))
    Notificacion.objects.bulk_create([
        Notificacion(usuario=u, evento=evento, titulo=titulo, mensaje=mensaje) for u in usuarios])
    correos = [(titulo, mensaje + "\n\n— Ticketera", None, [u.email]) for u in usuarios if u.email]
    if correos:
        send_mass_mail(correos, fail_silently=True)
    return len(usuarios)
