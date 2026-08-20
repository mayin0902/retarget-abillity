"""Low-overhead process instrumentation for individual pipeline stages."""

from __future__ import annotations

import time
import uuid
from datetime import UTC, datetime
from types import TracebackType

import psutil

from .models import StageEvent


class StageTimer:
    def __init__(
        self,
        run_id: str,
        stage: str,
        task_id: str | None = None,
        candidate_id: str | None = None,
    ) -> None:
        self.run_id = run_id
        self.stage = stage
        self.task_id = task_id
        self.candidate_id = candidate_id
        self.process = psutil.Process()
        self.result: StageEvent | None = None

    def __enter__(self) -> StageTimer:
        self.started_at = datetime.now(UTC)
        self.wall_start = time.perf_counter()
        self.cpu_start = time.process_time()
        self.rss_start = self.process.memory_info().rss
        return self

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool:
        del traceback
        finished_at = datetime.now(UTC)
        memory = self.process.memory_info()
        rss_end = memory.rss
        peak_rss = max(self.rss_start, rss_end, int(getattr(memory, "peak_wset", 0)))
        status = "FAILED" if exception is not None else "COMPLETED"
        self.result = StageEvent(
            event_id=f"stage-{uuid.uuid4().hex}",
            run_id=self.run_id,
            task_id=self.task_id,
            candidate_id=self.candidate_id,
            stage=self.stage,
            status=status,
            started_at=self.started_at,
            finished_at=finished_at,
            wall_seconds=time.perf_counter() - self.wall_start,
            cpu_seconds=time.process_time() - self.cpu_start,
            rss_start_bytes=self.rss_start,
            rss_end_bytes=rss_end,
            peak_rss_bytes=peak_rss,
            error_type=exception_type.__name__ if exception_type else None,
            error_summary=str(exception)[:500] if exception else None,
        )
        return False
