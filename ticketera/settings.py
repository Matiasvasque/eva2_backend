"""
Configuración del proyecto Ticketera (venta de entradas para eventos).
Puntos clave: PostgreSQL, DRF + JWT + django-filter + Swagger, templates,
modelo de usuario personalizado y SIN Django Admin (prohibido por el docente).
"""
import os
from datetime import timedelta
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")  # carga las variables de .env (DB_PASSWORD, etc.) al entorno

SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "dev-key-cambiar-en-produccion")
DEBUG = os.environ.get("DJANGO_DEBUG", "True") == "True"  # con False se ve el 404 propio también en Django
ALLOWED_HOSTS = ["*"]

# Datos del alumno para el footer obligatorio (base.html)
ALUMNO = {
    "nombre": "Matías Alexander Vásquez Freire",
    "seccion": os.environ.get("ALUMNO_SECCION", "AOL-ELE-TILE21-C14/V"),
    "anio": 2026,
}

# ── Aplicaciones (sin django.contrib.admin) ──
INSTALLED_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "rest_framework_simplejwt",
    "django_filters",
    "drf_spectacular",
    "entradas",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "entradas.middleware.ExpirarPendientesMiddleware",  # cancela reservas vencidas (5 min)
]

ROOT_URLCONF = "ticketera.urls"

# ── Templates (interfaz 100% HTML) ──
TEMPLATES = [{
    "BACKEND": "django.template.backends.django.DjangoTemplates",
    "DIRS": [BASE_DIR / "templates"],
    "APP_DIRS": True,
    "OPTIONS": {"context_processors": [
        "django.template.context_processors.request",
        "django.contrib.auth.context_processors.auth",
        "django.contrib.messages.context_processors.messages",
        "entradas.context.alumno",
    ]},
}]

WSGI_APPLICATION = "ticketera.wsgi.application"

# ── Base de datos: PostgreSQL obligatorio (no SQLite) ──
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.environ.get("DB_NAME", "ticketera_db"),
        "USER": os.environ.get("DB_USER", "postgres"),
        "PASSWORD": os.environ.get("DB_PASSWORD", "postgres"),
        "HOST": os.environ.get("DB_HOST", "localhost"),
        "PORT": os.environ.get("DB_PORT", "5432"),
    }
}

# Usuario personalizado (debe definirse ANTES del primer migrate)
AUTH_USER_MODEL = "entradas.Usuario"

# ── Django REST Framework ──
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": ["rest_framework_simplejwt.authentication.JWTAuthentication"],
    # Seguro por defecto: lo público se declara explícitamente con AllowAny.
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    "DEFAULT_FILTER_BACKENDS": [
        "django_filters.rest_framework.DjangoFilterBackend",
        "rest_framework.filters.SearchFilter",
        "rest_framework.filters.OrderingFilter",
    ],
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 12,
}

# ── JWT: access corto, refresh largo ──
SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=30),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=7),
    "AUTH_HEADER_TYPES": ("Bearer",),
}

# ── Swagger / OpenAPI (ruta /api/docs/) ──
SPECTACULAR_SETTINGS = {
    "TITLE": "Ticketera API",
    "DESCRIPTION": "Venta de entradas para eventos y conciertos",
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
}

# ── Compras: minutos que una orden PENDIENTE reserva el stock antes de cancelarse sola ──
ORDEN_MINUTOS_EXPIRACION = int(os.environ.get("ORDEN_MINUTOS_EXPIRACION", "5"))

# ── Correo: en desarrollo los emails se imprimen en la terminal de runserver ──
EMAIL_BACKEND = os.environ.get("EMAIL_BACKEND", "django.core.mail.backends.console.EmailBackend")
DEFAULT_FROM_EMAIL = "Ticketera <no-responder@ticketera.cl>"

LANGUAGE_CODE = "es-cl"
TIME_ZONE = "America/Santiago"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
