# Regla de desarrollo: cambios acotados y verificables

## Alcance de aplicacion
Siempre activa.

## Regla
Antes de finalizar cualquier cambio, el agente debe confirmar que:
1. Solo se modificaron archivos dentro del alcance solicitado por el requerimiento activo.
2. No se alteraron rutas protegidas sin confirmacion explicita del desarrollador.
3. Se registraron resultados de validacion (pruebas, lint o checks disponibles) para los modulos impactados.

## Criterio de cumplimiento
Si cualquiera de los puntos anteriores no se cumple, el cambio se considera incompleto y no debe pasar a commit.
