# Analisis de duplicacion entre frontends

## Alcance

Se revisaron el sitio corporativo y las interfaces del backoffice de proveedores e incidencias. Estan implementados con HTML, CSS y JavaScript plano; no existe una base React ni un modulo compartido de UI, por lo que las abstracciones propuestas son utilidades ES modules o componentes web, no Custom Hooks de React.

## 1. Ciclo de envio de formularios

**Donde aparece:** el submit del formulario publico en [uis/validation.js](uis/validation.js#L267) deshabilita el boton, cambia su etiqueta, espera la peticion, presenta el resultado y restaura el boton. El alta de incidencias en [incidents.js](uis/application/app/incidents/incidents.js#L142) repite ese ciclo y tambien administra `aria-busy` y el spinner. El alta de proveedores en [suppliers.js](uis/application/app/suppliers/suppliers.js) presenta un estado de envio, ejecuta una peticion y resuelve los estados de exito y error.

**Por que es candidato:** los tres flujos necesitan mantener sincronizados el estado ocupado, el control de envio y el feedback aunque falle la peticion. Cada pagina lo implementa por separado, asi que cambios de accesibilidad o restauracion del boton pueden divergir.

**Abstraccion propuesta:** crear una utilidad compartida, por ejemplo `runFormSubmission({ form, button, label, spinner, pendingText }, submit)`, que aplique y restaure el estado ocupado con `try/finally`. Cada pagina conservaria su validacion, payload, texto de exito/error y acciones posteriores al exito. Asi la utilidad no acopla las reglas comerciales de la solicitud corporativa con las del backoffice.

## 2. Feedback accesible de formularios

**Donde aparece:** el formulario publico actualiza el mensaje de exito/error desde [uis/validation.js](uis/validation.js), en el nodo `formSuccess` de [uis/application.html](uis/application.html#L244). El backoffice hace lo mismo mediante `setFeedback` en [suppliers.js](uis/application/app/suppliers/suppliers.js#L43) y [incidents.js](uis/application/app/incidents/incidents.js#L31), y `setPageFeedback` en [panel.js](uis/application/app/incidents/panel.js#L124). Sus nodos usan variantes de `role="status"` y `aria-live="polite"` en [incidents/index.html](uis/application/app/incidents/index.html#L132) y [suppliers/index.html](uis/application/app/suppliers/index.html#L147).

**Por que es candidato:** todas estas vistas comunican estados asincronos al usuario y a tecnologias de asistencia, pero el marcado, las clases de tono y la forma de actualizar el texto estan distribuidos entre HTML y scripts.

**Abstraccion propuesta:** un helper `setFeedback(node, { message, tone })` compartido que conserve el nodo persistente, aplique una clase de estado consistente y limpie el mensaje anterior. Se puede complementar con un fragmento HTML o un Web Component de estado accesible. Los estilos de cada frontend seguirian siendo locales para conservar su identidad visual.

**Refactor aplicado:** se implemento `<trackflow-feedback>` en [uis/shared/form-feedback.js](uis/shared/form-feedback.js) y se reutilizo en el formulario corporativo, el alta de proveedores y el alta de incidencias. Mantiene el nodo accesible (`role="status"`, `aria-live="polite"`) y expone `setMessage()`; el CSS permanece local y cada vista conserva sus reglas de negocio.

## 3. Configuracion y transporte de la API en backoffice

**Donde aparece:** `getApiBase()` se repite en [suppliers.js](uis/application/app/suppliers/suppliers.js#L38), [incidents.js](uis/application/app/incidents/incidents.js#L86) y [panel.js](uis/application/app/incidents/panel.js#L119). La cabecera JSON/JWT se construye en [panel.js](uis/application/app/incidents/panel.js#L143); [suppliers.js](uis/application/app/suppliers/suppliers.js#L91) y `submitIncident` implementan sus propias llamadas `fetch` y tratamiento de respuesta.

**Por que es candidato:** el origen, el token bearer, la serializacion JSON y la interpretacion de errores son preocupaciones transversales. Mantenerlas en cada vista facilita que cambien de manera inconsistente los codigos de error, los headers o el tratamiento de respuestas `204`.

**Abstraccion propuesta:** un modulo `uis/application/shared/api-client.js` que exponga `createApiClient({ getBaseUrl, getToken })` y metodos `get/post/patch`. El cliente centralizaria headers, JSON, errores y respuestas vacias; `getBaseUrl` permite conservar la URL configurable de proveedores y el valor guardado en `localStorage` de las vistas de incidencias. Las paginas seguirian decidiendo que mostrar y como recuperar su estado.

## Revision asistida de rendimiento y calidad

### Skills y alcance

Se instalaron en `.agents/skills/` las skills `core-web-vitals` (addyosmani/web-quality-skills), `performance` (addyosmani/web-quality-skills, referencia de medicion) y `web-perf` (cloudflare/skills). Se aplicaron a:

- Sitio corporativo: `uis/index.html` y `uis/application.html`, escritorio y movil.
- Backoffice: `uis/application/app/suppliers/index.html`, escritorio.

Se usaron los informes Lighthouse 13.5.0 ya guardados en `audit/before/`, con Chromium 153 y una corrida de laboratorio por URL y modo. No habia herramientas de traza de DevTools disponibles. CrUX no ofrece datos para estas URL locales; INP de campo queda sin medir. TBT es solo la metrica de laboratorio disponible, no un sustituto de INP. Los valores TTFB de 0 ms corresponden al servidor estatico local, no a produccion.

### Puntuaciones iniciales de Lighthouse

| Frontend / pagina | Modo | Performance | Accesibilidad | Buenas practicas | SEO |
| --- | --- | ---: | ---: | ---: | ---: |
| Corporativo / inicio | Escritorio | 100 | 100 | 100 | 91 |
| Corporativo / inicio | Movil | 96 | 100 | 100 | 91 |
| Corporativo / formulario | Escritorio | 100 | 96 | 100 | 90 |
| Corporativo / formulario | Movil | 95 | 96 | 100 | 90 |
| Backoffice / proveedores | Escritorio | 100 | 100 | 96 | 90 |

Los informes fuente y las capturas iniciales estan en [audit/before/README.md](audit/before/README.md).

### Core Web Vitals de laboratorio

| Frontend / pagina | Modo | LCP | CLS | TBT | INP / CrUX |
| --- | --- | ---: | ---: | ---: | --- |
| Corporativo / inicio | Escritorio | 0.6 s | 0 | 0 ms | No disponible |
| Corporativo / inicio | Movil | 2.2 s | 0 | 0 ms | No disponible |
| Corporativo / formulario | Escritorio | 0.6 s | 0 | 0 ms | No disponible |
| Corporativo / formulario | Movil | 2.3 s | 0 | 0 ms | No disponible |
| Backoffice / proveedores | Escritorio | 0.7 s | 0.034 | 0 ms | No disponible |

Los LCP observados estan bajo 2.5 s y los CLS bajo 0.1, pero son mediciones sinteticas de una sola corrida, no percentiles de usuarios. Las puntuaciones de las cuatro categorias por URL/modo permanecen en [el resumen del baseline](audit/before/README.md).

### Causas raiz

1. **Bloqueo del render corporativo:** [uis/index.html](uis/index.html#L7) y [uis/application.html](uis/application.html#L7) ejecutaban Tailwind desde un script CDN en el `head`. El navegador esperaba la descarga y ejecucion del runtime remoto para generar estilos antes de continuar el parseo; era el recurso con mayor ahorro estimado en movil.
2. **Validacion del formulario en ruta critica:** [uis/application.html](uis/application.html#L251) incluye `validation.js` como script clasico sin `defer`. Lighthouse lo identifica entre los recursos bloqueantes; el codigo se necesita para interaccion, pero no para pintar el contenido inicial.
3. **Fuente remota de proveedores:** [suppliers/index.html](uis/application/app/suppliers/index.html#L10) referencia el CSS de Google Fonts como stylesheet. La descarga de la hoja tipografica remota bloquea el render y retrasa el texto; los preconnect existentes no eliminan esa espera.
4. **Nombre accesible no alineado:** el enlace en [uis/index.html](uis/index.html#L102) declara un `aria-label` que reemplaza el nombre accesible calculado desde el texto visible, y el label no contiene ese texto. Lighthouse marca una discrepancia que puede desorientar a usuarios de lector de pantalla.
5. **Contraste insuficiente del contador:** [uis/application.html](uis/application.html#L209) aplica `text-slate-500` a `#commentsCounter`. En el fondo calculado Lighthouse obtiene 4.03:1, por debajo del 4.5:1 requerido para texto normal WCAG AA.
6. **Metadescripciones ausentes:** inicio, formulario y proveedores no declaran `meta name="description"` en sus `<head>`. Falta el campo que los buscadores usan como resumen de pagina.
7. **Error de red del backoffice:** `ERR_CONNECTION_REFUSED` para `127.0.0.1:8000/suppliers` se debe a que el API no estaba levantado durante la medicion local; no prueba un defecto del codigo frontend.

### Correcciones identificadas

1. **Prioridad alta - quitar Tailwind CDN de la ruta critica.** [uis/index.html](uis/index.html#L7) y [uis/application.html](uis/application.html#L7) cargan `https://cdn.tailwindcss.com/3.4.17` como script en el `head` (~127 KB). Lighthouse estima 1,490 ms de ahorro de bloqueo en inicio movil y 1,470 ms en formulario movil (320-330 ms en escritorio). Compilar Tailwind a CSS estatico/minificado para produccion y medir de nuevo con las mismas condiciones.

2. **Prioridad media - diferir la validacion del formulario y revisar los recursos que bloquean.** [uis/application.html](uis/application.html#L251) carga `validation.js` sin `defer`; Lighthouse le atribuye 452 ms de bloqueo estimado en movil y 81 ms en escritorio, ademas del CSS local. Probar `defer` o un modulo y conservar la validacion/reporte de errores; repetir el audit para comprobar que mejora el render inicial.

3. **Prioridad media - reducir dependencias remotas del backoffice.** [suppliers/index.html](uis/application/app/suppliers/index.html#L10) bloquea el render esperando el CSS de Google Fonts (1.3 KB, 230 ms estimados); Lighthouse estima 460 ms totales para el insight de bloqueo en escritorio. Considerar alojar localmente solo los pesos usados o usar una pila local; [suppliers.js](uis/application/app/suppliers/suppliers.js) aporta otros 83 ms estimados y puede cargarse de forma diferida tras verificar su inicializacion.

4. **Corregir dos hallazgos de accesibilidad confirmados por Lighthouse.** En [uis/index.html](uis/index.html#L102), el enlace visible dice "Ver directorio de proveedores" pero su `aria-label` no incluye ese texto; eliminar el `aria-label` redundante o hacer que coincida con el texto visible. En [uis/application.html](uis/application.html#L209), `#commentsCounter` tiene contraste 4.03:1 frente al minimo AA 4.5:1; ajustar el color contra el fondo realmente calculado y volver a comprobar escritorio y movil.

5. **Agregar metadescripciones unicas.** Lighthouse marca `meta-description` ausente en inicio, formulario y proveedores. Agregar un `<meta name="description">` especifico a cada documento; verificar que describa esa pagina y no reutilizar una misma descripcion generica.

### Limitaciones y descartes

- `unused-javascript` estima 38-39 KB recuperables, pero 0 ms de ahorro; no se recomienda podar codigo por este dato aislado.
- El informe de proveedores registra `ERR_CONNECTION_REFUSED` en `http://127.0.0.1:8000/suppliers`: el API no estaba iniciado durante la auditoria estatica. No se considera una correccion del frontend; repetir con el API activo para revisar tabla, estados y errores funcionales.
- Las recomendaciones secundarias anteriores (contraste, meta descriptions y carga del API) siguen pendientes; no se mezclaron con la correccion prioritaria de rendimiento. Deben validarse en commits separados. El impacto en usuarios requiere datos CrUX/RUM posteriores.

## Correccion aplicada: Tailwind local

Se reemplazo el script CDN de Tailwind 3.4.17, que bloqueaba el render, por CSS estatico compilado con Tailwind CLI 4.3.0. El bundle generado pesa 26.4 KB; `npm audit --prefix uis` termina con 0 vulnerabilidades. Se revisaron las capturas Lighthouse posteriores y el diseño movil conserva sus utilidades y layout.

| Pagina | Modo | Performance antes/despues | LCP antes | LCP despues | Ahorro render-blocking antes/despues |
| --- | --- | ---: | ---: | ---: | ---: |
| Inicio | Movil | 96 / 100 | 2,239 ms (2,201-2,240) | 904 ms (901-904) | 1,490 / 250 ms |
| Formulario | Movil | 95 / 100 | 2,343 ms (2,343-2,366) | 1,253 ms (1,249-1,258) | 1,490 / 410 ms |
| Inicio | Escritorio | 100 / 100 | 0.6 s | 0.2 s | 320 / 80 ms |
| Formulario | Escritorio | 100 / 100 | 0.6 s | 0.3 s | 330 / 70 ms |

Las cifras moviles son medianas de tres corridas Lighthouse 13.5.0 con Chromium 153, misma URL local, formulario publico y emulacion movil. Los valores entre parentesis son rangos observados. Desktop tiene una corrida antes y una despues. CLS y TBT permanecen en cero; INP/CrUX no estan disponibles para localhost. El ahorro de render-blocking es una estimacion de Lighthouse, no tiempo real de usuario.
