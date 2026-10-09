# Recuperacion y cambio de contrasena

## Configuracion de Resend

Configura estas variables en el entorno del servicio o en su archivo `.env` local:

```dotenv
RESEND_API_KEY=replace-with-your-resend-api-key
EMAIL_FROM=TrackFlow <security@example.com>
RESET_PASSWORD_URL=https://app.example.com/reset-password
```

`RESEND_API_KEY` es la API key de Resend. No la incluyas en Git, logs ni frontend.
`EMAIL_FROM` debe pertenecer a un dominio verificado en Resend.
`RESET_PASSWORD_URL` es la pagina del frontend que recibe el parametro `token` y
envia `{ token, new_password }` a `POST /auth/reset-password`. Esta implementacion
solo incluye el backend. Se exige HTTPS, salvo localhost/127.0.0.1 en desarrollo.
Las variables existentes `JWT_SECRET_KEY` y `ACCESS_TOKEN_EXPIRE_MINUTES` siguen siendo obligatorias.

## Endpoints

- `POST /auth/forgot-password`: `{ "email": "user@example.com" }`. Responde 200
  con el mismo mensaje exista o no la cuenta. Envia correo HTML adaptable a movil
  y texto plano. Un fallo de envio mantiene el 200, invalida ese token y genera
  un aviso sin incluir destinatario, token ni API key. Monitoriza estos avisos.
- `POST /auth/reset-password`: `{ "token": "...", "new_password": "..." }`.
  Responde 200 al actualizar o 400 ante un token invalido, expirado o utilizado.
- `POST /auth/change-password`: `{ "current_password": "...", "new_password": "..." }`
  y `Authorization: Bearer <access_token>`. Responde 200, 400 si la contrasena
  actual es incorrecta, o 401 si la sesion no es valida.

Las nuevas contrasenas requieren al menos 8 caracteres y como maximo 72 bytes
UTF-8, por el limite de bcrypt. Los cuerpos invalidos reciben 422 de FastAPI.
Los tokens duran 30 minutos, tienen firma separada de las sesiones y se guarda
solo su hash SHA-256. Una nueva solicitud sustituye la anterior. Cambiar la
contrasena, el email o desactivar la cuenta invalida el token pendiente.
Las sesiones existentes conservan su expiracion habitual.

TinyDB requiere un unico proceso/worker. La comprobacion, actualizacion de la
contrasena y consumo del token estan protegidos por un bloqueo dentro del proceso;
para varios workers se necesita una base de datos con transacciones.

## Pruebas

Desde `services/user-api`, con Python 3.12:

```bash
python -m pip install -r requirements.txt httpx
python -B -m unittest discover -s tests -v
```

Las pruebas utilizan una base de datos temporal y simulan Resend. No envian correo
real ni modifican los usuarios del proyecto. Para verificar la entrega real,
configura Resend y solicita un restablecimiento con una cuenta de prueba propia.