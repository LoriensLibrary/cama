"""Endpoint routers for the CAMA HTTP API.

Each module here owns one endpoint family. The app factory in
``cama.api.server`` mounts each module's ``router`` and stays
narrowly focused on cross-cutting concerns (lifespan, middleware,
exception handlers).
"""
