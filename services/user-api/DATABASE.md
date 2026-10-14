# Database configuration

Authentication continues to use the existing TinyDB users, profiles and
applications tables. SQLModel uses a separate PostgreSQL engine for inventory;
no authentication tables are created in PostgreSQL.

Set `DATABASE_URL` in the ignored `services/user-api/.env` file using the
PostgreSQL connection string from the Supabase project's Connect panel:

```dotenv
DATABASE_URL=postgresql+psycopg://USER:URL_ENCODED_PASSWORD@HOST:PORT/postgres?sslmode=require
```

The values above are placeholders, not credentials. Copy the actual host, user
and port from Supabase, and URL-encode special characters in the password. Do not
use the Supabase HTTP API URL or API keys. Never commit the real connection
string. Environment variables take precedence over the `.env` file.

The engine enables connection health checks and defaults to required TLS when
`sslmode` is absent. Creating the engine does not contact Supabase or create
tables. During FastAPI startup, `SQLModel.metadata.create_all(engine)` initializes
the `sku`, `stockentry` and `stockexit` tables. Connection or schema creation
errors prevent startup. Without `DATABASE_URL`, initialization is skipped with a
warning and TinyDB authentication remains available, but requesting a SQL session
raises a configuration error.

`SKU` stores the client, category and warehouse alongside its name and code;
`current_stock` is not a stored column. Both movement tables reference `sku.id`,
record the warehouse and a UTC creation timestamp, and require positive quantities.
Their `user_uuid` is a string referencing TinyDB, without an SQL foreign key.
Entries include a reference; exits include an exit type and nullable tracking
number. Database constraints require nonblank tracking for dispatches and null
tracking for losses. `create_all` creates missing tables but does not migrate
existing tables. Stock calculations and insufficient-stock checks belong to the
inventory endpoints, not to these ORM definitions.

Inject `app.db.DBSession` in inventory route parameters. It is an alias for
`Annotated[Session, Depends(get_db)]`. Each request gets its own session, closed
on success or failure. Routes must explicitly call `session.commit()` for writes;
uncommitted work is rolled back when the session closes.

## Inventory schemas

`app/schemas.py` defines independent Pydantic classes: `SKUCreateRequest`,
`SKUResponse`, `StockEntryCreateRequest`, `StockEntryResponse`,
`StockExitCreateRequest` and `StockExitResponse`. They are not SQLModel tables.
Requests reject unknown fields. IDs, timestamps and movement `user_uuid` are
response-only fields; routes obtain the user identifier from TinyDB authentication.

`SKUResponse.current_stock` is required and is never accepted in a creation
request or stored in the ORM. Route logic must calculate entries minus exits
filtered by SKU and warehouse, then pass that value with the SKU fields to
`SKUResponse.model_validate`. Movement responses support ORM objects through
`from_attributes=True`. The schemas validate categories, warehouses, positive
integer quantities and the dispatch/loss tracking rules before persistence.

## Inventory routes

`app/routers/inventory.py` is registered in the main application under
`/inventory`. All six routes require a Bearer token from TinyDB authentication,
including GETs, following the service's existing access policy. Any active
authenticated user can read and write inventory.

| Method | Path | Response |
| --- | --- | --- |
| GET | `/inventory/products` | SKU list with calculated stock |
| POST | `/inventory/products` | Created SKU, HTTP 201 |
| GET | `/inventory/products/{id}` | SKU with calculated stock |
| POST | `/inventory/orders/inbound` | Created StockEntry, HTTP 201 |
| POST | `/inventory/orders/outbound` | Created StockExit, HTTP 201 |
| GET | `/inventory/orders` | Chronological movement list with product data |

Each SKU represents one warehouse. Responses calculate stock using only that
SKU's movements in its warehouse, not a total across warehouses. Movement
requests must match the SKU's warehouse (HTTP 400); missing SKUs return HTTP 404.
Outbound requests exceeding available stock return HTTP 400 before any write,
with `Insufficient stock for SKU '{sku}'. Available: {available}, requested: {quantity}.`
Invalid request fields or tracking combinations return HTTP 422.

Inbound and outbound operations lock the SKU row with PostgreSQL `FOR UPDATE`
until commit or rollback, serializing stock changes for that SKU. The lock is
not implemented by SQLite, which is used for isolated tests.

`GET /inventory/orders` returns both movement types with `order_type` (`inbound`
or `outbound`), all movement fields including `user_uuid`, and a nested `product`
of type `SKUResponse`. Its `current_stock` is the current stock, not the stock at
the time of the movement. IDs are unique within each movement table; use
`order_type` together with `id` to identify an order.