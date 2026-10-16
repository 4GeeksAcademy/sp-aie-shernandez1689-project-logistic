This is a [Next.js](https://nextjs.org) project bootstrapped with [`create-next-app`](https://nextjs.org/docs/app/api-reference/cli/create-next-app).

## Getting Started

First, run the development server:

```bash
npm run dev
# or
yarn dev
# or
pnpm dev
# or
bun dev
```

Open [http://localhost:3000](http://localhost:3000) with your browser to see the result.

You can start editing the page by modifying `app/page.tsx`. The page auto-updates as you edit the file.

This project uses [`next/font`](https://nextjs.org/docs/app/building-your-application/optimizing/fonts) to automatically optimize and load [Geist](https://vercel.com/font), a new font family for Vercel.

## Learn More

To learn more about Next.js, take a look at the following resources:

- [Next.js Documentation](https://nextjs.org/docs) - learn about Next.js features and API.
- [Learn Next.js](https://nextjs.org/learn) - an interactive Next.js tutorial.

You can check out [the Next.js GitHub repository](https://github.com/vercel/next.js) - your feedback and contributions are welcome!

## Deploy on Vercel

The easiest way to deploy your Next.js app is to use the [Vercel Platform](https://vercel.com/new?utm_medium=default-template&filter=next.js&utm_source=create-next-app&utm_campaign=create-next-app-readme) from the creators of Next.js.

Check out our [Next.js deployment documentation](https://nextjs.org/docs/app/building-your-application/deploying) for more details.

## Telemetría (TrackFlow)

El backoffice envía eventos de telemetría mediante `lib/telemetry.ts` — el punto de entrada único es `track(eventType, properties)`; ningún componente construye el envelope manualmente ni usa `fetch`/`axios` directos para telemetría.

### Variables de entorno

| Variable | Descripción |
| --- | --- |
| `NEXT_PUBLIC_TELEMETRY_ENDPOINT` | URL base de ingesta. El servicio envía lotes a `<endpoint>` (POST JSON `{"events": [...]}`). Sin valor, los lotes se descartan silenciosamente. En local con el stub de Fase 1: `http://127.0.0.1:8000/telemetry/events`. |

Copia `uis/talent-pipeline-tracker/.env.local.example` (ejemplo con la variable de telemetría y referencias a las del inventario/auth) a `.env.local`. Nota: `.env*` está en `.gitignore` del subproyecto, así que las claves no se versionan.

### Tests

```bash
node --test lib/telemetry.test.ts
```

Cobertura: envelope automático (eventId UUID v4, timestamp ISO, sessionId en Session Storage, userId del JWT `sub`), batch a los 20 eventos, debounce a los 10 s, `sendBeacon` en `visibilitychange`, reintentos con backoff exponencial (3), descarte en 4xx y sin endpoint.
