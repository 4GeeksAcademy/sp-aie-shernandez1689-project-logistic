# Medicion final Lighthouse

Fecha: 2026-10-14
Lighthouse: 13.5.0
Chromium: 153.0.8010.0
Origen: servidor HTTP local (`python3 -m http.server`, puerto 4173)

Las cuatro puntuaciones son Lighthouse Performance, Accessibility, Best Practices y SEO. En movil se hicieron tres corridas por URL; la tabla muestra la mediana de scores y KPI. Desktop y backoffice tienen una corrida cada uno.

| Frontend | Pagina | Modo | Performance | Accesibilidad | Buenas practicas | SEO | LCP | CLS | TBT |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Corporativo | Inicio | Escritorio | 100 | 100 | 100 | 91 | 0.2 s | 0 | 0 ms |
| Corporativo | Inicio | Movil | 100 | 100 | 100 | 91 | 903 ms (902-905) | 0 | 0 ms |
| Corporativo | Formulario | Escritorio | 100 | 96 | 100 | 90 | 0.3 s | 0 | 0 ms |
| Corporativo | Formulario | Movil | 100 | 96 | 100 | 90 | 1,253 ms (1,247-1,307) | 0 | 0 ms |
| Backoffice | Directorio de proveedores | Escritorio | 100 | 100 | 96 | 90 | 0.7 s | 0.034 | 0 ms |

## Evidencia

Cada ruta/modo tiene informe HTML/JSON. Las dos corridas moviles adicionales por pagina estan guardadas como JSON para calcular mediana y rango:

- [Inicio corporativo, escritorio](corporate-home-desktop.report.html) · [JSON](corporate-home-desktop.report.json)
- [Inicio corporativo, movil](corporate-home-mobile.report.html) · [JSON](corporate-home-mobile.report.json)
- [Inicio movil, repeticion 2](corporate-home-mobile-repeat-2.json)
- [Inicio movil, repeticion 3](corporate-home-mobile-repeat-3.json)
- [Formulario corporativo, escritorio](corporate-application-desktop.report.html) · [JSON](corporate-application-desktop.report.json)
- [Formulario corporativo, movil](corporate-application-mobile.report.html) · [JSON](corporate-application-mobile.report.json)
- [Formulario movil, repeticion 2](corporate-application-mobile-repeat-2.json)
- [Formulario movil, repeticion 3](corporate-application-mobile-repeat-3.json)
- [Backoffice, proveedores escritorio](backoffice-suppliers-desktop.report.html) · [JSON](backoffice-suppliers-desktop.report.json)
- [Captura del informe corporativo](corporate-report.png)
- [Captura del informe de backoffice](backoffice-report.png)

Las URL medidas fueron `/index.html`, `/application.html` y `/application/app/suppliers/index.html`. Las pruebas son laboratorio local con Chromium 153; no hay CrUX/RUM ni INP de campo para localhost. TBT es la metrica de laboratorio disponible y no sustituye INP. El API del backoffice no estaba iniciado, por lo que Lighthouse puede mostrar un error de red al solicitar proveedores; la auditoria evalua la vista estatica.
