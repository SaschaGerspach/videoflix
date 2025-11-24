from django.core.management.base import BaseCommand
from django.conf import settings
import os
import redis

# RQ: unter Windows SimpleWorker verwenden (kein os.fork)
from rq import Queue
from rq.worker import Worker, SimpleWorker


def get_connection():
    url = getattr(settings, "RQ_REDIS_URL", None) or getattr(
        settings, "REDIS_URL", "redis://127.0.0.1:6379/1"
    )
    return redis.from_url(url)


class Command(BaseCommand):
    help = "Start an RQ worker for queues [transcode, default]"

    def add_arguments(self, parser):
        parser.add_argument(
            "--burst",
            action="store_true",
            help="Run in burst mode (exit when queue is empty)",
        )

    def handle(self, *args, **options):
        conn = get_connection()
        queues = [
            Queue("transcode", connection=conn),
            Queue("default", connection=conn),
        ]
        is_windows = os.name == "nt"
        WorkerCls = SimpleWorker if is_windows else Worker

        self.stdout.write(
            f"Starting RQ {'Simple' if is_windows else ''}Worker for queues [transcode, default] using {conn.connection_pool.connection_kwargs.get('host')}:{conn.connection_pool.connection_kwargs.get('port')}"
        )
        worker = WorkerCls(queues, connection=conn)
        worker.work(burst=bool(options.get("burst")))
