from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.core.config import settings
from app.core.logging import setup_logging
from app.api.routers import admin, stores, x_reports, jobs
from app.api.routers import simulations, y_reports

setup_logging("DEBUG" if settings.DEBUG else "INFO")

app = FastAPI(title=settings.APP_TITLE, version=settings.APP_VERSION)

# ── CORS ────────────────────────────────────────────────
# allow_origins: 명시 허용 (로컬 + 특정 Vercel 도메인)
# allow_origin_regex: Vercel Preview 유동 도메인 대응
# prod 배포 시 CORS_ALLOWED_ORIGINS / CORS_ALLOWED_ORIGIN_REGEX 환경변수로 좁힐 것
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ALLOWED_ORIGINS,
    allow_origin_regex=settings.CORS_ALLOWED_ORIGIN_REGEX,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── routers ─────────────────────────────────────────────
app.include_router(admin.router)
app.include_router(stores.router)
app.include_router(x_reports.router)
app.include_router(jobs.router)
app.include_router(simulations.router)
app.include_router(simulations.store_router)
app.include_router(y_reports.router)
app.include_router(y_reports.store_router)


# ── error handlers ──────────────────────────────────────

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """422 Unprocessable Entity → [VALIDATION_ERROR] 포맷으로 통일.

    프론트가 정규식 ^\\[([A-Z_]+)\\] 로 ERROR_CODE를 추출 가능하도록
    다른 에러와 동일한 포맷을 사용한다.
    detail 배열은 디버깅용으로 유지한다.
    """
    errors = exc.errors()
    parts = []
    for e in errors[:5]:   # 최대 5개 필드까지만 노출
        loc = " -> ".join(str(l) for l in e.get("loc", []) if l != "body")
        msg = e.get("msg", "invalid")
        parts.append(f"{loc}: {msg}" if loc else msg)
    detail_str = "; ".join(parts)[:300]
    return JSONResponse(
        status_code=422,
        content={
            "error_message": f"[VALIDATION_ERROR] {detail_str}",
            "detail": errors,
        },
    )


@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception):
    return JSONResponse(
        status_code=500,
        content={"code": "INTERNAL_ERROR", "message": str(exc)},
    )


@app.get("/health")
async def health():
    return {"status": "ok"}
