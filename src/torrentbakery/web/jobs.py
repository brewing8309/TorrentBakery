from __future__ import annotations

import json
import re
import shutil
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from .. import config as config_mod
from ..pipeline import LIVE_DIR
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
    # live progress of a running bake (the progress page): the planned steps, what each is doing, and what has been found out so far
    plan: list[str] = field(default_factory=list)
    steps: dict[str, dict] = field(default_factory=dict)
    info: dict = field(default_factory=dict)  # release, facts, poster, frames, out (the folder being written)
    started: float = 0.0
    failed_step: str = ""


# the steps of a bake in the order they happen; weight = rough share of the whole time, used for the overall bar
STEPS = {
    "recipe": ("Fetching the recipe", "Looking up the scene's data", 2),
    "probe": ("Weighing the ingredients", "Reading resolution, codec and runtime", 3),
    "sheet": ("Slicing the loaf", "Cutting frames out of the video for the screenshot sheet", 30),
    "cover": ("Glazing the cover", "Stitching short clips into an animation", 25),
    "torrent": ("Into the oven", "Hashing every piece of the file for the torrent", 28),
    "upload": ("Serving the images", "Uploading cover and sheet to the image host", 8),
    "post": ("Plating the post", "Writing the forum post and the tags", 2),
    "publish": ("Off to the tracker", "Uploading the release to the tracker", 6),
}


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
        job.pub = {"status": "queued", "stage": "", "fraction": 0.0, "error": "", "result": None, "tracker": kw.get("name", ""), "t0": time.time()}
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
                                         title=r.get("title", ""), out_dir=out, created=r["created"], finished=r.get("finished", 0.0), failed_step=r.get("failed_step", ""), started=r.get("started", 0.0))
            except (ValueError, KeyError):
                continue  # a damaged line must not take the whole history down

    @staticmethod
    def _record(job: Job) -> dict:
        return {"id": job.id, "source": job.source, "label": job.label, "status": job.status, "error": job.error, "title": job.title,
                "options": job.options, "out_dir": job.out_dir.name if job.out_dir else "", "created": job.created, "finished": job.finished,
                "failed_step": job.failed_step, "started": job.started}

    def _save(self, job: Job) -> None:
        try:
            self.out_root.mkdir(parents=True, exist_ok=True)
            with self._lock, open(self.history_file, "a") as f:
                f.write(json.dumps(self._record(job), ensure_ascii=False) + "\n")
        except OSError:
            pass  # history is a convenience; never fail a bake over it

    # ---- deleting ----
    @staticmethod
    def _busy(job: Job) -> bool:
        return job.status in ("queued", "running") or job.pub.get("status") in ("queued", "running")

    def _remove_files(self, job: Job) -> None:
        """Delete the job's output folder and ZIP, but only what really lies directly inside the output root
        (a damaged history line must never point the deletion elsewhere; the root itself is never removed)."""
        d = job.out_dir
        root = self.out_root.resolve()
        if not d or d.name in ("", ".", "..") or d.is_symlink() or d.parent.resolve() != root:
            return
        shutil.rmtree(d, ignore_errors=True)
        (root / f"{d.name}.zip").unlink(missing_ok=True)

    def _drop(self, targets: list[Job]) -> None:
        for d in {j.out_dir for j in targets if j.out_dir}:
            self._remove_files(next(j for j in targets if j.out_dir == d))
        with self._lock:
            for j in targets:
                self.jobs.pop(j.id, None)
            try:  # rewrite the history without them (it only ever holds finished jobs)
                tmp = self.history_file.with_suffix(".tmp")
                tmp.write_text("".join(json.dumps(self._record(j), ensure_ascii=False) + "\n" for j in sorted(self.jobs.values(), key=lambda j: j.created) if j.finished))
                tmp.replace(self.history_file)
            except OSError:
                pass

    def related(self, job: Job) -> list[Job]:
        """The job plus every other entry that shares its output folder (a re-bake of the same release reuses it): they go together."""
        return [j for j in self.jobs.values() if j is job or (job.out_dir and j.out_dir == job.out_dir)]

    def delete(self, job_id: str) -> int:
        """Remove a finished job with its files. Returns how many history entries went. Raises ValueError while it is still running."""
        job = self.jobs.get(job_id)
        if job is None:
            return 0
        targets = self.related(job)
        if any(self._busy(j) for j in targets):
            raise ValueError("this release is still being baked or published")
        self._drop(targets)
        return len(targets)

    def clear(self) -> tuple[int, int]:
        """Remove every finished job with its files. Returns (removed, still running and therefore kept)."""
        busy = [j for j in self.jobs.values() if self._busy(j)]
        keep_dirs = {j.out_dir for j in busy if j.out_dir}
        targets = [j for j in self.jobs.values() if not self._busy(j) and j.out_dir not in keep_dirs]
        self._drop(targets)
        return len(targets), len(self.jobs)

    @staticmethod
    def _plan(cfg, job: Job) -> list[str]:
        o = job.options
        t = cfg.trackers.get(o.get("tracker") or "")
        plan = ["probe", "sheet", "cover", "torrent"]
        if job.source.startswith("stash:") or o.get("stashbox"):
            plan.insert(0, "recipe")
        if o.get("upload"):
            plan.append("upload")
        plan.append("post")
        if t and t.site and (t.auto_publish if o.get("publish") is None else o.get("publish")):
            plan.append("publish")
        return plan

    @staticmethod
    def _track(job: Job, fraction: float, info: dict) -> None:
        """What the bake reports (see pipeline.bake): which step it is in, how far, and what it found out. A new step ends the running one."""
        now = time.time()
        key = info.pop("key", None)
        for k in ("release", "facts", "poster", "frames"):
            if k in info:
                job.info[k] = info[k]
        if "out" in info:
            job.info["out"] = info["out"]
        if key is None:
            return
        if key not in job.plan:  # a step the plan did not expect: show it anyway, in order
            job.plan.append(key)
        for k, st in job.steps.items():
            if k != key and st["state"] == "running":
                st.update(state="done", fraction=1.0, end=now)
        i = job.plan.index(key)
        for k in job.plan[:i]:  # steps before this one that never ran (cover taken from Stash, no stash-box ...) are skipped
            job.steps.setdefault(k, {"state": "skipped", "fraction": 1.0, "detail": "", "start": now, "end": now})
        st = job.steps.setdefault(key, {"state": "running", "fraction": 0.0, "detail": "", "start": now, "end": 0.0})
        st["fraction"] = max(st["fraction"], fraction) if st["state"] == "running" else st["fraction"]
        for k in ("detail", "file"):
            if k in info:
                st[k] = info[k]
        if info.get("done"):
            st.update(state="done", fraction=1.0, end=now)

    def state(self, job: Job) -> dict:
        """Everything the progress page shows, as JSON."""
        now = time.time()
        plan = job.plan or ["probe", "sheet", "cover", "torrent", "post"]
        steps = []
        for k in plan:
            st = job.steps.get(k, {})
            state = st.get("state", "pending")
            if job.status == "done" and state in ("pending", "running"):
                state = "done"
            label, flavour, _ = STEPS.get(k, (k, "", 1))
            steps.append({"key": k, "label": label, "flavour": flavour, "state": state, "detail": st.get("detail", ""), "fraction": st.get("fraction", 0.0) if state == "running" else (1.0 if state == "done" else 0.0),
                          "took": round((st.get("end") or now) - st["start"], 1) if st and state in ("done", "running") else 0, "file": st.get("file", "")})
        weights = {k: STEPS.get(k, ("", "", 1))[2] for k in plan}
        total = sum(weights.values()) or 1
        overall = sum(weights[s["key"]] * (1.0 if s["state"] in ("done", "skipped") else s["fraction"] if s["state"] == "running" else 0.0) for s in steps) / total
        ahead = sum(1 for j in self.jobs.values() if j is not job and j.status in ("queued", "running") and j.created <= job.created)
        info = job.info
        poster = info.get("poster") or (f"/stash-thumb/{job.source[6:]}" if job.source.startswith("stash:") else "")
        return {"status": job.status, "stage": job.stage, "fraction": 1.0 if job.status == "done" else overall, "error": job.error, "failed_step": job.failed_step,
                "steps": steps, "release": info.get("release"), "facts": info.get("facts"), "poster": poster, "frames": info.get("frames"),
                "ahead": ahead if job.status == "queued" else 0, "started": job.started, "finished": job.finished, "now": now, "label": job.label}

    def live_file(self, job: Job, name: str) -> Path | None:
        """A file the progress page may show while the bake is still writing its folder: sheet frames, and the finished sheet and cover."""
        out = job.info.get("out")
        if not out or job.status not in ("running", "queued"):
            return None
        base = Path(out)
        if re.fullmatch(r"frame-\d{2,3}\.jpg", name):
            f = base / LIVE_DIR / name
        elif name == "contactsheet.jpg" and job.steps.get("sheet", {}).get("state") == "done":
            f = base / name
        elif name == job.steps.get("cover", {}).get("file") and job.steps["cover"]["state"] == "done":
            f = base / name
        else:
            return None
        return f if f.is_file() else None

    def _run(self, job: Job) -> None:
        def progress(stage: str, fraction: float = 0.0, **info) -> None:
            job.stage, job.fraction = stage, fraction
            self._track(job, fraction, info)

        job.status = "running"
        job.started = time.time()
        try:
            cfg = config_mod.load(self.config_path)
            job.plan = self._plan(cfg, job)
            job.info["frames"] = {"done": 0, "total": cfg.sheet_cols * cfg.sheet_rows, "cols": cfg.sheet_cols, "rows": cfg.sheet_rows}  # the empty sheet is shown right away
            job.out_dir = self.runner(cfg, job.source, self.out_root, media_roots=cfg.web_media_roots, progress=progress, **job.options)
            job.status = "done"
            try:
                job.title = json.loads((job.out_dir / "release.json").read_text())["release"]["title"]
            except (OSError, ValueError, KeyError):
                pass
        except Exception as e:  # shown to the user on the job page
            job.status, job.error = "error", f"{type(e).__name__}: {e}"
            job.failed_step = next((k for k in job.plan if job.steps.get(k, {}).get("state") == "running"), "")
        job.finished = time.time()
        self._save(job)
