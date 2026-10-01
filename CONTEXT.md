# Milestone 0:  Bienvenido a TrackFlow

---

TrackFlow es una empresa de logística de última milla y gestión de almacenes fundada en 2009 en Los Ángeles, Estados Unidos. Opera en dos mercados — Estados Unidos y España — con almacenes en Los Ángeles y Zaragoza. La empresa cuenta con unos 130 empleados y factura alrededor de 9 millones de euros anuales.

TrackFlow existe porque las marcas de e-commerce son buenas haciendo y vendiendo productos, pero no haciendo llegar esos productos a la puerta del cliente. Eso es lo que hace TrackFlow por ellas: almacena su inventario, prepara y empaqueta los pedidos, los envía a través de una red de transportistas y gestiona las devoluciones cuando los productos regresan. Para las marcas que trabajan con TrackFlow, toda la operación logística — desde el momento en que se hace un pedido hasta el momento en que se entrega o se devuelve — es responsabilidad de TrackFlow.

## Cómo está organizada la empresa

TrackFlow está liderada por **Thomas Harry**, fundador y CEO, con sede en Los Ángeles. La empresa tiene una oficina de tecnología en Zaragoza, España, donde están el CTO Andrés Kim y la mayor parte del equipo técnico. Los equipos de operaciones, comercial y atención al cliente están distribuidos entre los dos países.

La empresa se organiza en las siguientes áreas:

**Operaciones de Almacén** es donde ocurre el trabajo físico de la logística. Ana Whitfield supervisa los dos almacenes —uno en Los Ángeles, otro en Zaragoza— y los aproximadamente 70 operarios que los gestionan. Cada día, cientos de pedidos llegan, se recogen de las estanterías, se empaquetan y se entregan a los transportistas. Los dos almacenes funcionan con sistemas distintos y no tienen una visión compartida del inventario.

**Última Milla y Gestión de Transportistas** gestiona la relación con los 8 transportistas con los que TrackFlow trabaja en los dos países — entre ellos UPS, FedEx, MRW y SEUR. Carlos Vega coordina qué transportista lleva cada envío, hace seguimiento de las entregas y gestiona las incidencias que inevitablemente ocurren: paquetes perdidos, entregas fallidas, direcciones incorrectas. Hoy, la mayor parte de esto se hace de forma manual, transportista por transportista.

**Logística Inversa** gestiona lo que ocurre cuando un producto regresa. Sofía Ramos lidera este equipo de cinco personas. Las devoluciones representan entre el 18% y el 25% del volumen total según el cliente y el país, y cada devolución implica una cadena de decisiones — aprobar o rechazar, recoger o no, reacondicionar o desechar — que actualmente pasan todas por revisión humana.

**Atención al Cliente** es la primera línea entre TrackFlow y las personas a las que sirve. Valentina Cruz gestiona 15 agentes en Los Ángeles y Zaragoza que atienden consultas tanto de las marcas (que quieren saber cómo va su operación) como de los consumidores finales (que quieren saber dónde está su paquete). La gran mayoría de las consultas son repetitivas, y ahora mismo cada una de ellas la responde una persona.

**Comercial y Relación con Clientes** gestiona la cartera de clientes marca de TrackFlow. Miguel Torres lidera a los account managers y al equipo de desarrollo de negocio, responsables de retener a los clientes actuales y ganar nuevos. Los contratos son anuales, y las renovaciones se ganan o se pierden en función de si los clientes sienten que su operación logística está funcionando bien.

**Tecnología** es el equipo que construye y mantiene todo. Andrés Kim lidera desde Zaragoza a un equipo de desarrolladores, data engineers y personas de sistemas. La arquitectura actual es un patchwork: dos sistemas de almacén distintos, un ERP de principios de los años 2010 e integraciones entre ellos que se construyeron con rapidez y nunca se documentaron bien. Cuando algo falla, el equipo se entera por un mensaje de WhatsApp de alguien en operaciones.

**Dirección Ejecutiva** recae en Thomas, que gestiona el negocio desde Los Ángeles con un informe semanal consolidado que cada director prepara manualmente — un proceso que consume horas cada domingo por la noche y que aun así entrega datos que ya llevan uno o dos días de antigüedad.

