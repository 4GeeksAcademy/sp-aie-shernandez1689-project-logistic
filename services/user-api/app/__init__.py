"""Paquete de la user-api de TrackFlow.

Sin este marcador, ``app`` se resuelve como *namespace package* y pierde la
prioridad de importación contra ``services/api/app.py`` (módulo regular),
rompiendo ``from app.main import app`` en la colección de tests de la raíz.
Este ``__init__.py`` hace que ``services/user-api/app`` sea un paquete regular
que se importa de forma inequívoca.
"""