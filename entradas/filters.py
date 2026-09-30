"""Filtros django-filter para los endpoints de consulta."""
import django_filters as df

from .models import Evento, Orden


class EventoFilter(df.FilterSet):
    """Ej: /api/eventos/?fecha_desde=2026-11-01&ciudad=Santiago&precio_max=30000"""
    fecha_desde = df.DateFilter(field_name="fecha", lookup_expr="date__gte")
    fecha_hasta = df.DateFilter(field_name="fecha", lookup_expr="date__lte")
    ciudad = df.CharFilter(field_name="recinto__ciudad", lookup_expr="icontains")
    artista = df.CharFilter(lookup_expr="icontains")
    precio_min = df.NumberFilter(field_name="sectores__precio", lookup_expr="gte", distinct=True)
    precio_max = df.NumberFilter(field_name="sectores__precio", lookup_expr="lte", distinct=True)

    class Meta:
        model = Evento
        fields = ["recinto", "activo", "creado_por"]


class OrdenFilter(df.FilterSet):
    desde = df.DateFilter(field_name="creada", lookup_expr="date__gte")
    hasta = df.DateFilter(field_name="creada", lookup_expr="date__lte")

    class Meta:
        model = Orden
        fields = ["estado", "usuario"]