## Dónde está la empresa hoy

TrackFlow tiene buenos clientes, un equipo de operaciones competente y una propuesta de valor clara. Lo que le falta es la infraestructura para gestionar una operación logística de dos países a escala. Los dos almacenes no pueden ver el inventario del otro. Los datos de rendimiento de los transportistas no existen en ninguna forma estructurada. Las devoluciones se aprueban o rechazan una por una. Las consultas de los clientes las responden agentes consultando un documento de Word en Google Drive. El CEO toma decisiones basándose en un informe ensamblado a mano.

La consecuencia es que TrackFlow es más lenta, más propensa a errores y menos rentable de lo que necesita ser — y la brecha crece mientras los competidores invierten en automatización.

Daniel ha creado una unidad interna llamada **TrackFlow Tech** con un mandato claro: construir los sistemas, las integraciones y las automatizaciones inteligentes que permitan a TrackFlow operar como la empresa logística moderna que necesita ser.

**Tú formas parte de esa unidad.**

---

## Los departamentos y sus problemas

### 🚚 Operaciones de almacén

**Responsable:** Ana Whitfield (~70 operarios + 2 responsables de almacén)

El almacén de Los Ángeles y el de Zaragoza utilizan sistemas de gestión de almacén (SGA) diferentes — uno es software comercial, el otro es una hoja de cálculo avanzada. No existe visibilidad de inventario en tiempo real a nivel global. Los pedidos entrantes llegan por email en formatos distintos según el cliente y se transcriben manualmente. El picking se hace con listas en papel. Las discrepancias de inventario son frecuentes y se detectan tarde.

**Qué necesitan:** Una API de inventario unificada que devuelva el stock en tiempo real de cualquier SKU en cualquiera de los dos almacenes, un pipeline de ingesta de pedidos que parsee los emails automáticamente, un dashboard de operaciones de almacén, y alertas de stock bajo que notifiquen al cliente y al equipo de compras.

---

### 📦 Última milla y gestión de transportistas

**Responsable:** Carlos Vega (6 coordinadores logísticos)

TrackFlow trabaja con 8 transportistas en ambos países (UPS, FedEx y DHL en Estados Unidos; MRW, SEUR y DHL en España, más dos transportistas locales). La asignación de transportista por envío es manual. El seguimiento de paquetes obliga a consultar múltiples portales de cada transportista por separado. No hay datos históricos de rendimiento: sin tasa de entrega a tiempo, sin incidencias por ruta, sin coste por kg.

**Qué necesitan:** Un motor de selección de transportista que recomiende la opción óptima dado el destino, peso y urgencia; un endpoint unificado de tracking que agregue el estado desde cualquier transportista; un portal de seguimiento público para el destinatario; y un dashboard de rendimiento de transportistas.

---

### 🔄 Logística inversa

**Responsable:** Sofía Ramos (equipo de 5 personas)

Las devoluciones representan entre el 18% y el 25% del volumen según cliente y país. Cada devolución pasa por revisión manual — no existen criterios de aprobación automáticos. La inspección del producto devuelto es subjetiva e inconsistente entre operarios. No hay visibilidad sobre qué productos se devuelven más ni por qué.

**Qué necesitan:** Un motor de aprobación automática de devoluciones con reglas configurables por cliente, un flujo automatizado de recogida (aprobación → etiqueta → instrucciones al cliente → programación con transportista), un sistema de inspección asistido por IA donde el operario fotografía el producto y la IA clasifica su estado, y un dashboard de devoluciones con análisis de patrones.

---

### 📞 Experiencia del cliente

**Responsable:** Valentina Cruz (15 agentes en Los Ángeles y Zaragoza)

TrackFlow atiende a dos tipos de cliente: las marcas (B2B) que contratan sus servicios y los consumidores finales (B2C) que reciben los paquetes. Los 15 agentes gestionan ambos a través de email, WhatsApp y teléfono sin un sistema unificado de tickets. El 80% de las consultas podría resolverse automáticamente. No hay base de conocimiento. La cobertura fuera del horario de oficina es cero.

