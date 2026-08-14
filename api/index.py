try:
    from backend.app.main import app
except ModuleNotFoundError as exc:
    # Direct API deployments have a small source-file count limit. For those
    # deployments, a generated zip bundle supplies the same tracked backend
    # package. Normal Git/Vercel and Docker builds import the source directly.
    if not (exc.name or "").startswith("backend"):
        raise
    from api.backend_bundle import install_backend_bundle

    install_backend_bundle()
    from backend.app.main import app
