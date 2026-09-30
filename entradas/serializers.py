"""Serializadores DRF y payload JWT con rol."""
from django.db import transaction
from rest_framework import serializers
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer

from . import services
from .models import (Carro, CarroItem, Entrada, Evento, Notificacion, Orden, OrdenItem,
                     Recinto, Sector, Usuario)


# ── JWT con claims personalizados ──
class TokenConRolSerializer(TokenObtainPairSerializer):
    """El payload del access/refresh incluye el rol y el username."""

    @classmethod
    def get_token(cls, user):
        token = super().get_token(user)
        token["rol"] = user.rol
        token["username"] = user.username
        return token

    def validate(self, attrs):
        data = super().validate(attrs)
        data["rol"] = self.user.rol
        return data


# ── Usuarios ──
class RegistroSerializer(serializers.ModelSerializer):
    """Registro público. El rol (Espectador u Organizador) lo elige el usuario al crear su cuenta."""
    password = serializers.CharField(write_only=True, min_length=6)
    rol = serializers.ChoiceField(choices=Usuario.Rol.choices, default=Usuario.Rol.ESPECTADOR)

    class Meta:
        model = Usuario
        fields = ["username", "email", "password", "rut", "first_name", "last_name", "rol"]

    def create(self, data):
        return Usuario.objects.create_user(**data)


# ── Catálogo ──
class RecintoSerializer(serializers.ModelSerializer):
    class Meta:
        model = Recinto
        fields = ["id", "nombre", "direccion", "ciudad", "capacidad"]


class SectorSerializer(serializers.ModelSerializer):
    evento_nombre = serializers.CharField(source="evento.nombre", read_only=True)

    class Meta:
        model = Sector
        fields = ["id", "evento", "evento_nombre", "nombre", "precio", "capacidad_total", "stock_disponible"]
        read_only_fields = ["stock_disponible"]

    def update(self, instance, validated_data):
        """
        Al editar la capacidad se recalcula el stock: disponibles = nueva capacidad - vendidas.
        No se permite bajar la capacidad por debajo de lo ya vendido.
        """
        with transaction.atomic():
            actual = Sector.objects.select_for_update().get(pk=instance.pk)
            nueva = validated_data.get("capacidad_total", actual.capacidad_total)
            vendidas = actual.capacidad_total - actual.stock_disponible
            if nueva < vendidas:
                raise serializers.ValidationError({
                    "capacidad_total": f"Ya hay {vendidas} entradas vendidas en esta zona; la capacidad no puede ser menor."})
            validated_data["stock_disponible"] = nueva - vendidas
            return super().update(actual, validated_data)


class EventoSerializer(serializers.ModelSerializer):
    sectores = SectorSerializer(many=True, read_only=True)
    recinto_nombre = serializers.CharField(source="recinto.nombre", read_only=True)
    ciudad = serializers.CharField(source="recinto.ciudad", read_only=True)
    creado_por_username = serializers.CharField(source="creado_por.username", read_only=True, default=None)
    entradas_vendidas = serializers.SerializerMethodField()

    class Meta:
        model = Evento
        fields = ["id", "nombre", "artista", "descripcion", "imagen", "fecha", "activo", "recinto",
                  "recinto_nombre", "ciudad", "sectores", "creado_por", "creado_por_username", "entradas_vendidas"]
        read_only_fields = ["creado_por"]

    def get_entradas_vendidas(self, obj):
        """Entradas vendidas o reservadas (órdenes no canceladas): las que impiden eliminar el evento."""
        return services.entradas_comprometidas(obj)


# ── Carro ──
class CarroItemSerializer(serializers.ModelSerializer):
    sector_nombre = serializers.CharField(source="sector.nombre", read_only=True)
    evento = serializers.CharField(source="sector.evento.nombre", read_only=True)
    precio = serializers.DecimalField(source="sector.precio", max_digits=10, decimal_places=0, read_only=True)
    subtotal = serializers.DecimalField(max_digits=12, decimal_places=0, read_only=True)

    class Meta:
        model = CarroItem
        fields = ["id", "sector", "sector_nombre", "evento", "precio", "cantidad", "subtotal"]


class CarroSerializer(serializers.ModelSerializer):
    items = CarroItemSerializer(many=True, read_only=True)
    total = serializers.DecimalField(max_digits=12, decimal_places=0, read_only=True)

    class Meta:
        model = Carro
        fields = ["id", "items", "total"]


class AgregarAlCarroSerializer(serializers.Serializer):
    sector = serializers.PrimaryKeyRelatedField(queryset=Sector.objects.select_related("evento"))
    cantidad = serializers.IntegerField(min_value=1, max_value=10)


# ── Órdenes y entradas ──
class EntradaSerializer(serializers.ModelSerializer):
    evento_id = serializers.IntegerField(source="orden_item.sector.evento_id", read_only=True)
    evento = serializers.CharField(source="orden_item.sector.evento.nombre", read_only=True)
    fecha = serializers.DateTimeField(source="orden_item.sector.evento.fecha", read_only=True)  # siempre la fecha vigente
    recinto = serializers.CharField(source="orden_item.sector.evento.recinto.nombre", read_only=True)
    ciudad = serializers.CharField(source="orden_item.sector.evento.recinto.ciudad", read_only=True)
    sector = serializers.CharField(source="orden_item.sector.nombre", read_only=True)
    estado_orden = serializers.CharField(source="orden_item.orden.estado", read_only=True)

    class Meta:
        model = Entrada
        fields = ["codigo", "evento_id", "evento", "fecha", "recinto", "ciudad", "sector", "estado_orden"]


class NotificacionSerializer(serializers.ModelSerializer):
    class Meta:
        model = Notificacion
        fields = ["id", "evento", "titulo", "mensaje", "leida", "creada"]


class OrdenItemSerializer(serializers.ModelSerializer):
    sector_nombre = serializers.CharField(source="sector.nombre", read_only=True)
    evento = serializers.CharField(source="sector.evento.nombre", read_only=True)
    fecha = serializers.DateTimeField(source="sector.evento.fecha", read_only=True)
    subtotal = serializers.DecimalField(max_digits=12, decimal_places=0, read_only=True)
    codigos = serializers.SerializerMethodField()

    class Meta:
        model = OrdenItem
        fields = ["evento", "fecha", "sector_nombre", "cantidad", "precio_unitario", "subtotal", "codigos"]

    def get_codigos(self, obj):
        return [str(e.codigo) for e in obj.entradas.all()]


class OrdenSerializer(serializers.ModelSerializer):
    items = OrdenItemSerializer(many=True, read_only=True)
    usuario_nombre = serializers.CharField(source="usuario.username", read_only=True)
    segundos_restantes = serializers.SerializerMethodField()

    class Meta:
        model = Orden
        fields = ["id", "estado", "total", "creada", "segundos_restantes", "usuario_nombre", "items"]

    def get_segundos_restantes(self, obj):
        """Solo para órdenes PENDIENTES: cuánto falta para que se cancele sola."""
        return services.segundos_restantes(obj)
