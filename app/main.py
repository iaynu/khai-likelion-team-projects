from datetime import datetime, timedelta

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


@app.on_event("startup")
async def reset_stuck_jobs():
    """서버 재시작 시 PROCESSING 상태로 멈춘 Job을 FAILED로 정리.

    조건: started_at < now-10분  (started_at NULL이면 created_at 기준)
    asyncio.create_task 는 프로세스 종료 시 모두 소멸하므로,
    재시작 후에도 PROCESSING으로 남은 잡은 완료 불가능한 좀비 상태다.
    """
    from sqlalchemy import update as sa_update, or_, and_
    from app.db.session import get_async_session
    from app.models.job import Job, JobStatus

    cutoff = datetime.utcnow() - timedelta(minutes=10)
    try:
        async with get_async_session() as session:
            async with session.begin():
                result = await session.execute(
                    sa_update(Job)
                    .where(
                        Job.status == JobStatus.PROCESSING,
                        or_(
                            Job.started_at < cutoff,
                            and_(Job.started_at.is_(None), Job.created_at < cutoff),
                        ),
                    )
                    .values(
                        status=JobStatus.FAILED,
                        error_message="[SERVER_RESTART] 서버 재시작으로 인해 중단된 작업입니다.",
                        completed_at=datetime.utcnow(),
                    )
                    .execution_options(synchronize_session=False)
                )
        count = result.rowcount
        if count > 0:
            print(f"[Startup] stuck job {count}개 → FAILED 처리 완료")
        else:
            print("[Startup] stuck job 없음")
    except Exception as e:
        # DB 초기화 전 호출되는 경우 등 — 서버 기동을 막지 않음
        print(f"[Startup] reset_stuck_jobs 실패 (무시): {e}")


@app.get("/health")
async def health():
    return {"status": "ok"}
