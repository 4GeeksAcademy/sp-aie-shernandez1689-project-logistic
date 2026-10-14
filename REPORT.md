# Reporte de correcciones Lighthouse

## Alcance y condiciones

Se comparan las mediciones iniciales y finales de Lighthouse 13.5.0 con Chromium 153.0.8010.0 en el servidor estatico local, en las mismas rutas. Se auditaron la home y el formulario corporativos en escritorio y movil, y el directorio de proveedores del backoffice en escritorio. Para movil hay tres corridas por URL; la tabla muestra la mediana de las puntuaciones y KPI. Desktop y backoffice tienen una corrida antes y despues.

No hay CrUX/RUM ni INP de campo para `localhost`. TBT es una metrica de laboratorio, no un reemplazo de INP. El API del backoffice no estaba activo, asi que la vista se midio con datos estaticos y Lighthouse registro un error de conexion al endpoint.

## Correcciones aplicadas

1. **Tailwind CDN reemplazado por CSS local.** Se sustituyo el script remoto que bloqueaba el parseo por un bundle minificado de 26.4 KB generado con Tailwind CLI 4.3.0. El frontend conserva Tailwind, sin reescribir la arquitectura. Se fijaron las dependencias y `npm audit --prefix uis` reporta cero vulnerabilidades.
2. **Feedback reutilizable extraido.** Se implemento `<trackflow-feedback>` en `uis/shared/form-feedback.js` y se uso en el formulario corporativo, el alta de proveedores y el alta de incidencias. Mantiene el mismo nodo para el feedback, asegura `role="status"`/`aria-live="polite"` y ofrece `setMessage()`. Un smoke test en Chromium verifico el registro, texto, rol, live region y display en las tres paginas.

## Puntuaciones antes y despues

| Frontend / pagina | Modo | Performance antes/despues | Accesibilidad antes/despues | Buenas practicas antes/despues | SEO antes/despues |
| --- | --- | ---: | ---: | ---: | ---: |
| Corporativo / inicio | Escritorio | 100 / 100 | 100 / 100 | 100 / 100 | 91 / 91 |
| Corporativo / inicio | Movil | 96 / 100 | 100 / 100 | 100 / 100 | 91 / 91 |
| Corporativo / formulario | Escritorio | 100 / 100 | 96 / 96 | 100 / 100 | 90 / 90 |
| Corporativo / formulario | Movil | 95 / 100 | 96 / 96 | 100 / 100 | 90 / 90 |
| Backoffice / proveedores | Escritorio | 100 / 100 | 100 / 100 | 96 / 96 | 90 / 90 |

## KPI y causa del impacto

| Pagina / modo | LCP antes | LCP despues | CLS antes/despues | TBT antes/despues | Ahorro render-blocking antes/despues |
| --- | ---: | ---: | ---: | ---: | ---: |
| Inicio / movil | 2,239 ms (2,201-2,240) | 903 ms (902-905) | 0 / 0 | 0 / 0 ms | 1,490 / 290 ms |
| Formulario / movil | 2,343 ms (2,343-2,366) | 1,253 ms (1,247-1,307) | 0 / 0 | 0 / 0 ms | 1,490 / 400 ms |
| Inicio / escritorio | 0.6 s | 0.2 s | 0 / 0 | 0 / 0 ms | 320 / 70 ms |
| Formulario / escritorio | 0.6 s | 0.3 s | 0 / 0 | 0 / 0 ms | 330 / 110 ms |
| Proveedores / escritorio | 0.7 s | 0.7 s | 0.034 / 0.034 | 0 / 0 ms | 460 / 490 ms |

**Mayor impacto:** compilar Tailwind localmente. El CDN era un script de aproximadamente 127 KB en el `head`; Lighthouse estimo que su bloqueo aportaba unos 1.49 s en movil. Tras reemplazarlo, el LCP mediano bajo 1.34 s en home y 1.09 s en formulario, y Performance subio 4 y 5 puntos respectivamente. El ahorro de bloqueo estimado bajo alrededor de 80% en home movil y 73% en formulario movil. Son datos sinteticos de laboratorio, no una promesa de mejora equivalente en usuarios reales.

El componente de feedback es una mejora de consistencia y accesibilidad del estado; no se planteo para cambiar las puntuaciones de Lighthouse y los scores finales se mantuvieron respecto a la corrida posterior al CSS local. CLS y TBT permanecieron en cero. El score del backoffice no cambio; su insight de bloqueo fluctuo 30 ms entre corridas y el error de red se explica porque el API no estaba levantado.

## Evidencia

- Baseline y capturas iniciales: [audit/before/README.md](audit/before/README.md), [captura corporativa](audit/before/corporate-report.png), [captura backoffice](audit/before/backoffice-report.png).
- Medicion final y capturas: [audit/after/README.md](audit/after/README.md), [captura corporativa](audit/after/corporate-report.png), [captura backoffice](audit/after/backoffice-report.png).
- Causas raiz detalladas, problemas secundarios y analisis de refactor: [AUDIT.md](AUDIT.md).
