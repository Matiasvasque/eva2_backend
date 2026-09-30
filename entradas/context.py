"""Context processor: datos del alumno disponibles en todos los templates (footer)."""
from django.conf import settings


def alumno(request):
    return {"ALUMNO": settings.ALUMNO}
