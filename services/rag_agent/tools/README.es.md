# Herramientas del agente RAG — TrackFlow

Herramientas de **solo lectura** del agente RAG de TrackFlow. Cada tool
consulta un gestor **real** del monorepo, nunca simula ni inventa datos, y
sigue el mismo contrato: entrada/salida tipadas, timeout numérico explícito,
fallback honesto cuando no puede resolver, y sin ramas de escritura.

| Tool                | Consulta                           | Gestor real             | Nodo en el grafo        |
|---------------------|------------------------------------|-------------------------|-------------------------|
| `incident_lookup`   | Estado de tickets de soporte       | `services/api` (TinyDB) | `incident_lookup`       |
| `inventory_lookup`  | Stock por producto (SKU)           | `services/user-api` (SQLModel/PostgreSQL) | `inventory_lookup` |

El enrutamiento es **automático** (el usuario nunca indica qué fuente usar):
el nodo `intent` decide a partir de la pregunta si hace falta el RAG, una
tool, o ambos — y puede correr **las dos tools** si la pregunta menciona un
ticket **y** un SKU. Cada tool hace **una sola cosa** (tickets **o** stock,
nunca ambas). Las consultas operativas se resuelven solo con la tool; las
documentales solo con el RAG; y las preguntas mixtas ejecutan tools y luego
`retrieve` para que `generate` componga una respuesta basada en ambas fuentes.
El trace registra la decisión y el orden real de las fuentes utilizadas.

- Código: `services/rag_agent/tools/`
- Usado por: `services/rag_agent/graph.py` (nodos `incident_lookup` e
  `inventory_lookup` + aristas condicionales).

> _Versión en inglés: [README.md](./README.md)._

---

# Tool `incident_lookup` — Consulta de tickets de soporte (incidencias)

Herramienta de **solo lectura** del agente RAG de TrackFlow para consultar el
estado de tickets de soporte / incidencias contra el gestor de incidencias
**real** del monorepo (`services/api`). Nunca simula ni inventa datos.

- Código: `services/rag_agent/tools/incident_lookup.py`
- Usada por: el grafo del agente (`services/rag_agent/graph.py`) vía el nodo
  `incident_lookup` y la arista condicional que decide cuándo usarla en lugar
  del RAG.

## Contrato tipado

### Entrada — `IncidentQuery`

| Campo       | Tipo  | Uso                                                        |
|-------------|-------|------------------------------------------------------------|
| `ticket_id` | `str` | id concreto del ticket (uuid4 o slug tipo `INC-1234`). Equivale a `GET /api/incidents/{id}`. |
| `status`    | `str` | filtro: estado del incidente                               |
| `category`  | `str` | filtro: categoría del incidente                            |
| `origin`    | `str` | filtro: origen del incidente (branch/customer/internal)    |
| `branch`    | `str` | filtro: sucursal                                           |

`ticket_id` vacío o con formato inválido se rechaza en validación (el resultado
vuelve con `resolved=False` y un `error` honesto). Todos los filtros son
opcionales.

### Salida — `IncidentLookupResult`

Mismos campos que expone la API de incidencias (`models.Incident` para
`GET /api/incidents/{id}` y `IncidentListResponse` para `GET /api/incidents`):

`ticket_id`, `status`, `category`, `origin`, `branch`, `title`, `description`,
`created_at`, `updated_at`.

Más dos metadatos de la consulta (no son campos del gestor):

| Campo          | Tipo | Significado                                                                 |
|----------------|------|-----------------------------------------------------------------------------|
| `resolved`     | bool | `True` si la consulta pudo confirmar algo; `False` si no se pudo (fallo/timeout/inexistente). |
| `error`        | str  | mensaje del fallo cuando `resolved=False`                                    |
| `matched_filters` | list | filtros que se aplicaron                                            |

## Transporte: en-proceso, SIN HTTP

