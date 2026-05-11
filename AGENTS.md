# AGENTS (dinerance-api)

## Rol y limites
- API REST en FastAPI para finanzas personales.
- Repo independiente: commitea desde `dinerance-api/`.
- Consumidor principal: `dinerance-dashboard`.

## Comandos reales (fuente de verdad)
- Instalar deps: `uv sync`
- Migrar esquema: `uv run alembic upgrade head`
- Correr API local: `uv run uvicorn app.main:app --reload`
- Tests: `uv run pytest`
- Test puntual: `uv run pytest tests/test_users_me.py::test_<nombre>`

## Variables y autenticacion (no obvio)
- `DATABASE_URL` es obligatorio en runtime (falla al importar si no existe).
- Para Supabase en Postgres remoto normalmente necesitas SSL:
  - `DB_SSLMODE=require` (o `sslmode` en la URL).
- Auth recomendado: `SUPABASE_URL` (JWKS auto en `/auth/v1/.well-known/jwks.json`).
- Fallback legacy: `SUPABASE_JWT_SECRET` (+ `JWT_ALGORITHMS`, etc.).

## Migrations y datos
- El esquema se maneja con Alembic (`alembic/versions/*`).
- Evita depender de `AUTO_CREATE_TABLES`; solo es fallback local en `app/main.py`.

## Tests: quirk importante
- `tests/conftest.py` usa SQLite en memoria y override de dependencias/auth.
- Aun asi, exporta `DATABASE_URL` antes de `pytest` porque `app.database.connection` lo exige al importar.

## Entrypoints para cambios
- App/routers: `app/main.py`
- Config CORS/env: `app/core/settings.py`
- Auth/JWT: `app/core/auth.py`
- Sesion DB: `app/database/connection.py`

## Conexion con el dashboard
- El frontend envia `Bearer` token y consume endpoints de este repo.
- Si falla desde browser, revisar primero `CORS_ORIGINS` y `NEXT_PUBLIC_API_BASE_URL` del dashboard.
