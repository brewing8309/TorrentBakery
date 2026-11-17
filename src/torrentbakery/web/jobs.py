from __future__ import annotations

import json
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from .. import config as config_mod
from ..publish import publish as publish_release
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
    title: str = ""  # release title once baked
    out_dir: Path | None = None
    created: float = field(default_factory=time.time)
    finished: float = 0.0
    pub: dict = field(default_factory=dict)  # state of the latest manual publish: status, stage, fraction, error, result (not persisted; publish.json on disk is)


class JobManager:
    """Runs bakes one at a time in a worker thread (hashing and ffmpeg are disk/CPU heavy).
    Finished jobs are appended to <out>/history.jsonl and reloaded at start, so the history survives restarts;
    the results themselves stay on disk in the output folder."""

    def __init__(self, out_root: Path, config_path: Path | None = None, runner=bake_source, publisher=publish_release):
        self.out_root, self.config_path, self.runner, self.publisher = out_root, config_path, runner, publisher
        self.history_file = out_root / "history.jsonl"
        self.jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        self._pool = ThreadPoolExecutor(max_workers=1)
        self._load()

    def submit(self, source: str, label: str = "", **options) -> Job:
        job = Job(source, options, label)
        with self._lock:
            self.jobs[job.id] = job
        self._pool.submit(self._run, job)
        return job

    def submit_publish(self, job: Job, **kw) -> None:
        """Queue an upload of a finished bake behind whatever else is running (it may hash a tracker-specific torrent)."""
        job.pub = {"status": "queued", "stage": "", "fraction": 0.0, "error": "", "result": None, "tracker": kw.get("name", "")}
        self._pool.submit(self._run_publish, job, kw)

    def _run_publish(self, job: Job, kw: dict) -> None:
        def progress(stage: str, fraction: float = 0.0) -> None:
            job.pub["stage"], job.pub["fraction"] = stage, fraction

        job.pub["status"] = "running"
        try:
            job.pub["result"] = self.publisher(config_mod.load(self.config_path), job.out_dir, progress=progress, **kw)
            job.pub["status"] = "done"
        except Exception as e:  # shown on the job page
            job.pub["status"], job.pub["error"] = "error", f"{type(e).__name__}: {e}"

    def all(self) -> list[Job]:
        return sorted(self.jobs.values(), key=lambda j: j.created, reverse=True)

    def recent(self, n: int = 8) -> list[Job]:
        return self.all()[:n]

    def _load(self) -> None:
        if not self.history_file.exists():
            return
        for line in self.history_file.read_text().splitlines():
            try:
                r = json.loads(line)
                out = self.out_root / r["out_dir"] if r.get("out_dir") else None
                self.jobs[r["id"]] = Job(r["source"], r.get("options", {}), r.get("label", ""), r["id"], r["status"], error=r.get("error", ""),
                                         title=r.get("title", ""), out_dir=out, created=r["created"], finished=r.get("finished", 0.0))
            except (ValueError, KeyError):
                continue  # a damaged line must not take the whole history down

    def _save(self, job: Job) -> None:
        rec = {"id": job.id, "source": job.source, "label": job.label, "status": job.status, "error": job.error, "title": job.title,
               "options": job.options, "out_dir": job.out_dir.name if job.out_dir else "", "created": job.created, "finished": job.finished}
        try:
            self.out_root.mkdir(parents=True, exist_ok=True)
            with self._lock, open(self.history_file, "a") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        except OSError:
            pass  # history is a convenience; never fail a bake over it

    def _run(self, job: Job) -> None:
        def progress(stage: str, fraction: float = 0.0) -> None:
            job.stage, job.fraction = stage, fraction

        job.status = "running"
        try:
            cfg = config_mod.load(self.config_path)
            job.out_dir = self.runner(cfg, job.source, self.out_root, media_roots=cfg.web_media_roots, progress=progress, **job.options)
            job.status = "done"
            try:
                job.title = json.loads((job.out_dir / "release.json").read_text())["release"]["title"]
            except (OSError, ValueError, KeyError):
                pass
        except Exception as e:  # shown to the user on the job page
            job.status, job.error = "error", f"{type(e).__name__}: {e}"
        job.finished = time.time()
        self._save(job)
