# Informe de Caching — TrackFlow

> **Proyecto**: sp-aie-shernandez1689-project-logistic
> **Fecha**: 2026-10-15
> **Alcance**: Frontend (Next.js 16 + React 19) y Backend (FastAPI + TinyDB)

---

## Índice

1. [Decisiones en el Frontend](#1-decisiones-en-el-frontend)
   - [Lazy Loading con `next/dynamic`](#11-lazy-loading-con-nextdynamic)
   - [Memoización con `useMemo`](#12-memoización-con-usememo)
2. [Decisiones en el Backend](#2-decisiones-en-el-backend)
   - [Arquitectura de caché](#21-arquitectura-de-caché)
   - [Endpoints cacheados](#22-endpoints-cacheados)
3. [Intercambios Reconocidos (Freshness vs. Performance)](#3-intercambios-reconocidos)
4. [Qué No Se Cacheó y Por Qué](#4-qué-no-se-cacheó-y-por-qué)

---

## 1. Decisiones en el Frontend

### 1.1 Lazy Loading con `next/dynamic`

Se identificaron dos componentes cuyo coste de importación/sincronía no está justificado en el flujo principal de carga de página.

#### a) `CandidateForm` — formulario de edición

| Atributo | Valor |
|----------|-------|
| **Archivo** | `app/candidates/[id]/page.tsx` |
| **Importación diferida** | `dynamic(() => import("@/components/candidate-form"))` |
| **Ubicación en página** | Debajo del pliegue, tras los datos del candidato |

**Razonamiento**:
- El formulario de edición es **contenido secundario**. El usuario que accede al detalle de una candidatura primero necesita ver el nombre, estado, etapa, fechas y progreso.
- El formulario incluye su propia lógica de validación y estado (`isPending`, `error`, `message`) que no es necesaria para el render inicial.
- Diferir su carga permite que el layout crítico (cabecera, datos, progreso) se renderice inmediatamente sin esperar a que el componente se importe e instancie.

**Beneficio estimado**: Reducción del tiempo de "First Meaningful Paint" en la página de detalle, ya que el navegador no necesita descargar/procesar el componente de formulario antes de mostrar la información principal.

---

#### b) `NotesSection` — sección de notas internas

| Atributo | Valor |
|----------|-------|
| **Archivo** | `components/notes-section.tsx` (nuevo) |
| **Importación diferida** | `dynamic(() => import("@/components/notes-section"))` |
| **Llamada API propia** | `GET /records/:id/notes` |

**Razonamiento**:
- Las notas requieren una **llamada API independiente** que añade latencia a la página. Sin lazy loading, la página de detalle debía esperar a que se resolviera esta llamada para completar su render.
- Al mover las notas a un componente autocontenido que gestiona su propio fetching, la página principal se sirve inmediatamente con los datos del candidato, y las notas se cargan de forma asíncrona.
- Es contenido secundario que aparece debajo del pliegue; el usuario puede interactuar con los datos principales mientras las notas se cargan en segundo plano.

**Beneficio estimado**: Eliminación de la espera síncrona por `GET /records/:id/notes` antes de renderizar la página. El layout principal se pinta sin bloquearse.

**Limpieza asociada**: Se eliminó la exportación `CandidateNotes` de `candidate-controls.tsx` junto con sus imports no utilizados (`addCandidateNote`, `deleteCandidateNote`, `formatDate`, `FormEvent`, `CandidateNote`). Esto reduce el tamaño del módulo compartido y evita dependencias innecesarias.

---

### 1.2 Memoización con `useMemo`

#### a) Filtrado de candidatos en `candidate-list.tsx`

```typescript
const filtered = useMemo(
  () => candidates.filter((candidate) =>
    (!status || candidate.status === status) &&
    (!stage || candidate.stage === stage) &&
    (!term ||
      (candidate.full_name ?? "").toLowerCase().includes(term) ||
      (candidate.email ?? "").toLowerCase().includes(term)),
  ),
  [candidates, status, stage, term],
);
```

| Atributo | Valor |
|----------|-------|
| **Coste de la operación** | O(n · k) — n candidatos, k campos por candidato (3 filtros: status, stage, búsqueda texto) |
| **Operaciones por elemento** | 3x `toLowerCase()`, 2x `includes()`, accesos a diccionario |
| **Dependencias** | `[candidates, status, stage, term]` — explícitas y bien definidas |
| **Frecuencia de cambio** | `candidates` cambia solo al recargar la página; `status/stage/term` cambian con interacción del usuario (filtros) |

**Beneficio estimado**:
- Sin `useMemo`, el filtrado se ejecuta **cada vez que React renderiza el componente** (repintados por CSS, hovers, focus, cambios en elementos no relacionados).
- Con `useMemo`, el filtrado solo se recalcula cuando **al menos una dependencia cambia**. En una sesión típica con 100 candidatos y 3 cambios de filtro, el ahorro es de decenas de ejecuciones O(n·k) redundantes.

---

#### b) Datos enriquecidos de SKU en `products/page.tsx`

```typescript
<tbody>{useMemo(() => products.map((product) => {
  const level = stockLevel(product.current_stock);
  return ( /* fila con categoría, almacén, nivel de stock */ );
}), [products])}</tbody>
```

| Atributo | Valor |
|----------|-------|
| **Coste de la operación** | O(p) — p productos, con llamada a `stockLevel()` + búsqueda en `categoryLabels` y `warehouseLabels` por producto |
| **Dependencias** | `[products]` — única fuente de verdad, cambia solo al refrescar desde API |
| **Naturaleza** | Datos puramente derivados (inmutables mientras `products` no cambie) |

**Beneficio estimado**:
- La tabla de SKUs se renderiza en el dashboard de inventario, que puede refrescar su layout por diversas interacciones (hover sobre filas, cambio de tabs, etc.).
- `stockLevel()` encapsula lógica condicional (3 ramas) que se ejecutaba por producto en cada render. Con `useMemo`, es O(p) una sola vez tras cada carga de datos, en lugar de O(p) en cada render.

---

## 2. Decisiones en el Backend

### 2.1 Arquitectura de caché

Se implementó un módulo de caché propio en `services/api/cache.py` con las siguientes características:

| Característica | Implementación |
|----------------|----------------|
| **Almacenamiento** | Diccionario en memoria (`dict[str, tuple[float, Any]]`) |
| **TTL** | Configurable por instancia y por `set()` |
| **Concurrencia** | `threading.Lock` — thread-safe para servidores con múltiples workers |
| **Invalidación por prefijo** | `invalidate_prefix("incidents:list:")` limpia todas las variantes de un grupo |
| **Decorador opcional** | `@cache.cached(ttl=30)` para envolver funciones existentes |

No se eligió Redis porque:
- TinyDB es el backend de datos; no hay infraestructura Redis existente.
- La caché en memoria con TTL es suficiente para el volumen actual (cientos de documentos, no millones).
- La invalidación por prefijo proporciona control granular sin la complejidad operativa de Redis.

### 2.2 Endpoints cacheados

#### `GET /api/incidents` — Listar incidencias

| Aspecto | Detalle |
|---------|---------|
| **Ruta** | `GET /api/incidents?status=...&origin=...&branch=...&category=...` |
| **Coste de la operación** | **O(2n)**: `incidents_table.all()` (lectura completa de TinyDB) + filtrado lineal en Python + hidratación Pydantic |
| **Frecuencia estimada** | **Alta**. Es el endpoint principal del dashboard de incidencias. Se llama en cada carga de página y en cada cambio de filtro. |
| **Frecuencia de escritura** | **Baja**. Solo `POST /api/incidents` (creación) y `PATCH .../status` (cambio de estado). Eventos poco frecuentes en la operativa diaria. |
| **TTL elegido** | **60 segundos** |
| **Clave de caché** | `incidents:list:\|status=open\|origin=\|branch=\|category=` |
| **Estrategia de invalidación** | `invalidate_prefix("incidents:list:")` en cada POST o PATCH |

**Variantes cacheadas**: 16 combinaciones posibles de filtros (4 dimensiones × 2 estados cada una ≈ 16 líneas de caché en el peor caso). Cada combinación tiene su propia clave.

---

#### `GET /api/incidents/summary` — Resumen agregado

| Aspecto | Detalle |
|---------|---------|
| **Ruta** | `GET /api/incidents/summary` |
| **Coste de la operación** | **O(n · m)**: recorre todos los documentos (n) y para cada uno actualiza contadores en 4 dimensiones (m=4). Es el endpoint más costoso del sistema de incidencias. |
| **Frecuencia estimada** | **Alta**. El sumario se muestra en la cabecera del dashboard y puede ser consultado en cada cambio de vista. |
| **Frecuencia de escritura** | **Baja** (idem list_incidents). |
| **TTL elegido** | **60 segundos** |
| **Clave de caché** | `incidents:summary` (clave única) |
| **Estrategia de invalidación** | `invalidate("incidents:summary")` en cada POST o PATCH |

**Ahorro estimado**: Para 1.000 incidencias, el sumario ejecuta ~4.000 operaciones de contador. Cachearlo con TTL 60 s reduce la carga en el servidor de ~60 llamadas/minuto a ~1 llamada/minuto real a TinyDB.

---

#### `GET /suppliers` — Listar proveedores

| Aspecto | Detalle |
|---------|---------|
| **Ruta** | `GET /suppliers?country=...&category=...` |
| **Coste de la operación** | **O(2n)**: lectura completa de tabla + filtrado en listas + hidratación Pydantic |
| **Frecuencia estimada** | **Media**. El módulo de inventario consulta proveedores al gestionar SKUs y órdenes. |
| **Frecuencia de escritura** | **Muy baja**. Los proveedores se crean/editan manualmente; la tasa de cambio es de horas o días. |
| **TTL elegido** | **120 segundos** |
| **Clave de caché** | `suppliers:list:\|country=USA\|category=` |
| **Estrategia de invalidación** | `invalidate_prefix("suppliers:list:")` en POST, PATCH rate/status, DELETE |

---

#### `GET /suppliers/{id}` — Detalle de proveedor

| Aspecto | Detalle |
|---------|---------|
| **Ruta** | `GET /suppliers/{supplier_id}` |
| **Coste de la operación** | **O(1)**: lookup por `doc_id` en TinyDB + hidratación Pydantic |
| **Frecuencia estimada** | **Media**. Se llama al hacer clic en un proveedor para ver su detalle. |
| **Frecuencia de escritura** | **Muy baja** (idem). |
| **TTL elegido** | **120 segundos** |
| **Clave de caché** | `suppliers:detail:123` (1 clave por proveedor) |
| **Estrategia de invalidación** | `invalidate("suppliers:detail:123")` en PATCH rate/status o DELETE de ese proveedor |

**Nota**: Aunque el coste individual es bajo (O(1)), el _patrón de acceso_ (muchos usuarios consultando distintos proveedores) suma llamadas. El TTL de 120 s elimina consultas repetitivas al mismo proveedor en ventanas cortas de tiempo.

---

### 2.3 Resumen de claves de invalidación

| Operación de escritura | Claves invalidadas |
|------------------------|---------------------|
| `POST /api/incidents` | `incidents:list:*`, `incidents:summary` |
| `PATCH /api/incidents/{id}/status` | `incidents:list:*`, `incidents:summary` |
| `POST /suppliers` | `suppliers:list:*`, `suppliers:detail:*` |
| `PATCH /suppliers/{id}/rate` | `suppliers:list:*`, `suppliers:detail:{id}` |
| `PATCH /suppliers/{id}/status` | `suppliers:list:*`, `suppliers:detail:{id}` |
| `DELETE /suppliers/{id}` | `suppliers:list:*`, `suppliers:detail:{id}` |

---

## 3. Intercambios Reconocidos

### 3.1 TTL de 60 s en incidencias vs. frescura inmediata

**El intercambio**: Un TTL de 60 s significa que, durante un minuto, el dashboard puede mostrar datos ligeramente desactualizados si otro usuario crea o cambia el estado de una incidencia.

**Por qué 60 s es aceptable**:

1. **Naturaleza del dato**: Las incidencias logísticas no son datos financieros en tiempo real. Una incidencia abierta sigue abierta aunque el dashboard tarde 60 s en reflejar una nueva. El flujo de trabajo típico es: abrir incidencia → ver en dashboard → cambiar estado. Cada paso toma minutos u horas, no segundos.

2. **Consistencia de sesión**: Un usuario que trabaja en una incidencia la busca por ID (endpoint sin cache), no por el listado. El listado cacheado es una vista general; el detalle siempre es fresco.

3. **Carga del servidor**: TinyDB no está diseñado para alta concurrencia. Cada lectura completa de la tabla (`all()`) es O(n) y bloquea el archivo. Reducir las lecturas de ~60/min a ~1/min protege a TinyDB de contención.

4. **Invalidación explícita**: Cuando el usuario _escribe_ (crea o cambia estado), la invalidación es inmediata. El TTL solo afecta a las _lecturas_ de otros usuarios que no hicieron el cambio.

### 3.2 TTL de 120 s en proveedores vs. TTL de 60 s en incidencias

**El intercambio**: Los proveedores tienen el doble de TTL que las incidencias.

**Por qué 120 s es aceptable**:

1. **Frecuencia de cambio radicalmente menor**: Los proveedores se crean/editan manualmente (tarifas, estado). La tasa de cambio típica es de horas o días. Un TTL de 120 s es equivalente a frescura prácticamente inmediata para este dominio.

2. **Coste de invalidación**: Al tener el doble de TTL pero la misma frecuencia de escritura ultrabaja, la probabilidad de que un usuario vea datos desactualizados es cercana a cero.

3. **Ahorro de CPU**: Con TTL 120 s, el endpoint `GET /suppliers` se ejecuta real contra TinyDB ~0.5 veces/minuto de media, frente a ~6 veces/minuto sin caché.

### 3.3 Memoización vs. Recomputación en frontend

**El intercambio**: `useMemo` introduce un coste de comparación de dependencias en cada render (referencia de array, comparación de strings) a cambio de evitar el cómputo del filtrado.

**Por qué el equilibrio es positivo**:

- Para el filtrado de candidatos, la comparación de dependencias es **O(1) en referencias** (el array `candidates` se compara por identidad, no por valor). El coste de comparar `status`, `stage` y `term` es trivial (3 comparaciones de strings cortas).
- El ahorro es **O(n · k)** por cada render donde las dependencias no cambian. En una página con 50 candidatos y múltiples renders (hover, focus, scroll), el beneficio supera ampliamente el coste.

---

## 4. Qué No Se Cacheó y Por Qué

### 4.1 `GET /api/incidents/{incident_id}` — Detalle de incidencia

| Aspecto | Evaluación |
|---------|------------|
| **Coste** | O(1) — lookup por `doc_id` en TinyDB |
| **Frecuencia** | Media |
| **¿Cacheado?** | ❌ No |

**Justificación**:
- El coste es **O(1)** — el lookup por ID en TinyDB es una lectura directa sin escaneo. No hay margen de mejora significativo.
- Los datos de una incidencia cambian con frecuencia relativa: cada vez que un editor cambia el estado, el documento se actualiza. La tasa de cambio por incidencia es alta en comparación con su tasa de consulta.
- El esfuerzo de invalidación (trackear qué incidencias están cacheadas y cuándo cambian) supera el beneficio de cachear un lookup O(1).

---

### 4.2 `GET /api/incidents/{incident_id}/status` (inexistente como GET)

No existe un endpoint GET para el estado de una incidencia; el estado se obtiene como parte del detalle (`GET /{incident_id}`). No aplica.

---

### 4.3 Endpoints de Flask (`app.py` legacy)

| Aspecto | Evaluación |
|---------|------------|
| **Endpoints** | `POST /api/incidents/analyze`, estáticos (`/`, `/styles.css`, etc.) |
| **¿Cacheados?** | ❌ No |

**Justificación**:
- `POST /api/incidents/analyze` recibe un CSV subido por el usuario y devuelve un análisis. Es una operación de **cómputo puro** sobre datos de entrada del usuario; no hay datos compartidos que cachear. Cada llamada tiene entrada diferente.
- Los endpoints estáticos (`/styles.css`, `/validation.js`, etc.) ya son servidos por Flask `send_from_directory` y los navegadores los cachean mediante cabeceras HTTP/304. Añadir otra capa de caché en la aplicación sería redundante.

---

### 4.4 Componentes frontend NO candidatos para lazy loading

| Componente | Razón |
|------------|-------|
| **`CandidateList`** (lista principal) | Es el contenido principal de la página de inicio. Diferir su carga retrasaría lo que el usuario busca. |
| **`CandidateProgress`** (controles de progreso) | Es pequeño (dos selects), no tiene llamada API propia, y aparece en la zona visible de la página de detalle. El overhead de `dynamic()` supera el beneficio. |
| **`InventoryLayout`** (layout de backoffice) | Es un layout de autenticación; debe cargarse sincrónicamente para proteger rutas. No aplica lazy loading. |

---

## 5. Resumen de Archivos Modificados

| Archivo | Cambio | Tipo |
|---------|--------|------|
| `components/notes-section.tsx` | Nuevo componente autocontenido para lazy loading de notas | Frontend |
| `app/candidates/[id]/page.tsx` | `next/dynamic` para CandidateForm y NotesSection | Frontend |
| `components/candidate-controls.tsx` | Eliminado CandidateNotes e imports no usados | Frontend |
| `components/candidate-list.tsx` | `useMemo` para filtrado O(n) | Frontend |
| `app/backoffice/inventory/products/page.tsx` | `useMemo` para datos enriquecidos de SKU | Frontend |
| `services/api/cache.py` | Nuevo módulo de caché TTL thread-safe | Backend |
| `services/api/routes/incidents.py` | Caché en GET list y summary + invalidación en escrituras | Backend |
| `services/api/routes/suppliers.py` | Caché en GET list y detail + invalidación en escrituras | Backend |

---

## 6. Resultados de Validación

| Prueba | Resultado |
|--------|-----------|
| TypeScript type-check (archivos modificados) | ✅ Sin errores |
| Python compile (`py_compile`) | ✅ 3/3 archivos OK |
| Tests de serialización existentes | ✅ 14/14 tests pasan |

---

*Fin del informe*