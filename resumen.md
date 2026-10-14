¿Qué hace útil una auditoría de Lighthouse?
Lighthouse analiza una página a la vez — no recorre toda la aplicación. Una sola ejecución en la página de inicio no dice nada sobre el rendimiento de una vista de dashboard con carga intensiva de datos. Al auditar una aplicación con múltiples vistas, ejecuta Lighthouse en las páginas que más importan: aquellas con mayor complejidad visual, más componentes renderizados a la vez, o mayor tráfico de usuarios. En el sitio corporativo suelen ser la home y cualquier página con mucho contenido; en el backoffice, normalmente el dashboard principal o cualquier vista con tablas, gráficos o datos en tiempo real.

Lighthouse te entrega cuatro puntuaciones: Performance, Accessibility, Best Practices y SEO. Para un frontend en producción, los benchmarks que más importan son:

Performance ≥ 90 — Por debajo de 50 se considera deficiente y afecta a usuarios reales en redes móviles.
LCP (Largest Contentful Paint) < 2.5s — Tiempo hasta que el contenido principal es visible.
CLS (Cumulative Layout Shift) < 0.1 — Movimiento inesperado del layout durante la carga.
FID / INP (Interaction to Next Paint) < 200ms — Capacidad de respuesta a la interacción del usuario.
TTFB (Time to First Byte) — Tiempo hasta que el servidor empieza a enviar la respuesta HTML.
Un informe completo de Lighthouse puede resultar abrumador las primeras veces — decenas de auditorías, oportunidades y diagnósticos en una sola pantalla. No intentes corregir todo de golpe. Una forma práctica de recorrerlo:
1- Empieza por los indicadores principales — las cuatro puntuaciones Core Web Vitals más las señales de servidor como TTFB, LCP, CLS e INP. Te dicen dónde duele (red, render, layout, interactividad) antes de entrar en cada sub-auditoría.
2- Apóyate en el agente de IA como tutor — pega o describe una métrica cada vez y pregunta qué mide, qué se considera “bueno” y qué correcciones suelen moverla. Paso a paso: entiende el indicador y luego las acciones recomendadas para tu stack (Next.js, imágenes, fuentes, hidratación, etc.).
3- Resuelve un caso por commit — elige el KPI de mayor impacto, aplica un cambio dirigido, haz commit con un mensaje claro, vuelve a ejecutar Lighthouse en la misma URL y anota la diferencia. Repite hasta que los KPI principales estén en rango saludable.
4- Después aborda lo complementario — avisos de accesibilidad, best practices, oportunidades de SEO y auditorías de menor prioridad importan, pero después de las métricas que afectan de verdad a usuarios en dispositivos lentos.
Causas habituales que los estudiantes pasan por alto: imágenes sin optimizar, recursos que bloquean el render, layout shifts por atributos width/height ausentes en imágenes, fuentes cargadas sin display: swap, y problemas de hidratación en Next.js.