El gestor de incidencias vive en el mismo monorepo que el agente
(`services/api`), así que la tool **no hace una llamada HTTP**: importa en
proceso la misma capa de repositorio que sirve la API.

```python
from database import get_incidents_table   # Misma función que GET /api/incidents
from models import Incident                 # Mismo modelo que POST /api/incidents
```

Esto garantiza que se lee la **misma tabla TinyDB** que expone la API. No
existe un dataset paralelo ni datos hardcodeados.

> **⚠️ Nota para el revisor (autenticación):** el endpoint HTTP
> `GET /api/incidents` exige JWT (dependencia `get_current_user` del router).
> La tool **no necesita credenciales** porque lee en-proceso directamente de la
> capa de repositorio (tabla TinyDB), **sin pasar por el router HTTP** — por lo
> tanto el middleware de autenticación no aplica a este transporte. No se
> hardcodea ningún token: si en el futuro el transporte pasara a HTTP, las
> credenciales deberían venir de entorno/inyección, nunca hardcodeadas.

## Solo lectura, timeout y fallback

- **Solo lectura:** la tool únicamente consulta; no crea, actualiza ni elimina
  tickets. No existe ninguna rama de escritura en el módulo (el gestor expone
  POST/PATCH, pero no se tocan aquí). La test
  `test_lookup_does_not_write_to_the_manager` verifica que el número de
  documentos no cambia tras las consultas.
- **Timeout:** `INCIDENT_TIMEOUT_SECONDS` (default **4.0 s**, dentro del rango
  3–5 s). Se configura vía env var `INCIDENT_LOOKUP_TIMEOUT`
  (`float(os.getenv("INCIDENT_LOOKUP_TIMEOUT", "4.0"))`). Se aplica con
  `signal.SIGALRM` sobre el hilo principal (ayudante compartido
  `tools/_timeout.py`). Si el gestor no responde a tiempo (`TimeoutError`), la
  tool devuelve un resultado honesto: **"el gestor de incidencias no respondió
  en 4.0s — no pude confirmar el estado de ese ticket ahora mismo"**. El grafo
  por tanto nunca queda colgado.
- **Fallback honesto (nunca inventa un estado):** el nodo `incident_lookup`
  responde *"No pude confirmar el estado de ese ticket ahora mismo"* en estos
  casos:
  - El ticket no existe (`el ticket 'X' no existe`).
  - Varios resultados para un filtro (pide `ticket_id`, no elige por el
    usuario).
  - El gestor no responde a tiempo (timeout).
  - Entrada inválida.
  - Fallo inesperado del gestor.

En ningún caso se sintetiza un `status` que no se haya leído del gestor real.

## Uso

```python
from services.rag_agent.tools.incident_lookup import lookup_incidents

# por id concreto (GET /api/incidents/{id})
result = lookup_incidents({"ticket_id": "4bccd74e-..."})

# por filtros (GET /api/incidents?status=...)
result = lookup_incidents({"status": "open"})

# sin argumentos: lista ("N incidencias registradas")
result = lookup_incidents({})

# timeout explícito por llamada
result = lookup_incidents({"status": "open"}, timeout=5.0)
```

En el grafo, la consulta se dispara automáticamente cuando la intención detecta
un ticket/incidencia (nodo `intent` → arista condicional → `incident_lookup`).
Puede correr junto a `inventory_lookup` (y/o junto al RAG): el enrutamiento
decide de forma automática. Si la pregunta es operativa, la tool responde sin
ejecutar `retrieve`; si también pide conocimiento documental, `retrieve` se
ejecuta después de las tools. La tool hace **una sola cosa** (tickets); nunca
consulta inventario.

---

# Tool `inventory_lookup` — Consulta de inventario (stock por producto)

Herramienta de **solo lectura** para consultar el stock de un producto contra
el gestor de inventario **real** del monorepo (`services/user-api`). Aplica el
mismo tipo de contrato que `incident_lookup` (tipado, timeout, fallback, sin
datos simulados), sobre stock: equivale a `GET /inventory/products`.

