## Diseño de la página principal

La página principal de TrackFlow presenta sus servicios de logística de última milla y gestión de almacenes a empresas que buscan coordinar entregas y operaciones. La acción principal es contactar con la empresa mediante el formulario.

### Identidad visual

- **Base:** fondos azul marino oscuro, texto blanco y tonos gris pizarra para el contenido secundario.
- **Acentos:** cian para la marca, enlaces y acciones principales; ámbar distingue el acceso al Data Lab.
- **Superficies:** paneles oscuros o translúcidos, bordes finos de bajo contraste, esquinas redondeadas y sombras discretas. El hero usa un degradado oscuro hacia cian.
- **Tipografía:** escala sans serif y jerarquía tipográfica de Tailwind; el titular del hero es el elemento de mayor peso visual.
- **Imagen de marca:** el logo SVG de TrackFlow aparece junto al titular y enlaza al formulario.

### Estructura y contenido

1. **Navegación:** marca y enlaces a Propuesta, Características, Experiencias, Contacto, Data Lab y Formulario.
2. **Hero:** promesa de logística inteligente, explicación breve, logo y botón «Ir al formulario», acompañado por el plazo de respuesta estimado.
3. **Propuesta de valor:** visibilidad de envíos, optimización automática de rutas y escalabilidad.
4. **Características y beneficios:** seguimiento de pedidos y rutas, panel para última milla y almacenes, alertas, reducción de costos y mejora de puntualidad y experiencia del cliente.
5. **Proceso y experiencias:** diagnóstico, integración y mejora continua, junto con dos testimonios de clientes.
6. **Pie de página:** descripción de TrackFlow, correo, teléfono, horario de soporte, cobertura en Madrid y Ciudad de México y acceso al formulario.

### Comportamiento adaptable

El contenido se centra en un ancho máximo de `max-w-6xl`, con márgenes internos que crecen en pantallas mayores. En móvil, la navegación puede ocupar varias líneas, el hero apila texto y propuesta, las secciones se muestran en una columna y el botón principal ocupa todo el ancho disponible. En escritorio, la navegación pasa a una fila; el hero se divide en dos columnas y las secciones de características, beneficios, proceso y experiencias usan dos columnas. Los puntos de adaptación corresponden a los breakpoints `sm`, `md` y `lg` de Tailwind.

### Navegación y accesibilidad

- Los enlaces de la navegación principal desplazan a las secciones identificadas en la misma página.
- El formulario y el Data Lab se abren desde `application.html` y `testing.html`, respectivamente.
- El logo tiene texto alternativo; la página utiliza regiones semánticas, etiquetas ARIA y una jerarquía de encabezados.
- Los enlaces cambian de color al pasar el puntero para indicar interacción.
- Los metadatos estructurados describen a TrackFlow como organización, sus medios de contacto, idiomas de soporte y áreas de servicio.

### Implementación

La página está implementada en `uis/index.html` como HTML estático y carga Tailwind CSS desde CDN. El diseño utiliza clases de utilidad directamente en el marcado y el logo de `uis/logo-logistica.svg`. `uis/styles.css` contiene estilos para estados y mensajes de validación usados por los formularios; no define el layout principal. La portada no requiere un framework de JavaScript para su navegación o presentación.
