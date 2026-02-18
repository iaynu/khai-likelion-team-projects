from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.core.config import settings
from app.core.logging import setup_logging
from app.api.routers import admin, stores, x_reports

setup_logging("DEBUG" if settings.DEBUG else "INFO")

app = FastAPI(title=settings.APP_TITLE, version=settings.APP_VERSION)

# ── routers ─────────────────────────────────────────────
app.include_router(admin.router)
app.include_router(stores.router)
app.include_router(x_reports.router)


# ── unified error handler ───────────────────────────────
@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception):
    return JSONResponse(
        status_code=500,
        content={"code": "INTERNAL_ERROR", "message": str(exc)},
    )


@app.get("/health")
async def health():
    return {"status": "ok"}
