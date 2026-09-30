# Ticketera — Venta de Entradas (EVA-2 Backend)

Proyecto Django + DRF + PostgreSQL (Proyecto 3: Venta de Entradas para Eventos y
Conciertos), con interfaz 100% en templates HTML (sin Django Admin, sin frontend
separado) que consume la propia API vía JWT.

## Instalación

```bash
python -m venv venv && source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env      # ajusta credenciales de PostgreSQL
createdb ticketera_db     # o crea la BD desde pgAdmin

python manage.py migrate
python manage.py poblar_datos      # crea usuarios, recintos y eventos de prueba
python manage.py runserver
```

Abre `http://127.0.0.1:8000/`.

## Usuarios de prueba (creados por `poblar_datos`)

| Usuario         | Password        | Rol                    |
|-----------------|-----------------|------------------------|
| organizador1    | organizador123  | Organizador de Eventos |
| espectador1     | espectador123   | Espectador             |
| admin            | admin1234       | Superusuario (solo para administración interna; no tiene panel propio) |

## Documentación de la API

- Swagger UI: `/api/docs/`
- Esquema OpenAPI: `/api/schema/`

## Estructura

- `entradas/models.py` — Usuario, Recinto, Evento, Sector, Carro (1 a 1 con Usuario), Orden, OrdenItem, Entrada
- `entradas/services.py` — lógica transaccional (reserva, pago, cancelación, expiración, stock, tickets UUID)
- `entradas/views.py` — endpoints DRF + vistas HTML (templates)
- `entradas/permissions.py` — RBAC por rol (Espectador / Organizador de Eventos)
- `templates/` — landing, evento, login, registro, carro, mis entradas, panel del organizador, 404
- `static/js/app.js` — cliente fetch + JWT que conecta los templates con la API

## Flujo de compra (lo decide el espectador)

1. **Carro** -> `POST /api/mis-compras/reservar/`: crea una orden `PENDIENTE`, verifica que haya stock (sin descontarlo) y congela los precios.
2. **Pagar** -> `POST /api/mis-compras/{id}/pagar/`: pasa a `PAGADO`: en ese instante se descuenta el stock y se emite un ticket UUID por entrada.
   **Cancelar** -> `POST /api/mis-compras/{id}/cancelar/`: pasa a `CANCELADO`; solo se repone stock si la orden estaba `PAGADO`
   (una orden pagada solo se puede cancelar si el evento aún no ocurrió).
3. Si una orden `PENDIENTE` no se paga en **5 minutos** (`ORDEN_MINUTOS_EXPIRACION` en `settings.py`) se cancela
   automáticamente (no hay stock que reponer, porque nunca se descontó). Lo hace un middleware en cada llamada a `/api/`;
   también puedes forzarlo con `python manage.py expirar_ordenes`.

El organizador ya no aprueba ni marca compras: en su panel (pestaña Ventas) solo consulta ventas y reservas pendientes.

## Panel del organizador (CRUD completo)

En `/panel/` (pestañas Resumen · Mis eventos · Recintos · Ventas) el organizador puede:

- **Eventos**: crear (imagen, artistas separados por coma, recinto, fecha), editar, ocultar/publicar y eliminar.
- **Zonas y precios**: por evento define zonas (Cancha, Tribuna, Palco…) con su propio precio y cantidad de entradas;
  se editan y eliminan desde `Editar`. Al cambiar la capacidad el stock se recalcula y no puede bajar de lo vendido.
- **Recintos**: crear, editar y eliminar.
- **Cambio de fecha o recinto**: si ya hay entradas vendidas, cada comprador recibe una notificación (se ve en
  «Mis entradas») y un correo (en desarrollo se imprime en la terminal de `runserver`); sus tickets muestran
  siempre la fecha vigente del evento.
- **Eliminar**: un evento con entradas vendidas no se puede eliminar (responde 409 con el mensaje
  «No se puede eliminar el evento porque ya hay N entrada(s) vendida(s)»); se oculta desmarcando «activo».

Solo el organizador dueño del evento puede modificar sus zonas (validado en la API).
Tras actualizar el proyecto ejecuta `python manage.py migrate` (migración `0002`).

## Notas de arquitectura

- **Sin Django Admin**: se removió `django.contrib.admin` de `INSTALLED_APPS`.
- **404 controlado**: `ticketera/urls.py` usa `re_path(r"^.*$", ...)` como comodín + `handler404`.
- **Carro persistente y 1 a 1**: `Carro.usuario` es `OneToOneField(Usuario)`, vive en
  PostgreSQL (no en sesión), por eso sobrevive al logout.
- **Stock**: no se descuenta al agregar al carro ni al reservar (orden `PENDIENTE`); se descuenta
  solo en el momento en que la orden pasa a `PAGADO`, de forma atómica (`transaction.atomic` +
  `select_for_update`, revalidando el stock). Si dos personas reservan las últimas entradas, quien
  paga primero se las queda y la otra orden se cancela con un mensaje claro. Cancelar una orden
  `PAGADO` repone el stock; cancelar o vencer una `PENDIENTE` no toca el stock.
- **Tercera forma normal (3FN)**: ningún modelo guarda un atributo derivable de otra
  tabla vía una FK propia (ej. `Evento` no guarda una tienda; en este proyecto de una
  sola plataforma tampoco aplica ese caso). Los únicos campos "redundantes" son
  `OrdenItem.precio_unitario` y `Orden.total`, que son snapshots históricos del
  momento de la compra (patrón estándar de facturación), no violaciones de 3FN.
- **Registro de Organizador**: por simplicidad del proyecto de curso, el registro
  público (`/api/registro/`) permite elegir el rol (Espectador u Organizador). Si tu
  docente pide que solo un superusuario pueda crear Organizadores, es un cambio menor
  en `RegistroSerializer` — dímelo y lo ajusto.

## Pruebas automatizadas

```bash
python manage.py test entradas
```

Corre 33 pruebas que cubren: JWT y claim de rol, permisos RBAC (incluyendo que
solo el dueño de un evento pueda editarlo/eliminarlo), carro 1 a 1 persistente,
checkout atómico (descuento de stock, congelamiento de precio, tickets UUID
únicos), cancelación con reposición de stock, transiciones de estado
inválidas, validadores (RUT, fecha futura), el 404 controlado y Swagger.

## CRUD de eventos (Organizador)

- Cualquier Organizador puede crear eventos (`POST /api/eventos/`); queda
  registrado como dueño en `creado_por`.
- Solo el dueño puede editarlo (`PATCH/PUT`) o eliminarlo (`DELETE`); otro
  Organizador recibe `403 Forbidden`.
- En `evento.html`, los botones "Editar"/"Eliminar" solo se muestran si el
  usuario logueado es el dueño (comparando el `user_id` del JWT decodificado
  en el navegador). Esa comparación en JS es solo para la interfaz — la
  autorización real siempre la valida la API (`EsOrganizadorYDuenioDelEvento`
  en `permissions.py`), así que aunque alguien manipule el HTML o llame a la
  API directo, no puede editar eventos ajenos.
