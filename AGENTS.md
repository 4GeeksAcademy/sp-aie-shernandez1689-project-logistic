# AGENTS

## Lectura obligatoria al iniciar cada sesion
Antes de ejecutar cambios, el agente debe leer en este orden:
1. memory-bank/projectbrief.md
2. memory-bank/techContext.md
3. memory-bank/progress.md

Si alguno de estos archivos no existe o esta vacio, el agente debe reportarlo y pedir confirmacion para completar el contenido.

## Flujo obligatorio antes de cada commit
El agente debe cumplir este flujo, en orden, sin omitir pasos:
1. Revisar alcance: validar que los cambios implementados correspondan exactamente al requerimiento activo.
2. Verificar impacto: inspeccionar archivos modificados y confirmar que no haya cambios accidentales fuera del alcance.
3. Ejecutar validaciones: correr pruebas, linters o checks disponibles para los modulos afectados y registrar resultados.
4. Actualizar memoria de sesion: reflejar en memory-bank/progress.md el estado actual, cambios relevantes y siguiente paso.
5. Preparar commit: revisar diff final, redactar mensaje de commit claro y confirmar que solo incluya archivos esperados.

Si algun paso falla, el agente no debe continuar al commit hasta resolverlo o recibir una instruccion explicita del desarrollador.

## Rutas protegidas (no modificar sin confirmacion explicita del desarrollador)
El agente no debe crear, editar ni eliminar contenido en estas rutas sin autorizacion explicita:
- infra/
- workflows/
- mcps/
- .gitignore
- README.md
- README.es.md
- packages/shared/types/
- scripts/

Regla adicional: cualquier archivo fuera del alcance solicitado que aparezca modificado debe tratarse como bloqueado hasta recibir confirmacion explicita.
