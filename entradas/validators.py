"""
Validadores personalizados a nivel de modelo.
Se ejecutan en full_clean(), en ModelForms y en los serializadores DRF.
"""
import re

from django.core.exceptions import ValidationError
from django.utils import timezone


def normalizar_rut(rut):
    """Quita puntos y espacios y deja la K en mayúscula: 12.345.678-k -> 12345678-K"""
    return re.sub(r"[.\s]", "", str(rut)).upper()


def validar_rut(value):
    """Valida formato y dígito verificador (módulo 11) de un RUT chileno."""
    rut = normalizar_rut(value)
    if not re.fullmatch(r"\d{7,8}-[\dK]", rut):
        raise ValidationError("Formato de RUT inválido. Ejemplo: 12345678-5")
    cuerpo, dv = rut.split("-")
    suma, mult = 0, 2
    for digito in reversed(cuerpo):  # de derecha a izquierda, multiplicadores 2..7
        suma += int(digito) * mult
        mult = 2 if mult == 7 else mult + 1
    resto = 11 - (suma % 11)
    esperado = "0" if resto == 11 else "K" if resto == 10 else str(resto)
    if dv != esperado:
        raise ValidationError("RUT inválido: el dígito verificador no coincide.")


def validar_email(value):
    """Formato de email estricto: usuario@dominio.tld"""
    if not re.fullmatch(r"^[\w.+-]+@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)+$", value or ""):
        raise ValidationError("Formato de email inválido.")


def validar_fecha_futura(value):
    """La fecha/hora de un evento no puede estar en el pasado."""
    if value <= timezone.now():
        raise ValidationError("La fecha del evento no puede estar en el pasado.")


def validar_precio(value):
    """Tipo/valor correcto: el precio debe ser mayor que 0."""
    if value is None or value <= 0:
        raise ValidationError("El precio debe ser mayor que 0.")


def validar_cantidad_positiva(value):
    """Cantidades (stock, ítems) deben ser enteros >= 1."""
    if not isinstance(value, int) or value < 1:
        raise ValidationError("La cantidad debe ser un entero mayor o igual a 1.")
