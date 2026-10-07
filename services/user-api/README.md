# User API (FastAPI + TinyDB)

This service implements the first backend milestone for user model and CRUD.

## Features

- User model in TinyDB with fields: `id`, `email`, `hashed_password`, `is_active`, `role`, `created_at`
- Role validation restricted to: `admin`, `manager`, `user`
- Default role for `POST /users`: `user`
- Service layer functions for create/get by id/get by email/update/delete
- Profile model (1:1 with user by `user_id`) with fields: `id`, `user_id`, `name`, `phone`, `address`
- User and Profile are stored only in TinyDB (no SQLModel/Supabase tables for auth domain)
- Passwords are always hashed using `passlib` with `bcrypt` (never plain text)
- Authentication is JWT stateless only (no sessions/cookies)
- REST endpoints under `/users`
- REST endpoints under `/profiles`
- Authentication endpoints under `/auth` with JWT
- Commercial application endpoint under `/applications`
- Optional profile creation (`name`, `phone`, `address`) when registering
- Sensitive PostgreSQL modules should reference TinyDB user identity via `user_uuid` only

## Run locally

```bash
cd services/user-api
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
uvicorn app.main:app --reload
```

## Auth model (JWT)

- `POST /auth/login` returns a JWT access token.
- Protected endpoints expect: `Authorization: Bearer <jwt_token>`
- `POST /users` is public.
- The API does not implement cookie/session authentication.
- Configure JWT using environment variables in `.env`:
	- `JWT_SECRET_KEY`
	- `ACCESS_TOKEN_EXPIRE_MINUTES`
- Optional CORS configuration for local frontend development:
	- `CORS_ALLOW_ORIGINS` as a comma-separated list of allowed origins

## Endpoints

- `POST /users`
- `GET /users` (protected)
- `GET /users/{user_id}` (protected)
- `PUT /users/{user_id}` (protected, self or admin; role change only admin)
- `DELETE /users/{user_id}` (protected, self or admin; also deletes profile)
- `GET /profiles/me` (protected)
- `PUT /profiles/me` (protected, owner only)
- `GET /profiles` (protected, admin)
- `GET /profiles/user/{user_id}` (protected, owner or admin)
- `GET /profiles/{profile_id}` (protected, admin)
- `PUT /profiles/user/{user_id}` (protected, admin)
- `DELETE /profiles/user/{user_id}` (protected, admin)
- `POST /auth/login`
- `GET /auth/me` (protected)
- `POST /applications`