- Código: `services/rag_agent/tools/inventory_lookup.py`
- Usada por: el grafo del agente (`services/rag_agent/graph.py`) vía el nodo
  `inventory_lookup` y la arista condicional que decide cuándo usarla en lugar
  del RAG.

## Contrato tipado

### Entrada — `InventoryQuery`

| Campo         | Tipo | Uso                                                            |
|---------------|------|----------------------------------------------------------------|
| `sku_id`      | `int`| id del producto (resolución exacta).                           |
| `sku`         | `str`| código SKU (p. ej. `CLT-SNK-W-42`); resolución exacta.         |
| `client_name` | `str`| filtro: cliente                                               |
| `category`    | `str`| filtro: categoría (`fashion` / `electronics` / `cosmetics`)    |
| `warehouse`   | `str`| filtro: almacén (`LA` / `ZGZ`)                                 |

Todos los campos son opcionales y se validan con Pydantic. Con `sku` o
`sku_id` la tool resuelve el producto exacto; sin SKU concreto consulta por
filtros, y sin filtros lista el catálogo ("N productos registrados").

### Salida — `InventoryLookupResult`

`.product` replica **los mismos campos que `SKUResponse`** (lo que devuelve
`GET /inventory/products`):

`id`, `name`, `sku`, `client_name`, `category`, `warehouse`, `current_stock`.

Más metadatos de la consulta (no son campos del gestor):

| Campo             | Tipo  | Significado                                                           |
|-------------------|-------|-----------------------------------------------------------------------|
| `product`         | dict  | `None` si no se resolvió un único producto.                           |
| `description`     | str   | texto cuando la consulta no devuelve un producto único (p. ej. recuento). |
| `resolved`        | bool  | `True` si la consulta pudo confirmar algo.                            |
| `error`           | str   | mensaje del fallo cuando `resolved=False`.                            |
| `matched_filters` | list  | filtros que se aplicaron.                                             |

## Transporte: en-proceso, SIN HTTP

El gestor de inventario vive en el mismo monorepo (`services/user-api`,
SQLModel/PostgreSQL, engine desde `DATABASE_URL`). La tool **no hace una
llamada HTTP**: abre una `Session` sobre el mismo engine
(`services/user-api/app/db.py`) y lee con los **mismos modelos SQLModel** y la
**misma fórmula de `current_stock`** que expone el router:

```python
# services/user-api/app/routers/inventory.py
# current_stock = Σ StockEntry.quantity − Σ StockExit.quantity (por sku+almacén)
#                 con COALESCE(SUM(...), 0) — escalar por subconsulta.
```

Esto garantiza que la cifra leída es exactamente la que expondría
`GET /inventory/products`. No existe un dataset paralelo ni datos hardcodeados.

> **⚠️ Nota técnica (colisión de paquete `app`):** en este monorepo
> `services/api/app.py` es un módulo regular que **sombrea** el *namespace
> package* `services/user-api/app/` a ojos de `import app` (los módulos
> regulares ganan a los namespace packages). Para evitarlo, la tool carga los
> módulos del gestor **por ruta de archivo** con `importlib` bajo nombres
> únicos (`trackflow_userapi_models`, `trackflow_userapi_schemas`,
> `trackflow_userapi_db`, ...). Los modelos/schemas/db no se importan a sí
> mismos con `from app...`, así que cargan limpios.
>
> **⚠️ Nota para el revisor (autenticación):** el endpoint HTTP
> `GET /inventory/products` exige JWT (dependencia `get_current_user` del
> router). La tool **no necesita credenciales** porque lee en-proceso
> directamente de la capa de datos **sin pasar por el router HTTP** — el
> middleware de autenticación no aplica a este transporte. No se hardcodea
> ningún token: si el transporte pasara a HTTP, las credenciales deberían
> venir de entorno/inyección, nunca hardcodeadas.

## Solo lectura, timeout y fallback

