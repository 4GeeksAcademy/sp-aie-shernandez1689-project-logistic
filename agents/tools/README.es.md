# Carpeta `agents/tools`

Esta carpeta agrupa **herramientas reutilizables** que pueden usar los agentes del monorepo (por ejemplo: conectores a APIs internas, wrappers de bases de datos, utilidades de scraping, validadores, clientes de servicios, etc.).

- **Propósito principal**: evitar duplicación de código entre agentes y estandarizar cómo los agentes interactúan con sistemas de la compañía.
- **Recomendación**: documenta cada herramienta que añadas (qué hace, entradas/salidas, permisos/seguridad, límites y ejemplos de uso) y enlázala desde los agentes que la consumen.

## Herramientas

| Herramienta | Ubicación | Descripción |
|-------------|-----------|-------------|
| `incident_lookup` | [`services/rag_agent/tools/`](../../services/rag_agent/tools/README.es.md) | Consulta de tickets de soporte de solo lectura contra el gestor de incidencias real (en-proceso, contrato tipado, timeout + fallback honesto). La usa el grafo del agente RAG. |
| `inventory_lookup` | [`services/rag_agent/tools/`](../../services/rag_agent/tools/README.es.md) | Consulta de stock por producto (SKU) de solo lectura contra el gestor de inventario real (`services/user-api`, SQLModel/PostgreSQL). El mismo contrato tipado que `incident_lookup`: transporte en-proceso (sin HTTP), timeout + fallback honesto, sin datos simulados. La usa el grafo del agente RAG. |
