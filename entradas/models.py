"""
Modelos del sistema de venta de entradas (Proyecto 3: una sola plataforma,
sin multi-tienda, tal como lo define la rúbrica de EVA-2).

Jerarquía:
  Usuario ── Recinto
          └─ Evento ── Sector (precio y stock)
  Usuario ─┬─ Carro (1 a 1, persistente)       ── CarroItem
           └─ Orden (histórico)                ── OrdenItem ── Entrada (UUID único)
"""
import uuid

from django.contrib.auth.models import AbstractUser
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import F, Q

from .validators import (
    normalizar_rut, validar_cantidad_positiva, validar_email,
    validar_fecha_futura, validar_precio, validar_rut,
)


# ─────────────────────────── USUARIOS ───────────────────────────
class Usuario(AbstractUser):
    """
    Usuario con rol (viaja como claim en el JWT).
    ESPECTADOR compra entradas; ORGANIZADOR administra recintos, eventos y ventas.
    """
    class Rol(models.TextChoices):
        ESPECTADOR = "ESPECTADOR", "Espectador"
        ORGANIZADOR = "ORGANIZADOR", "Organizador de Eventos"

    email = models.EmailField(unique=True, validators=[validar_email])
    rut = models.CharField(max_length=12, unique=True, null=True, blank=True, validators=[validar_rut])
    rol = models.CharField(max_length=15, choices=Rol.choices, default=Rol.ESPECTADOR)

    def save(self, *args, **kwargs):
        if self.rut:
            self.rut = normalizar_rut(self.rut)  # se guarda sin puntos
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.username} ({self.get_rol_display()})"


# ─────────────────────────── CATÁLOGO ───────────────────────────
class Recinto(models.Model):
    """Lugar físico donde se realizan los eventos."""
    nombre = models.CharField(max_length=120)
    direccion = models.CharField(max_length=200)
    ciudad = models.CharField(max_length=80)
    capacidad = models.PositiveIntegerField(validators=[validar_cantidad_positiva])

    class Meta:
        ordering = ["nombre"]

    def __str__(self):
        return f"{self.nombre} - {self.ciudad}"


class Evento(models.Model):
    """Evento realizado en un recinto. 'creado_por' define al dueño para el CRUD restringido."""
    recinto = models.ForeignKey(Recinto, on_delete=models.PROTECT, related_name="eventos")
    creado_por = models.ForeignKey("Usuario", on_delete=models.SET_NULL, null=True, blank=True,
                                   related_name="eventos_creados")
    nombre = models.CharField(max_length=150)
    artista = models.CharField(max_length=300)  # uno o varios artistas (lineup), separados por coma
    descripcion = models.TextField(blank=True)
    imagen = models.ImageField(upload_to="eventos/", blank=True, null=True)
    fecha = models.DateTimeField(validators=[validar_fecha_futura])
    activo = models.BooleanField(default=True)

    class Meta:
        ordering = ["fecha"]

    def __str__(self):
        return f"{self.nombre} - {self.artista}"


class Sector(models.Model):
    """Localidad de un evento (Cancha, VIP, Tribuna) con precio y stock propios."""
    evento = models.ForeignKey(Evento, on_delete=models.CASCADE, related_name="sectores")
    nombre = models.CharField(max_length=60)
    precio = models.DecimalField(max_digits=10, decimal_places=0, validators=[validar_precio])
    capacidad_total = models.PositiveIntegerField(validators=[validar_cantidad_positiva])
    stock_disponible = models.PositiveIntegerField()  # se descuenta SOLO al pagar

    class Meta:
        ordering = ["evento", "precio"]
        constraints = [
            models.UniqueConstraint(fields=["evento", "nombre"], name="sector_unico_por_evento"),
            # Integridad en BD: el stock nunca supera la capacidad.
            models.CheckConstraint(condition=Q(stock_disponible__lte=F("capacidad_total")),
                                   name="stock_no_supera_capacidad"),
        ]

    def __str__(self):
        return f"{self.evento.nombre} - {self.nombre}"


