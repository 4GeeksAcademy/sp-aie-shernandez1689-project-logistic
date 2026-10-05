# Skill: Workflow Progress Sync (agent5e)

## Objetivo unico
Actualizar de forma consistente el archivo memory-bank/progress.md al cerrar una tarea o iteracion, reflejando estado actual y proximos pasos accionables.

## Cuando usar esta skill
- Al terminar una tarea de desarrollo con cambios en el repositorio.
- Al cerrar una iteracion parcial y dejar continuidad para el siguiente agente.
- Cuando se necesita trazabilidad clara del avance sin revisar todo el historial.

## Inputs documentados
1. task_summary (string, obligatorio): resumen corto de la tarea completada.
2. changed_files (lista de rutas, obligatorio): archivos modificados en la tarea.
3. validation_results (lista de strings, obligatorio): resultados de pruebas, lint o checks ejecutados.
4. next_steps (lista de 1 a 5 items, obligatorio): acciones concretas para continuar el trabajo.
5. blockers (lista de strings, opcional): riesgos o bloqueos detectados.

## Procedimiento
1. Leer memory-bank/progress.md.
2. Actualizar la seccion Estado actual del desarrollo con la tarea recien completada.
3. Reemplazar o ajustar Proximos pasos previstos con items priorizados y accionables.
4. Incluir bloqueos solo si existen y con accion de mitigacion.
5. Guardar cambios sin modificar secciones no relacionadas.

## Criterios de aceptacion (explicitos y verificables)
1. El archivo memory-bank/progress.md queda actualizado en la misma ejecucion de la skill.
2. El contenido agregado incluye al menos:
- 1 item nuevo en Estado actual del desarrollo.
- 1 a 5 proximos pasos en formato numerado.
3. Se mencionan validaciones ejecutadas o se indica explicitamente "Sin validaciones ejecutadas".
4. No se alteran archivos fuera de memory-bank/progress.md.
5. El texto final es claro, sin placeholders (por ejemplo: TODO, TBD, pendiente definir).

## Output esperado
Un resumen breve para el usuario con:
- actualizaciones aplicadas en memory-bank/progress.md,
- validaciones reportadas,
- siguiente accion recomendada.
