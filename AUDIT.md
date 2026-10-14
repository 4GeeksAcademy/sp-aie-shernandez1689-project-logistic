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