# ─────────────────────────── CARRO PERSISTENTE ───────────────────────────
class Carro(models.Model):
    """
    Reserva temporal de entradas. Vive en PostgreSQL, por eso sobrevive al
    logout y al cambio de dispositivo. Relación 1 a 1 con el Usuario.
    """
    usuario = models.OneToOneField(Usuario, on_delete=models.CASCADE, related_name="carro")
    actualizado = models.DateTimeField(auto_now=True)

    @property
    def total(self):
        return sum(i.subtotal for i in self.items.select_related("sector"))

    def __str__(self):
        return f"Carro de {self.usuario.username}"


class CarroItem(models.Model):
    """Ítem del carro. NO descuenta stock; un sector no se repite en el mismo carro."""
    carro = models.ForeignKey(Carro, on_delete=models.CASCADE, related_name="items")
    sector = models.ForeignKey(Sector, on_delete=models.CASCADE)
    cantidad = models.PositiveIntegerField(validators=[validar_cantidad_positiva])

    class Meta:
        constraints = [models.UniqueConstraint(fields=["carro", "sector"], name="sector_unico_en_carro")]

    @property
    def subtotal(self):
        return self.sector.precio * self.cantidad

    def __str__(self):
        return f"{self.cantidad} x {self.sector}"


# ─────────────────────────── ÓRDENES Y ENTRADAS ───────────────────────────
class Orden(models.Model):
    """Registro histórico de compra generado en el checkout."""
    class Estado(models.TextChoices):
        PENDIENTE = "PENDIENTE", "Pendiente"
        PAGADO = "PAGADO", "Pagado"
        CANCELADO = "CANCELADO", "Cancelado"
        ENTREGADO = "ENTREGADO", "Entregado"

    usuario = models.ForeignKey(Usuario, on_delete=models.PROTECT, related_name="ordenes")
    estado = models.CharField(max_length=10, choices=Estado.choices, default=Estado.PENDIENTE)
    total = models.DecimalField(max_digits=12, decimal_places=0, default=0)
    # Solo se repone stock al cancelar si antes se descontó (evita doble reposición).
    stock_descontado = models.BooleanField(default=False)
    creada = models.DateTimeField(auto_now_add=True)
    actualizada = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-creada"]

    def __str__(self):
        return f"Orden #{self.pk} - {self.estado}"


class OrdenItem(models.Model):
    """Detalle de la orden. Congela el precio al momento de la compra (snapshot histórico)."""
    orden = models.ForeignKey(Orden, on_delete=models.CASCADE, related_name="items")
    sector = models.ForeignKey(Sector, on_delete=models.PROTECT, related_name="ventas")
    cantidad = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    precio_unitario = models.DecimalField(max_digits=10, decimal_places=0)

    @property
    def subtotal(self):
        return self.precio_unitario * self.cantidad


class Entrada(models.Model):
    """Ticket individual emitido al pagar, con código UUID único."""
    orden_item = models.ForeignKey(OrdenItem, on_delete=models.CASCADE, related_name="entradas")
    codigo = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    emitida = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return str(self.codigo)


# ─────────────────────────── NOTIFICACIONES ───────────────────────────
class Notificacion(models.Model):
    """Aviso para un comprador (ej.: el organizador cambió la fecha o el recinto de su evento)."""
    usuario = models.ForeignKey(Usuario, on_delete=models.CASCADE, related_name="notificaciones")
    evento = models.ForeignKey(Evento, on_delete=models.SET_NULL, null=True, blank=True, related_name="notificaciones")
    titulo = models.CharField(max_length=160)
    mensaje = models.TextField()
    leida = models.BooleanField(default=False)
    creada = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-creada"]

    def __str__(self):
        return f"{self.usuario.username}: {self.titulo}"
