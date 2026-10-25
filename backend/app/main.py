import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api import auth, routes_admin, routes_data, routes_offers, routes_stores, routes_strategy
from app.core.config import get_settings
from app.core.errors import AppError
from app.core.logging import configure_logging
from app.core.security import redact

log = logging.getLogger("egi")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    s = get_settings()
    configure_logging(s.log_level, s.log_json)
    if s.env == "hosted" and s.auth_mode == "none":
        raise RuntimeError("Refusing to start a hosted deployment without authentication (set EGI_AUTH_MODE).")
    if s.scheduler_enabled and s.env != "test":
        from app.services import scheduler

        scheduler.start()
    log.info("Ecommerce Growth Intelligence started (env=%s, db=%s)", s.env, "sqlite" if s.is_sqlite else "postgres")
    yield
    if s.scheduler_enabled and s.env != "test":
        from app.services import scheduler

        scheduler.shutdown()


def create_app() -> FastAPI:
    s = get_settings()
    app = FastAPI(title="Ecommerce Growth Intelligence", version=routes_admin.VERSION, lifespan=lifespan,
                  docs_url="/api/docs", openapi_url="/api/openapi.json")
    app.add_middleware(CORSMiddleware, allow_origins=s.cors_origins, allow_credentials=True,
                       allow_methods=["*"], allow_headers=["*"])
    app.middleware("http")(auth.auth_middleware)

    @app.exception_handler(AppError)
    async def app_error(_req: Request, exc: AppError):
        return JSONResponse(status_code=exc.status_code,
                            content={"error": {"code": exc.code, "message": redact(exc.message),
                                               "details": exc.details}})

    @app.exception_handler(RequestValidationError)
    async def validation_error(_req: Request, exc: RequestValidationError):
        msgs = [f"{'.'.join(str(p) for p in e['loc'][1:])}: {e['msg']}" for e in exc.errors()]
        return JSONResponse(status_code=422, content={"error": {"code": "validation_error",
                                                                "message": "; ".join(msgs)[:500]}})

    @app.exception_handler(Exception)
    async def unhandled(req: Request, exc: Exception):
        log.exception("Unhandled error on %s %s", req.method, req.url.path)
        return JSONResponse(status_code=500, content={"error": {
            "code": "internal_error", "message": "Unexpected server error. Details are in the server log."}})

    for r in (auth.router, routes_admin.router, routes_stores.router, routes_data.router, routes_offers.router,
              routes_strategy.router):
        app.include_router(r)

    dist = s.frontend_dist
    if dist.exists():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        async def spa(path: str):
            if path.startswith("api/"):
                return JSONResponse({"error": {"code": "not_found", "message": "Unknown API route."}}, 404)
            candidate = dist / path
            if path and candidate.is_file() and dist in candidate.resolve().parents:
                return FileResponse(candidate)
            return FileResponse(dist / "index.html")
    return app


app = create_app()
