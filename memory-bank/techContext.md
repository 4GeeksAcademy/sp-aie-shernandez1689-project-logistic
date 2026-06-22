# Tech Context

## Stack tecnologico
- Monorepo con estructura modular por dominios (agents, services, skills, uis, infra, packages).
- Frontend base en HTML, CSS y JavaScript para interfaces ligeras y validaciones en cliente.
- Paquetes compartidos en TypeScript dentro de packages/shared para tipos y contratos reutilizables.
- Scripts de analisis y soporte en Python para procesamiento de datos y tareas operativas.
- Documentacion bilingue (README.md y README.es.md) para alineacion de equipos tecnicos y no tecnicos.

## Decisiones de arquitectura tomadas
- Se adopta un enfoque de monorepo para centralizar codigo, documentacion y flujos de trabajo.
- Se separan responsabilidades por carpetas de dominio para reducir acoplamiento y facilitar mantenimiento.
- Se promueve reutilizacion mediante paquetes compartidos y definicion explicita de tipos comunes.
- Se mantiene una capa de UI simple y desacoplada para iterar rapido en experiencias y validaciones.
- Se estructura el trabajo para habilitar integraciones futuras con automatizaciones, agentes y servicios.

## Restricciones tecnicas
- Debe mantenerse compatibilidad entre multiples modulos sin romper contratos compartidos.
- Cambios en tipos comunes requieren coordinacion para evitar regresiones en consumidores del monorepo.
- Se prioriza simplicidad de herramientas base para minimizar friccion de onboarding.
- La documentacion debe actualizarse en ambos idiomas para conservar coherencia del proyecto.
- El crecimiento de componentes debe respetar la organizacion existente para evitar duplicidad y deuda tecnica.
