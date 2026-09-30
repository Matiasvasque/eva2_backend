"""URLs: API REST, Swagger, páginas HTML y 404 controlado con re_path."""
from django.conf import settings
from django.conf.urls.static import static
from django.urls import include, path, re_path
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView
from rest_framework.routers import DefaultRouter
from rest_framework_simplejwt.views import TokenRefreshView

from entradas import views as v

router = DefaultRouter()
router.register("recintos", v.RecintoViewSet, basename="recinto")
router.register("eventos", v.EventoViewSet, basename="evento")
router.register("sectores", v.SectorViewSet, basename="sector")
router.register("compras", v.ComprasOrganizadorViewSet, basename="compra")
router.register("mis-compras", v.MisComprasViewSet, basename="mi-compra")

api = [
    path("token/", v.LoginView.as_view()),
    path("token/refresh/", TokenRefreshView.as_view()),
    path("registro/", v.RegistroView.as_view()),
    path("carro-tickets/", v.CarroTicketsView.as_view()),
    path("mis-entradas/", v.MisEntradasView.as_view()),
    path("metricas/", v.MetricasView.as_view()),
    path("notificaciones/", v.NotificacionesView.as_view()),
    path("schema/", SpectacularAPIView.as_view(), name="schema"),
    path("docs/", SpectacularSwaggerView.as_view(url_name="schema"), name="swagger"),
    path("", include(router.urls)),
]

urlpatterns = [
    path("api/", include(api)),
    path("", v.pagina("landing.html", pagina="landing"), name="landing"),
    path("evento/<int:pk>/", v.evento_detalle, name="evento"),
    path("evento/<int:pk>/editar/", v.evento_editar, name="evento-editar"),
    path("evento/<int:pk>/eliminar/", v.evento_eliminar, name="evento-eliminar"),
    path("login/", v.pagina("login.html", pagina="login")),
    path("registro/", v.pagina("registro.html", pagina="registro")),
    path("carro/", v.pagina("carro.html", pagina="carro")),
    path("mis-entradas/", v.pagina("mis_entradas.html", pagina="mis-entradas")),
    path("mis-compras/", v.pagina("mis_compras.html", pagina="mis-compras")),
    path("panel/", v.pagina("panel.html", pagina="panel")),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

# 404 controlado: cualquier ruta no reconocida cae en esta expresión regular.
urlpatterns += [re_path(r"^.*$", v.error_404)]
handler404 = "entradas.views.error_404"
