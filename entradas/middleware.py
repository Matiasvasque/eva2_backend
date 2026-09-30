"""Cancela automáticamente las órdenes PENDIENTES vencidas (5 min) antes de atender cualquier llamada a /api/."""
import logging

from . import services

logger = logging.getLogger(__name__)


class ExpirarPendientesMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.path.startswith("/api/"):
            try:
                services.expirar_pendientes()
            except Exception:  # nunca debe tumbar la petición del usuario
                logger.exception("No se pudieron expirar las órdenes pendientes")
        return self.get_response(request)