**Qué necesitan:** Un agente de CX de primera línea que resuelva automáticamente consultas de seguimiento y estado de devoluciones, una base de conocimiento semántica indexada para RAG, un sistema unificado de tickets, un dashboard de CX en tiempo real, y análisis de sentimiento para detectar clientes frustrados antes de que escalen. El soporte multiidioma (español + inglés) es opcional pero altamente recomendado, empezando por un idioma base.

---

### 🤝 Comercial y relación con clientes

**Responsable:** Miguel Torres (4 account managers + 4 desarrollo de negocio)

Los account managers gestionan sus cuentas en hojas de cálculo personales e hilos de email — no hay CRM. Los informes a clientes son manuales: cada mes un account manager consolida datos de distintos sistemas para enviar a cada cliente un informe en PDF. No hay visibilidad sobre qué clientes corren riesgo de no renovar.

**Qué necesitan:** Integración con CRM con perfil unificado de cliente, informes PDF para clientes generados automáticamente por un agente, un dashboard de salud de cliente con puntuación de riesgo de renovación, alertas a 90 y 30 días del vencimiento del contrato, y un agente comercial que sugiera el servicio y la estructura de precios más relevantes para cada prospecto.

---

### 💻 Tecnología

**CTO:** Andrés Kim (equipo de 7 personas en Zaragoza)

La arquitectura tecnológica de TrackFlow es el resultado de años de crecimiento no planificado: dos SGA diferentes, un ERP corporativo de principios de la década de 2010, scripts de Python punto a punto sin documentar, y bases de datos en dos proveedores cloud distintos. No hay telemetría centralizada. Cuando un endpoint falla en Los Ángeles, el equipo de Zaragoza se entera por WhatsApp. Desplegar una nueva funcionalidad lleva entre una y dos semanas.

**Qué necesitan:** Telemetría y logging centralizados de ambos países, un pipeline de datos que alimente todos los dashboards de la empresa, monitorización en tiempo real con alertas automáticas, un agente de documentación técnica, y automatización de tareas de operaciones (backups, health checks, notificaciones de incidencias con contexto).

---

### 📊 Dirección Ejecutiva

**CEO:** Daniel Espinoza

Daniel recibe un informe consolidado cada lunes que sus directores preparan el domingo por la tarde combinando datos de distintos sistemas — 3 o 4 horas de trabajo por director. A las 10 de la mañana del lunes, algunos datos ya tienen dos días de antigüedad. No hay una visión unificada del negocio por país. Las decisiones estratégicas se toman con datos parciales.

**Qué necesita:** Un dashboard ejecutivo global con KPIs de ambas operaciones en tiempo real (volumen de envíos, tasa de entrega a tiempo, coste operativo, devoluciones, satisfacción del cliente), un informe semanal generado automáticamente los lunes a las 7 de la mañana, comparativas por país, alertas por umbrales, y un asistente de IA al que pueda consultar en lenguaje natural.

---

## ¿Por qué elegir TrackFlow?

Elige TrackFlow si te atraen:

- **Logística y operaciones físicas** — cada línea de código que escribas está conectada a un paquete que se mueve de una estantería de almacén a la puerta de alguien.
- **Complejidad transfronteriza** — dos países, dos idiomas, dos entornos regulatorios y dos stacks tecnológicos separados que hay que unificar.
- **Ingeniería de datos en su forma más concreta** — métricas de rendimiento de transportistas, inventario a nivel de SKU, streams de eventos de envío y clasificación de devoluciones son todos estructurados, medibles y visualmente impactantes en dashboards.
- **Sistemas que funcionan 24/7** — los clientes de TrackFlow no dejan de esperar sus paquetes después de las 18:00. El agente de CX, el portal de seguimiento y el dashboard de operaciones deben estar siempre disponibles.

Los retos de IA en TrackFlow incluyen clasificación de estado de productos devueltos por imagen, búsqueda semántica sobre políticas logísticas en dos idiomas, selección inteligente de transportista con recomendaciones explicables, y un agregador de tracking en tiempo real que extrae datos de 8 APIs de transportistas distintas. Si quieres construir sistemas que manejen complejidad del mundo físico a escala, TrackFlow es tu empresa.

