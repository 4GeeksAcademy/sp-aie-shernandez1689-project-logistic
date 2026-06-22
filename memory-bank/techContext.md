# Tech Context

## Stack tecnologico
- Monorepo modular para organizar UI, documentacion, skills y recursos de soporte.
- Frontend web con HTML, CSS y JavaScript para la landing y formulario de contacto.
- Tailwind CSS como requisito explicito de implementacion de interfaz.
- Validacion de formulario en cliente con reglas, mensajes de error y simulacion de envio exitoso.
- SEO on-page y datos estructurados con Schema.org (Organization) en la landing.

## Decisiones de arquitectura tomadas
- Se define una landing corporativa con secciones fijas en orden obligatorio: Header, Hero, Services, Coverage, Why TrackFlow, Contact y Footer.
- Se separa el formulario de solicitud de informacion como flujo principal de conversion B2B.
- Se prioriza base language unico para todo el flujo y se deja bilingue como mejora opcional.
- Se implementan validaciones deterministicas por campo con mensajes de error exactamente definidos por contexto.
- Se incluye warning de negocio para volumen 0-100 envios/mes por posible desajuste de servicio.

## Restricciones tecnicas
- El formulario debe capturar todos los campos obligatorios definidos en CONTEXT.md.
- Deben aplicarse validaciones especificas: nombre empresa, contacto, email, telefono, website, servicios, comentarios y privacidad.
- Deben mostrarse los mensajes de error esperados con el texto indicado en CONTEXT.md.
- Debe mostrarse mensaje de exito de confirmacion cuando el formulario valida correctamente.
- La solucion debe ser responsive, accesible y SEO optimized.
- Se debe incluir markup Schema.org Organization con los datos de TrackFlow.
