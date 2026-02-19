"""
Celery Application
비동기 작업 처리를 위한 Celery 앱 설정
"""
from celery import Celery
from app.core.config import settings

# Celery 앱 생성
celery_app = Celery(
    "backbone_worker",
    broker=settings.CELERY_BROKER_URL,
    backend=settings.CELERY_RESULT_BACKEND,
    include=["tasks.x_report_task"]  # Task 모듈
)

# Celery 설정
celery_app.conf.update(
    # 타임존
    timezone="Asia/Seoul",
    enable_utc=True,

    # Task 설정
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",

    # Worker 설정
    worker_prefetch_multiplier=1,  # 한 번에 하나씩 처리
    task_acks_late=True,  # Task 완료 후 ACK
    task_reject_on_worker_lost=True,  # Worker 중단 시 Task 재시도

    # 재시도 설정
    task_track_started=True,
    task_time_limit=3600,  # 1시간 제한
    task_soft_time_limit=3000,  # 50분 경고

    # 결과 만료
    result_expires=3600,  # 1시간 후 결과 삭제
)

if __name__ == "__main__":
    celery_app.start()
