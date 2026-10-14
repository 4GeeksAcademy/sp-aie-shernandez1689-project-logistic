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

### Core Web Vitals de laboratorio

| Frontend / pagina | Modo | LCP | CLS | TBT | INP / CrUX |
| --- | --- | ---: | ---: | ---: | --- |
| Corporativo / inicio | Escritorio | 0.6 s | 0 | 0 ms | No disponible |
| Corporativo / inicio | Movil | 2.2 s | 0 | 0 ms | No disponible |
| Corporativo / formulario | Escritorio | 0.6 s | 0 | 0 ms | No disponible |
| Corporativo / formulario | Movil | 2.3 s | 0 | 0 ms | No disponible |
| Backoffice / proveedores | Escritorio | 0.7 s | 0.034 | 0 ms | No disponible |

Los LCP observados estan bajo 2.5 s y los CLS bajo 0.1, pero son mediciones sinteticas de una sola corrida, no percentiles de usuarios. Las puntuaciones de las cuatro categorias por URL/modo permanecen en [el resumen del baseline](audit/before/README.md).

### Correcciones identificadas

1. **Prioridad alta - quitar Tailwind CDN de la ruta critica.** [uis/index.html](uis/index.html#L7) y [uis/application.html](uis/application.html#L7) cargan `https://cdn.tailwindcss.com/3.4.17` como script en el `head` (~127 KB). Lighthouse estima 1,490 ms de ahorro de bloqueo en inicio movil y 1,470 ms en formulario movil (320-330 ms en escritorio). Compilar Tailwind a CSS estatico/minificado para produccion y medir de nuevo con las mismas condiciones.

2. **Prioridad media - diferir la validacion del formulario y revisar los recursos que bloquean.** [uis/application.html](uis/application.html#L250) carga `validation.js` sin `defer`; Lighthouse le atribuye 452 ms de bloqueo estimado en movil y 81 ms en escritorio, ademas del CSS local. Probar `defer` o un modulo y conservar la validacion/reporte de errores; repetir el audit para comprobar que mejora el render inicial.

3. **Prioridad media - reducir dependencias remotas del backoffice.** [suppliers/index.html](uis/application/app/suppliers/index.html#L10) bloquea el render esperando el CSS de Google Fonts (1.3 KB, 230 ms estimados); Lighthouse estima 460 ms totales para el insight de bloqueo en escritorio. Considerar alojar localmente solo los pesos usados o usar una pila local; [suppliers.js](uis/application/app/suppliers/suppliers.js) aporta otros 83 ms estimados y puede cargarse de forma diferida tras verificar su inicializacion.

4. **Corregir dos hallazgos de accesibilidad confirmados por Lighthouse.** En [uis/index.html](uis/index.html#L102), el enlace visible dice "Ver directorio de proveedores" pero su `aria-label` no incluye ese texto; eliminar el `aria-label` redundante o hacer que coincida con el texto visible. En [uis/application.html](uis/application.html#L209), `#commentsCounter` tiene contraste 4.03:1 frente al minimo AA 4.5:1; ajustar el color contra el fondo realmente calculado y volver a comprobar escritorio y movil.

5. **Agregar metadescripciones unicas.** Lighthouse marca `meta-description` ausente en inicio, formulario y proveedores. Agregar un `<meta name="description">` especifico a cada documento; verificar que describa esa pagina y no reutilizar una misma descripcion generica.

### Limitaciones y descartes

- `unused-javascript` estima 38-39 KB recuperables, pero 0 ms de ahorro; no se recomienda podar codigo por este dato aislado.
- El informe de proveedores registra `ERR_CONNECTION_REFUSED` en `http://127.0.0.1:8000/suppliers`: el API no estaba iniciado durante la auditoria estatica. No se considera una correccion del frontend; repetir con el API activo para revisar tabla, estados y errores funcionales.
- No se aplicaron estas correcciones. Son recomendaciones del analisis para priorizar y validar en commits separados, con la misma ruta/modo y una nueva medicion de laboratorio; el impacto en usuarios requiere datos CrUX/RUM posteriores.
