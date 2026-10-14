# Medicion inicial Lighthouse

Fecha: 2026-10-14
Lighthouse: 13.5.0
Chromium: 153.0.8010.0
Origen: servidor HTTP local (`python3 -m http.server`, puerto 4173)
Rutas: `/index.html`, `/application.html` y `/application/app/suppliers/index.html`

| Area | Pagina | Modo | Performance | Accesibilidad | Buenas practicas | SEO | LCP | CLS | TBT | TTFB |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Corporativo | Inicio | Escritorio | 100 | 100 | 100 | 91 | 0.6 s | 0 | 0 ms | 0 ms |
| Corporativo | Inicio | Movil | 96 | 100 | 100 | 91 | 2.2 s | 0 | 0 ms | 0 ms |
| Corporativo | Formulario | Escritorio | 100 | 96 | 100 | 90 | 0.6 s | 0 | 0 ms | 0 ms |
| Corporativo | Formulario | Movil | 95 | 96 | 100 | 90 | 2.3 s | 0 | 0 ms | 0 ms |
| Backoffice | Directorio de proveedores | Escritorio | 100 | 100 | 96 | 90 | 0.7 s | 0.034 | 0 ms | 0 ms |

Cada medicion tiene un informe HTML y su fuente JSON con el detalle completo. Las capturas muestran el resumen del informe de escritorio de inicio corporativo y del directorio de proveedores:

Para las comparaciones before/after se guardan dos corridas moviles adicionales por cada pagina. La mediana y rango del LCP inicial fueron 2,239 ms (2,201-2,240) para inicio y 2,343 ms (2,343-2,366) para el formulario; las cuatro muestras adicionales estan enlazadas al final.

- [Informe corporativo: inicio en escritorio](corporate-home-desktop.report.html)
- [Datos JSON: inicio en escritorio](corporate-home-desktop.report.json)
- [Informe corporativo: inicio en movil](corporate-home-mobile.report.html)
- [Datos JSON: inicio en movil](corporate-home-mobile.report.json)
- [Informe corporativo: formulario en escritorio](corporate-application-desktop.report.html)
- [Datos JSON: formulario en escritorio](corporate-application-desktop.report.json)
- [Informe corporativo: formulario en movil](corporate-application-mobile.report.html)
- [Datos JSON: formulario en movil](corporate-application-mobile.report.json)
- [Informe backoffice: proveedores en escritorio](backoffice-suppliers-desktop.report.html)
- [Datos JSON: proveedores en escritorio](backoffice-suppliers-desktop.report.json)
- [Captura del informe corporativo](corporate-report.png)
- [Captura del informe de backoffice](backoffice-report.png)
- [Inicio movil, repeticion 2](corporate-home-mobile-repeat-2.json)
- [Inicio movil, repeticion 3](corporate-home-mobile-repeat-3.json)
- [Formulario movil, repeticion 2](corporate-application-mobile-repeat-2.json)
- [Formulario movil, repeticion 3](corporate-application-mobile-repeat-3.json)

El directorio de proveedores es la vista de backoffice con mas elementos visibles (filtros, tabla y formulario). Se sirvio como pagina estatica; no se inicio el API, por lo que la informacion dinamica de proveedores no esta incluida. TBT es una metrica de laboratorio y no sustituye el INP de campo. TTFB corresponde al documento raiz segun Lighthouse. La home corporativa se midio desde la raiz `uis/`, donde el logo devuelve 200. Los informes JSON incluyen la fecha, URL final, configuracion y todas las categorias. La categoria experimental `agentic-browsing` de Lighthouse 13.5.0 se excluye de la tabla para mantener las cuatro puntuaciones solicitadas.
