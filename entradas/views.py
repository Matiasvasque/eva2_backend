"""Vistas de la API REST (con permisos por rol) y vistas HTML (templates)."""
from django.db.models import ProtectedError, Sum
from django.shortcuts import get_object_or_404, render
from drf_spectacular.utils import extend_schema
from rest_framework import generics, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.views import TokenObtainPairView

from . import services
from .filters import EventoFilter, OrdenFilter
from .models import (Carro, CarroItem, Entrada, Evento, Notificacion, Orden, Recinto, Sector,
                     Usuario)
from .permissions import (EsEspectador, EsOrganizador, EsOrganizadorYDuenioDelEvento,
                          EsOrganizadorYDuenioDelEventoDelSector, LecturaPublicaEscrituraOrganizador)
from .serializers import (AgregarAlCarroSerializer, CarroSerializer, EntradaSerializer,
                          EventoSerializer, OrdenSerializer,
                          NotificacionSerializer, RecintoSerializer, RegistroSerializer,
                          SectorSerializer,
                          TokenConRolSerializer)


# ───────────── Autenticación ─────────────
class LoginView(TokenObtainPairView):
    """POST /api/token/ -> access + refresh (con claim 'rol')."""
    serializer_class = TokenConRolSerializer


class RegistroView(generics.CreateAPIView):
    """POST /api/registro/ -> crea un usuario (Espectador u Organizador, según 'rol')."""
    serializer_class = RegistroSerializer
    permission_classes = [AllowAny]


# ───────────── Catálogo ─────────────
class EliminacionProtegidaMixin:
    """Si el objeto tiene datos asociados protegidos (PROTECT), responde 409 con un mensaje claro en vez de 500."""
    mensaje_protegido = "No se puede eliminar porque tiene datos asociados."

    def destroy(self, request, *args, **kwargs):
        try:
            return super().destroy(request, *args, **kwargs)
        except ProtectedError:
            return Response({"detail": self.mensaje_protegido}, status=status.HTTP_409_CONFLICT)


class RecintoViewSet(EliminacionProtegidaMixin, viewsets.ModelViewSet):
    """Lectura pública; alta/edición/borrado solo un Organizador."""
    serializer_class = RecintoSerializer
    queryset = Recinto.objects.all()
    mensaje_protegido = "Este recinto tiene eventos asociados. Elimina o cambia esos eventos antes de borrarlo."
    permission_classes = [LecturaPublicaEscrituraOrganizador]


class EventoViewSet(EliminacionProtegidaMixin, viewsets.ModelViewSet):
    """
    GET público con filtros. Crear: cualquier Organizador. Editar/eliminar:
    solo el Organizador dueño del evento (ver EsOrganizadorYDuenioDelEvento).
    """
    serializer_class = EventoSerializer
    permission_classes = [EsOrganizadorYDuenioDelEvento]
    filterset_class = EventoFilter
    mensaje_protegido = ("No se puede eliminar el evento: tiene compras (incluso canceladas) en el historial. "
                         "Desmárcalo como activo para ocultarlo del público.")
    search_fields = ["nombre", "artista", "recinto__nombre"]
    ordering_fields = ["fecha", "nombre"]

    def get_queryset(self):
        qs = Evento.objects.select_related("recinto", "creado_por").prefetch_related("sectores").distinct()
        u = self.request.user
        es_organizador = u.is_authenticated and u.rol == Usuario.Rol.ORGANIZADOR
        if self.request.method in ("GET", "HEAD") and not es_organizador:
            return qs.filter(activo=True)  # público: solo eventos activos
        return qs  # el organizador ve (y puede editar) también los inactivos

    def perform_create(self, serializer):
        serializer.save(creado_por=self.request.user)

    def perform_update(self, serializer):
        """Si cambia la fecha o el recinto, se avisa a quienes ya compraron entradas."""
        antes = serializer.instance
        fecha_antes, recinto_antes = antes.fecha, antes.recinto
        evento = serializer.save()
        self._notificados = services.notificar_cambio_evento(evento, fecha_antes, recinto_antes)

    def update(self, request, *args, **kwargs):
        resp = super().update(request, *args, **kwargs)
        resp.data = {**resp.data, "notificados": getattr(self, "_notificados", 0)}
        return resp

    def destroy(self, request, *args, **kwargs):
        evento = self.get_object()  # valida que sea el dueño (403 si no)
        vendidas = services.entradas_comprometidas(evento)
        if vendidas:
            return Response({"detail": f"No se puede eliminar el evento porque ya hay {vendidas} "
                                       f"entrada(s) vendida(s) o reservada(s). Puedes ocultarlo desmarcando 'activo'."},
                            status=status.HTTP_409_CONFLICT)
        return super().destroy(request, *args, **kwargs)

    @extend_schema(responses=SectorSerializer(many=True))
    @action(detail=True, methods=["get"], permission_classes=[AllowAny])
    def sectores(self, request, pk=None):
        """GET /api/eventos/{id}/sectores/ (público)."""
        evento = self.get_object()
        return Response(SectorSerializer(evento.sectores.all(), many=True).data)


