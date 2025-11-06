from __future__ import annotations

import importlib
import sys
from typing import Any, Callable

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Start a Windows-safe RQ worker for the transcode queue."

    def add_arguments(self, parser):
        parser.add_argument(
            "--burst",
            action="store_true",
            help="Run in burst mode (exit after queue is empty).",
        )

    def handle(self, *args, **options):
        queue_name = getattr(settings, "RQ_QUEUE_TRANSCODE", "transcode").strip()
        if not queue_name:
            raise CommandError("RQ_QUEUE_TRANSCODE is not configured.")

        get_worker = self._resolve_get_worker()

        burst = bool(options.get("burst"))
        burst_label = str(burst).lower()

        worker_kwargs = self._worker_kwargs()
        worker_description = worker_kwargs.pop("worker_description", "")

        base_message = f"Starting RQ worker for queue '{queue_name}' (burst={burst_label})."
        self.stdout.write(self.style.SUCCESS(base_message))
        if worker_description:
            self.stdout.write(self.style.SUCCESS(f"Worker class: {worker_description}"))

        worker = self._init_worker(get_worker, queue_name, worker_kwargs)
        worker.work(burst=burst)

    def _resolve_get_worker(self) -> Callable[..., Any]:
        try:
            rq_module = importlib.import_module("django_rq")
        except ImportError as exc:
            raise CommandError("django_rq is required to run this command.") from exc

        get_worker = getattr(rq_module, "get_worker", None)
        if not callable(get_worker):
            raise CommandError("django_rq is required for this command.")
        return get_worker

    def _worker_kwargs(self) -> dict[str, Any]:
        """
        Prefer SimpleWorker on Windows where fork-based workers are unsupported.
        """
        if not sys.platform.lower().startswith("win"):
            return {}

        try:
            rq_module = importlib.import_module("rq")
            simple_worker = getattr(rq_module, "SimpleWorker", None)
        except ImportError:
            simple_worker = None

        if simple_worker is None:
            return {}

        return {"worker_class": simple_worker, "worker_description": "rq.SimpleWorker"}

    def _init_worker(
        self,
        get_worker: Callable[..., Any],
        queue_name: str,
        worker_kwargs: dict[str, Any],
    ):
        try:
            return get_worker(queue_name, **worker_kwargs)
        except TypeError:
            if "worker_class" in worker_kwargs:
                # Retry without the optional kwarg for older django_rq versions.
                return get_worker(queue_name)
            raise
