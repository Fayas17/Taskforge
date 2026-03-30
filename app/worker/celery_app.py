from celery import Celery

from app.core.config import get_settings

settings = get_settings()

celery_app = Celery(
    "taskforge",
    broker=settings.CELERY_BROKER_URL,
    backend=settings.CELERY_RESULT_BACKEND,
    include=["app.worker.tasks"],
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    # Task is only removed from the queue after it completes successfully.
    # If the worker crashes mid-task, Redis re-queues it automatically.
    task_acks_late=True,
    # Process one task at a time per worker — prevents head-of-line blocking
    # when large CSV tasks are in the queue alongside small ones.
    worker_prefetch_multiplier=1,
    task_routes={
        "app.worker.tasks.process_csv": {"queue": "csv_processing"},
    },
)