- **Solo lectura:** la tool únicamente consulta; nunca crea/actualiza/elimina
  SKU ni movimientos (StockEntry/StockExit). No existe ninguna rama de
  escritura en el módulo. La test
  `test_lookup_does_not_write_to_the_manager` de `test_inventory_lookup.py`
  verifica que el número de filas de `SKU` no cambia tras las consultas.
- **Timeout:** `INVENTORY_TIMEOUT_SECONDS` (default **4.0 s**, rango 3–5 s),
  vía env `INVENTORY_LOOKUP_TIMEOUT`. Se aplica con `signal.SIGALRM`
  (ayudante compartido `tools/_timeout.py`, el mismo de `incident_lookup`). Si
  expira: **"el gestor de inventario no respondió en 4.0s — no pude confirmar
  el stock de ese producto ahora mismo"**.
- **Fallback honesto (nunca inventa un stock):** el nodo `inventory_lookup`
  responde *"No pude consultar el stock de ese producto ahora mismo"* en estos
  casos:
  - El producto no existe (`el producto 'X' no existe` / `el producto con id
    'N' no existe`).
  - Varios productos para un filtro (pide `sku`/`sku_id`, no elige por el
    usuario): `hay N productos con: ... — especifica un sku_id o sku`.
  - `DATABASE_URL` no configurada: *"DATABASE_URL no está configurada ... No
    hay datos inventados."*.
  - Timeout, entrada inválida (`entrada inválida: ...`) o fallo inesperado
    (`fallo inesperado consultando inventario: ...`).

En ningún caso se sintetiza una cifra de `current_stock` que no se haya
calculado del gestor real.

## Uso

```python
from services.rag_agent.tools.inventory_lookup import lookup_inventory

# por SKU exacto (GET /inventory/products?sku=...)
result = lookup_inventory({"sku": "CLT-SNK-W-42"})

# por id de producto
result = lookup_inventory({"sku_id": 3})

# por filtros
result = lookup_inventory({"category": "electronics", "warehouse": "ZGZ"})

# sin argumentos: lista ("N productos registrados")
result = lookup_inventory({})

# acepta un str (se interpreta como SKU)
result = lookup_inventory("CLT-SNK-W-42")

# timeout explícito por llamada
result = lookup_inventory({"sku": "CLT-SNK-W-42"}, timeout=5.0)
```

En el grafo, la consulta se dispara automáticamente cuando la intención detecta
stock/inventario/SKU (nodo `intent` → arista condicional → `inventory_lookup`).
Puede correr junto a `incident_lookup` (y/o junto al RAG): el enrutamiento
decide de forma automática. Las consultas operativas van solo a la tool; las
preguntas mixtas ejecutan además `retrieve` después de las tools. La tool hace
**una sola cosa** (stock); nunca consulta tickets.

---

## Tests

- `tests/pipelines/test_incident_lookup.py` — contrato tipado, lectura real del
  gestor, timeout, fallback y solo-lectura.
- `tests/pipelines/test_inventory_lookup.py` — ídem para inventario: lee el
  gestor real en-proceso (mismos modelos SQLModel y misma fórmula
  `current_stock`) sobre una base SQLite aislada; contrato, timeout (SIGALRM),
  fallback honesto y solo-lectura.
- `tests/pipelines/test_agent_graph_routing.py` — enrutamiento del grafo
  (decisión automática RAG / tool / ambos, ticket inexistente, filtro múltiple,
  inventario con/sin SKU, ambas tools en una pregunta mixta, pregunta vacía).
- `tests/pipelines/test_agent_graph_evals.py` — evals `eval_ticket_lookup` y
  `eval_inventory_lookup` (traces persistidos con tool + retrieve + generate).

```bash
uv run pytest tests/pipelines/test_incident_lookup.py \
              tests/pipelines/test_inventory_lookup.py \
              tests/pipelines/test_agent_graph_routing.py \
              tests/pipelines/test_agent_graph_evals.py -q
```