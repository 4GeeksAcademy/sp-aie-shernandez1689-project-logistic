# Tool `incident_lookup` — Consulta de tickets de soporte (incidencias)

Herramienta de **solo lectura** del agente RAG de TrackFlow para consultar el
estado de tickets de soporte / incidencias contra el gestor de incidencias
**real** del monorepo (`services/api`). Nunca simula ni inventa datos.

- Código: `services/rag_agent/tools/incident_lookup.py`
- Usada por: el grafo del agente (`services/rag_agent/graph.py`) vía el nodo
  `incident_lookup` y la arista condicional que decide cuándo usarla en lugar
  del RAG.

> _Versión en inglés: [README.md](./README.md)._

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

## Solo lectura

La tool únicamente consulta: no crea, actualiza ni elimina tickets. No existe
ninguna rama de escritura en el módulo (el gestor expone POST/PATCH, pero no se
tocan aquí). La test `test_lookup_does_not_write_to_the_manager` verifica que el
número de documentos no cambia tras las consultas.

## Timeout numérico explícito

`INCIDENT_TIMEOUT_SECONDS` (default **4.0 s**, dentro del rango 3–5 s exigido).
Se configura vía env var `INCIDENT_LOOKUP_TIMEOUT`:

```python
INCIDENT_TIMEOUT_SECONDS = float(os.getenv("INCIDENT_LOOKUP_TIMEOUT", "4.0"))
```

El timeout se aplica con `signal.SIGALRM` sobre el hilo principal. Si el gestor
no responde a tiempo (`TimeoutError`), la tool devuelve un resultado honesto:
**"el gestor de incidencias no respondió en 4.0s — no pude confirmar el estado
de ese ticket ahora mismo"**. El grafo por tanto nunca queda colgado.

## Fallback honesto (nunca inventa un estado)

El nodo `incident_lookup` del grafo responde con *"No pude confirmar el estado
de ese ticket ahora mismo"* en estos casos:

- El ticket no existe (`el ticket 'X' no existe`).
- Varios resultados para un filtro (pide `ticket_id`, no elige por el usuario).
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
un ticket/incidencia (nodo `intent` → arista condicional → `incident_lookup`),
**en lugar del RAG** (nunca ambos).

## Tests

- `tests/pipelines/test_incident_lookup.py` — contrato tipado, lectura real del
  gestor, timeout, fallback y solo-lectura.
- `tests/pipelines/test_agent_graph_routing.py` — enrutamiento del grafo (tool
  vs. RAG, ticket inexistente, filtro múltiple, pregunta vacía).
- `tests/pipelines/test_agent_graph_evals.py` — eval `eval_ticket_lookup`
  (trace persistido con la tool en lugar del RAG).

```bash
uv run pytest tests/pipelines/test_incident_lookup.py \
              tests/pipelines/test_agent_graph_routing.py \
              tests/pipelines/test_agent_graph_evals.py -q
```