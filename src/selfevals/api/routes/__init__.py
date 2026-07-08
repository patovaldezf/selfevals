"""Resource-scoped route modules mounted by `api.app.build_app()`.

Each module exposes a single `register(app, deps)` function that adds its
routes to the FastAPI app. Modules own no state; all storage/object-store
wiring comes from the shared `api.deps.AppDeps` instance.
"""