# CONTEXT.md — TrackFlow

## Milestone 1: Your Company's Public Website

_Estas instrucciones están [disponibles en español](./CONTEXT-trackflow.es.md)._

> This document describes your company and the specific situation you're building this milestone for. Read it completely before writing any code. Everything you build must reflect this context.

---

## Your company

**TrackFlow** is a last-mile delivery and warehouse management company founded in 2009 in Los Angeles, United States. It operates in two markets—United States (Los Angeles) and Spain (Zaragoza)—and offers three services: warehouse management for e-commerce brands, last-mile delivery (the final leg from warehouse to end customer), and reverse logistics (returns and product reconditioning). It has approximately 130 employees and generates around 9 million euros in annual revenue. Its clients are mid-sized fashion, electronics, and cosmetics brands that sell online.

---

## Your department and the problem you must solve

You work in the **TrackFlow Tech** unit, reporting directly to CTO Andrés Kim. TrackFlow's current corporate website was built years ago by an external agency and is completely outdated. It doesn't reflect that the company operates in two countries, doesn't clearly explain the services, and there's no way for interested companies to request information in a structured manner. Miguel Torres (Commercial Director) needs a professional website that presents TrackFlow's services and captures leads from potential companies that want to outsource their logistics.

---

## Your stakeholder

**Miguel Torres**, Commercial Director

> Hi,
>
> We need a new website that presents TrackFlow as what we are: a serious logistics operator with presence in the United States and Spain. It must explain our three main services: warehouse management, last mile, and reverse logistics. I also need a page with a form so interested companies can request information. We currently get very vague inquiries by email and waste a lot of time qualifying whether they're real clients or not. I want to capture: company data, type of product they handle, estimated monthly shipping volume, countries where they operate, and which services interest them. The site must be responsive, accessible, and SEO optimized. Use Tailwind and make sure the form has complete validation.

---

## Language scope

- Multilingual support is **optional but highly recommended** given TrackFlow's operations in the United States and Spain.
- You must choose one **base language** for the full website and form experience.
- If you implement a second language, treat it as an enhancement (do not reduce quality/completeness in the base language).

## Landing page content

Your landing page must include the following sections, in this order:

### Header

- Logo or name "TrackFlow"
- Navigation: Home | Services | Coverage | Contact

### Hero

- **Headline:** "Logistics that scales with your e-commerce"
- **Subheadline:** "Warehouse management, last-mile deliveries, and reverse logistics in the United States and Spain. Over 15 years helping fashion, electronics, and cosmetics brands grow without worrying about operations."
- **Call to action:** Button "Request information" linking to the form

### Services (3 columns)

1. **Warehouse Management**
   - Storage, picking and packing
   - Real-time inventory
   - We operate warehouses in Los Angeles and Zaragoza

2. **Last-Mile Deliveries**
   - Certified carrier network in both countries
   - Unified shipment tracking
   - Incident and returns management

3. **Reverse Logistics**
   - Complete returns management
   - Inspection and reconditioning
   - Integration with your sales platform

### Coverage (2 columns)

- **United States**
  - Warehouse in Los Angeles
  - National coverage
  - Carriers: UPS, FedEx, DHL

- **Spain**
  - Warehouse in Zaragoza
  - Peninsular and island coverage
  - Carriers: MRW, SEUR, DHL

### Why TrackFlow (4 benefits)

- **Binational operation:** The only operator with own infrastructure in the United States and Spain
- **+130 professionals** dedicated to your logistics
- **Own technology** for total visibility of your inventory
- **E-commerce specialization** in fashion, electronics, and cosmetics

### Contact

- Email: <comercial@trackflow.com>
- Los Angeles: +1 213 555 0147
- Zaragoza: +34 976 123 456

### Footer

- © 2025 TrackFlow. All rights reserved.
- LinkedIn

---

## Information request form fields

Your form must capture the following information:

