import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("entradas", "0001_initial"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AlterField(
            model_name="evento",
            name="artista",
            field=models.CharField(max_length=300),
        ),
        migrations.CreateModel(
            name="Notificacion",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("titulo", models.CharField(max_length=160)),
                ("mensaje", models.TextField()),
                ("leida", models.BooleanField(default=False)),
                ("creada", models.DateTimeField(auto_now_add=True)),
                ("evento", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                                             related_name="notificaciones", to="entradas.evento")),
                ("usuario", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,
                                              related_name="notificaciones", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ["-creada"]},
        ),
    ]
