from __future__ import annotations

import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from .. import config as config_mod
from ..service import bake_source


@dataclass
class Job:
    source: str
    options: dict
    label: str = ""  # what the user picked (scene title), shown instead of "stash:123"
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:10])
    status: str = "queued"  # queued | running | done | error
    stage: str = ""
    fraction: float = 0.0
    error: str = ""
    out_dir: Path | None = None
    created: float = field(default_factory=time.time)


class JobManager:
    """Runs bakes one at a time in a worker thread (hashing and ffmpeg are disk/CPU heavy); state is in memory, results stay on disk."""

    def __init__(self, out_root: Path, config_path: Path | None = None, runner=bake_source):
        self.out_root, self.config_path, self.runner = out_root, config_path, runner
        self.jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        self._pool = ThreadPoolExecutor(max_workers=1)

    def submit(self, source: str, label: str = "", **options) -> Job:
        job = Job(source, options, label)
        with self._lock:
            self.jobs[job.id] = job
        self._pool.submit(self._run, job)
        return job

    def recent(self) -> list[Job]:
        return sorted(self.jobs.values(), key=lambda j: j.created, reverse=True)[:20]

    def _run(self, job: Job) -> None:
        def progress(stage: str, fraction: float = 0.0) -> None:
            job.stage, job.fraction = stage, fraction

        job.status = "running"
        try:
            cfg = config_mod.load(self.config_path)
            job.out_dir = self.runner(cfg, job.source, self.out_root, media_roots=cfg.web_media_roots, progress=progress, **job.options)
            job.status = "done"
        except Exception as e:  # shown to the user on the job page
            job.status, job.error = "error", f"{type(e).__name__}: {e}"