class SectorViewSet(EliminacionProtegidaMixin, viewsets.ModelViewSet):
    """
    Zonas de un evento (Cancha, Tribuna, Palco...) con precio y capacidad propios.
    Lectura pública; crear/editar/eliminar solo el Organizador dueño del evento.
    El stock inicial = capacidad; al editar la capacidad el stock se recalcula.
    """
    serializer_class = SectorSerializer
    permission_classes = [EsOrganizadorYDuenioDelEventoDelSector]
    queryset = Sector.objects.select_related("evento")
    filterset_fields = ["evento"]
    mensaje_protegido = "No se puede eliminar esta zona: tiene ventas (incluso canceladas) en el historial."

    def perform_create(self, serializer):
        evento = serializer.validated_data["evento"]
        if evento.creado_por_id != self.request.user.id:
            raise PermissionDenied("Solo el organizador que creó el evento puede agregarle zonas.")
        serializer.save(stock_disponible=serializer.validated_data["capacidad_total"])

    def destroy(self, request, *args, **kwargs):
        sector = self.get_object()
        vendidas = sector.capacidad_total - sector.stock_disponible
        if vendidas > 0:
            return Response({"detail": f"No se puede eliminar la zona «{sector.nombre}» porque ya hay "
                                       f"{vendidas} entrada(s) vendida(s)."}, status=status.HTTP_409_CONFLICT)
        return super().destroy(request, *args, **kwargs)

    def perform_update(self, serializer):
        evento = serializer.validated_data.get("evento", serializer.instance.evento)
        if evento.creado_por_id != self.request.user.id:
            raise PermissionDenied("Solo el organizador que creó el evento puede editar sus zonas.")
        serializer.save()


# ───────────── Carro persistente (Espectador) ─────────────
class CarroTicketsView(APIView):
    """
    GET    /api/carro-tickets/            -> mi carro (1 a 1 con el usuario)
    POST   /api/carro-tickets/            -> {sector, cantidad}: agrega (NO descuenta stock)
    DELETE /api/carro-tickets/?sector=ID  -> quita un ítem
    """
    permission_classes = [EsEspectador]

    @extend_schema(responses=CarroSerializer)
    def get(self, request):
        carro, _ = Carro.objects.get_or_create(usuario=request.user)
        return Response(CarroSerializer(carro).data)

    @extend_schema(request=AgregarAlCarroSerializer, responses=CarroSerializer)
    def post(self, request):
        ser = AgregarAlCarroSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        sector, cant = ser.validated_data["sector"], ser.validated_data["cantidad"]
        carro, _ = Carro.objects.get_or_create(usuario=request.user)
        item, creado = CarroItem.objects.get_or_create(carro=carro, sector=sector, defaults={"cantidad": cant})
        if not creado:  # sin duplicar: se acumula la cantidad
            item.cantidad += cant
            item.save()
        return Response(CarroSerializer(carro).data, status=status.HTTP_201_CREATED)

    @extend_schema(responses={204: None})
    def delete(self, request):
        n, _ = CarroItem.objects.filter(carro__usuario=request.user, sector_id=request.query_params.get("sector")).delete()
        return Response(status=status.HTTP_204_NO_CONTENT if n else status.HTTP_404_NOT_FOUND)


class MisEntradasView(generics.ListAPIView):
    """GET /api/mis-entradas/ -> tickets con código UUID."""
    serializer_class = EntradaSerializer
    permission_classes = [EsEspectador]

    def get_queryset(self):
        return Entrada.objects.filter(orden_item__orden__usuario=self.request.user) \
            .select_related("orden_item__sector__evento__recinto", "orden_item__orden")


