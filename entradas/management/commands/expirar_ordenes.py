from django.core.management.base import BaseCommand

from entradas import services


class Command(BaseCommand):
    help = "Cancela las órdenes PENDIENTES que superaron el tiempo de reserva y repone su stock."

    def handle(self, *args, **options):
        n = services.expirar_pendientes()
        self.stdout.write(self.style.SUCCESS(f"{n} orden(es) pendiente(s) vencida(s) cancelada(s)."))
