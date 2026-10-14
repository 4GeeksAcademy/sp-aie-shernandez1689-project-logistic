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
tables. Without `DATABASE_URL`, TinyDB authentication remains available, but
requesting a SQL session raises a configuration error.

Inject `app.db.DBSession` in inventory route parameters. It is an alias for
`Annotated[Session, Depends(get_db)]`. Each request gets its own session, closed
on success or failure. Routes must explicitly call `session.commit()` for writes;
uncommitted work is rolled back when the session closes.