class MisComprasViewSet(viewsets.ReadOnlyModelViewSet):
    """
    Compras del espectador. Él decide: reservar el carro, pagar o cancelar (el organizador no aprueba nada).

      GET  /api/mis-compras/                 historial (filtro ?estado=PENDIENTE|PAGADO|CANCELADO)
      POST /api/mis-compras/reservar/        carro -> orden PENDIENTE (verifica stock, NO lo descuenta; 5 minutos para pagar)
      POST /api/mis-compras/{id}/pagar/      PENDIENTE -> PAGADO (aquí se descuenta el stock y se emiten los tickets)
      POST /api/mis-compras/{id}/cancelar/   cancela (repone stock solo si estaba PAGADO)
    Una orden PENDIENTE que no se paga en 5 minutos se cancela automáticamente.
    """
    serializer_class = OrdenSerializer
    permission_classes = [EsEspectador]
    filterset_class = OrdenFilter

    def get_queryset(self):
        return Orden.objects.filter(usuario=self.request.user) \
            .prefetch_related("items__sector__evento", "items__entradas")

    @extend_schema(request=None, responses=OrdenSerializer)
    @action(detail=False, methods=["post"])
    def reservar(self, request):
        orden = services.reservar_carro(request.user)
        return Response(OrdenSerializer(orden).data, status=status.HTTP_201_CREATED)

    @extend_schema(request=None, responses=OrdenSerializer)
    @action(detail=True, methods=["post"])
    def pagar(self, request, pk=None):
        return Response(OrdenSerializer(services.pagar_orden(request.user, pk)).data)

    @extend_schema(request=None, responses=OrdenSerializer)
    @action(detail=True, methods=["post"])
    def cancelar(self, request, pk=None):
        return Response(OrdenSerializer(services.cancelar_orden(request.user, pk)).data)


# ───────────── Ventas (Organizador: solo lectura) ─────────────
class ComprasOrganizadorViewSet(viewsets.ViewSet):
    """GET /api/compras/ -> todas las ventas y reservas (solo consulta: las compras las decide el espectador)."""
    permission_classes = [EsOrganizador]

    @extend_schema(responses=OrdenSerializer(many=True))
    def list(self, request):
        qs = Orden.objects.all().select_related("usuario").prefetch_related("items__sector__evento", "items__entradas")
        return Response(OrdenSerializer(qs, many=True).data)


class NotificacionesView(APIView):
    """GET /api/notificaciones/ (mis avisos) y POST para marcarlos todos como leídos."""
    permission_classes = [IsAuthenticated]

    @extend_schema(responses=dict)
    def get(self, request):
        qs = Notificacion.objects.filter(usuario=request.user)
        return Response({"no_leidas": qs.filter(leida=False).count(),
                         "results": NotificacionSerializer(qs[:50], many=True).data})

    @extend_schema(request=None, responses=dict)
    def post(self, request):
        n = Notificacion.objects.filter(usuario=request.user, leida=False).update(leida=True)
        return Response({"marcadas": n})


class MetricasView(APIView):
    """GET /api/metricas/ (dashboard del Organizador)."""
    permission_classes = [EsOrganizador]

    @extend_schema(responses=dict)
    def get(self, request):
        vigentes = Orden.objects.filter(estado__in=[Orden.Estado.PAGADO, Orden.Estado.ENTREGADO])
        return Response({
            "eventos": Evento.objects.count(),
            "recintos": Recinto.objects.count(),
            "ordenes": Orden.objects.count(),
            "pendientes": Orden.objects.filter(estado=Orden.Estado.PENDIENTE).count(),
            "ingresos": vigentes.aggregate(t=Sum("total"))["t"] or 0,
            "entradas_vendidas": Entrada.objects.filter(orden_item__orden__in=vigentes).count(),
        })


# ───────────── Vistas HTML (templates 100% Django) ─────────────
# Las páginas son "cascarones" HTML; los datos se piden a la API con fetch + JWT (static/js/app.js).
def pagina(nombre_template, **extra):
    def vista(request):
        return render(request, nombre_template, extra)
    return vista


def evento_detalle(request, pk):
    get_object_or_404(Evento, pk=pk)
    return render(request, "evento.html", {"evento_id": pk, "pagina": "evento"})


def evento_editar(request, pk):
    """Formulario de edición. El dueño se valida en JS (comparando el JWT) y, de verdad, en la API."""
    get_object_or_404(Evento, pk=pk)
    return render(request, "evento_form.html", {"evento_id": pk, "pagina": "evento-editar"})


def evento_eliminar(request, pk):
    """Confirmación de borrado. El DELETE real lo hace la API y valida la propiedad del evento."""
    evento = get_object_or_404(Evento, pk=pk)
    return render(request, "evento_eliminar.html", {"evento_id": pk, "nombre": evento.nombre, "pagina": "evento-eliminar"})


def error_404(request, *args, **kwargs):
    """404 controlado: se usa como handler404 y como re_path comodín."""
    return render(request, "404.html", status=404)
