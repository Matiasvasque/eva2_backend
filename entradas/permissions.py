"""Permisos DRF por rol (RBAC). El rol se toma del usuario autenticado por el JWT."""
from rest_framework.permissions import SAFE_METHODS, BasePermission

from .models import Usuario


class EsEspectador(BasePermission):
    def has_permission(self, request, view):
        u = request.user
        return bool(u and u.is_authenticated and u.rol == Usuario.Rol.ESPECTADOR)


class EsOrganizador(BasePermission):
    def has_permission(self, request, view):
        u = request.user
        return bool(u and u.is_authenticated and u.rol == Usuario.Rol.ORGANIZADOR)


class LecturaPublicaEscrituraOrganizador(BasePermission):
    """GET público (catálogo); POST/PUT/PATCH/DELETE solo un Organizador."""

    def has_permission(self, request, view):
        if request.method in SAFE_METHODS:
            return True
        u = request.user
        return bool(u and u.is_authenticated and u.rol == Usuario.Rol.ORGANIZADOR)


class EsOrganizadorYDuenioDelEvento(BasePermission):
    """
    GET público. Crear: cualquier Organizador. Editar/eliminar (PUT/PATCH/DELETE):
    solo el Organizador que creó ese evento (creado_por). Se valida a nivel de
    objeto (has_object_permission), que DRF invoca automáticamente en retrieve/
    update/destroy vía get_object().
    """

    def has_permission(self, request, view):
        if request.method in SAFE_METHODS:
            return True
        u = request.user
        return bool(u and u.is_authenticated and u.rol == Usuario.Rol.ORGANIZADOR)

    def has_object_permission(self, request, view, obj):
        if request.method in SAFE_METHODS:
            return True
        return obj.creado_por_id == request.user.id


class EsOrganizadorYDuenioDelEventoDelSector(BasePermission):
    """
    Sectores (zonas/entradas): lectura pública; crear/editar/eliminar solo un Organizador,
    y solo sobre sectores de eventos que él mismo creó (la creación se valida en la vista).
    """

    def has_permission(self, request, view):
        if request.method in SAFE_METHODS:
            return True
        u = request.user
        return bool(u and u.is_authenticated and u.rol == Usuario.Rol.ORGANIZADOR)

    def has_object_permission(self, request, view, obj):
        if request.method in SAFE_METHODS:
            return True
        return obj.evento.creado_por_id == request.user.id