| Field                                       | Type     | Validation                                             | Required |
| ------------------------------------------- | -------- | ------------------------------------------------------ | -------- |
| **Company name**                            | text     | Minimum 2 characters                                   | Yes      |
| **Contact person**                          | text     | Minimum 2 words (first and last name)                  | Yes      |
| **Corporate email**                         | email    | Valid email format                                     | Yes      |
| **Phone**                                   | tel      | Format: +[country code] [number]                       | Yes      |
| **Company website**                         | url      | Valid URL format                                       | No       |
| **Main operating country**                  | select   | United States / Spain / Both / Other                   | Yes      |
| **Product type**                            | select   | Fashion / Electronics / Cosmetics / Food / Other       | Yes      |
| **Estimated monthly shipping volume**       | select   | 0-100 / 101-500 / 501-2000 / 2000+ / Not sure          | Yes      |
| **Services of interest**                    | checkbox | Warehousing / Last mile / Reverse logistics (multiple) | Yes      |
| **Do you currently work with another 3PL?** | radio    | Yes / No / Evaluating options                          | Yes      |
| **Comments or specific needs**              | textarea | Maximum 500 characters                                 | No       |
| **I accept the privacy policy**             | checkbox | Must be checked to submit                              | Yes      |

---

## Specific validations

1. **Company name:** Minimum 2 characters
2. **Contact person:** Must contain at least first and last name
3. **Email:** Must be valid format (contain @ and domain)
4. **Phone:** Must start with + followed by country code
5. **Website:** If provided, must be valid URL (start with http:// or https://)
6. **Services of interest:** At least one must be selected
7. **Comments:** Limit to 500 characters with visible counter
8. **Privacy policy:** Checkbox must be checked to submit

---

## Expected error messages

When a field doesn't meet validation, display these specific messages:

- **Company name:** "Company name must have at least 2 characters"
- **Contact person:** "Enter first and last name of contact"
- **Email:** "Enter a valid corporate email (example: <name@company.com>)"
- **Phone:** "Phone must include country code (example: +1 213 555 0147)"
- **Website:** "If you include website, it must be a valid URL"
- **Country:** "Select main operating country"
- **Product type:** "Select the type of product you handle"
- **Monthly volume:** "Select estimated monthly volume"
- **Services of interest:** "Select at least one service of interest"
- **Current 3PL:** "Indicate if you currently work with another logistics provider"
- **Comments:** "Comments cannot exceed 500 characters (X remaining)"
- **Privacy policy:** "You must accept the privacy policy to continue"

---

## Success message

When the form validates correctly (simulate submission), display:

> **Thank you for your interest in TrackFlow!**
>
> We have received your request. Our commercial team will review your information and contact you within the next 24-48 hours to schedule a call and learn about your logistics needs in detail.
>
> If you have any urgent inquiry, write to us directly at <comercial@trackflow.com>

---

## Specific restriction

The form is designed for **e-commerce companies looking to outsource their logistics**, not for end consumers who want to track a package or make a return. If you detect that the selected volume is "0-100 shipments/month" AND the "Product type" field is relevant, include a warning message: "For volumes under 100 monthly shipments, our services might not be the most efficient solution. Are you sure you want to continue?"

---

## Required Schema.org markup

Implement the following Schema.org markup on your landing page:

```json
{
  "@context": "https://schema.org",
  "@type": "Organization",
  "name": "TrackFlow",
  "description": "Warehouse management and last-mile deliveries for e-commerce",
  "url": "https://trackflow.com",
  "foundingDate": "2009",
  "address": [
    {
      "@type": "PostalAddress",
      "addressCountry": "US",
      "addressLocality": "Los Angeles",
      "addressRegion": "California"
    },
    {
      "@type": "PostalAddress",
      "addressCountry": "ES",
      "addressLocality": "Zaragoza",
      "addressRegion": "Aragón"
    }
  ],
  "contactPoint": {
    "@type": "ContactPoint",
    "telephone": "+1-213-555-0147",
    "contactType": "sales",
    "availableLanguage": ["Spanish", "English"]
  },
  "sameAs": ["https://linkedin.com/company/trackflow"],
  "areaServed": [
    {
      "@type": "Country",
      "name": "United States"
    },
    {
      "@type": "Country",
      "name": "Spain"
    }
  ]
}
```

_Until you add your context, keep this placeholder so the repo structure is clear._
