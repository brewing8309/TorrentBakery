from __future__ import annotations

from pathlib import Path

import httpx
from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from .. import config as config_mod
from ..config import StashBoxCfg, Tracker
from ..hosts import HOST_NAMES
from ..sources.gql import gql
from .jobs import JobManager

SEARCH_QUERY = """
query Search($q: String!) {
  findScenes(filter: {q: $q, per_page: 12, sort: "date", direction: DESC}) {
    scenes { id title date studio { name } files { basename } }
  }
}
"""


def create_app(out_root: Path = Path("out"), config_path: Path | None = None, manager: JobManager | None = None, transport: httpx.BaseTransport | None = None) -> FastAPI:
    app = FastAPI(title="TorrentBakery")
    templates = Jinja2Templates(directory=Path(__file__).parent / "templates")
    jobs = manager or JobManager(out_root, config_path)
    cfg = lambda: config_mod.load(config_path)  # noqa: E731 - re-read per request so settings edits apply at once

    def save(c) -> RedirectResponse:
        config_mod.save(c, config_path)
        return RedirectResponse("/settings", status_code=303)

    def job_or_404(job_id: str):
        job = jobs.jobs.get(job_id)
        if job is None:
            raise HTTPException(404, "unknown job")
        return job

    def job_files(job) -> list[str]:
        return sorted(p.name for p in job.out_dir.iterdir() if p.is_file()) if job.out_dir else []

    # ---- bake ----
    @app.get("/")
    def index(request: Request):
        c = cfg()
        return templates.TemplateResponse(request, "index.html", {"trackers": list(c.trackers), "hosts": HOST_NAMES, "stash": bool(c.stash.url), "roots": c.web_media_roots, "jobs": jobs.recent()})

    @app.get("/search")
    def search(q: str):
        c = cfg()
        if not c.stash.url or not q.strip():
            return []
        data = gql(c.stash.url.rstrip("/") + "/graphql", SEARCH_QUERY, {"q": q}, c.stash.api_key, transport)
        return [
            {"id": s["id"], "title": s["title"] or (s["files"][0]["basename"] if s["files"] else f"Scene {s['id']}"), "date": s["date"] or "",
             "studio": s["studio"]["name"] if s["studio"] else ""}
            for s in data["findScenes"]["scenes"]
        ]

    @app.get("/stash-thumb/{scene_id}")
    def stash_thumb(scene_id: int):
        c = cfg()
        if not c.stash.url:
            raise HTTPException(404)
        headers = {"ApiKey": c.stash.api_key} if c.stash.api_key else {}
        with httpx.Client(transport=transport, timeout=20, headers=headers) as client:
            r = client.get(f"{c.stash.url.rstrip('/')}/scene/{scene_id}/screenshot")
        if r.status_code != 200:
            raise HTTPException(404)
        return Response(r.content, media_type=r.headers.get("content-type", "image/jpeg"), headers={"Cache-Control": "max-age=3600"})

    @app.post("/jobs")
    def create_job(source: str = Form(...), tracker: str = Form(""), upload: str = Form(""), sfw: str = Form(""), stashbox: str = Form("")):
        source = source.strip()
        if not source:
            raise HTTPException(400, "source required")
        if source.isdigit():
            source = f"stash:{source}"
        job = jobs.submit(source, tracker=tracker or None, upload=upload or None, nsfw=not sfw, stashbox=stashbox.strip() or None)
        return RedirectResponse(f"/jobs/{job.id}", status_code=303)

    @app.get("/jobs/{job_id}")
    def job_page(request: Request, job_id: str):
        job = job_or_404(job_id)
        post = ""
        if job.status == "done" and job.out_dir and (job.out_dir / "post.bbcode").exists():
            post = (job.out_dir / "post.bbcode").read_text()
        return templates.TemplateResponse(request, "job.html", {"job": job, "files": job_files(job), "post": post})

    @app.get("/jobs/{job_id}/state")
    def job_state(job_id: str):
        j = job_or_404(job_id)
        return {"status": j.status, "stage": j.stage, "fraction": j.fraction, "error": j.error}

    @app.get("/jobs/{job_id}/files/{name}")
    def job_file(job_id: str, name: str):
        job = job_or_404(job_id)
        if name not in job_files(job):  # only names that exist in the job's folder: no path traversal
            raise HTTPException(404)
        return FileResponse(job.out_dir / name)

    @app.get("/jobs/{job_id}/zip")
    def job_zip(job_id: str):
        job = job_or_404(job_id)
        z = job.out_dir.parent / f"{job.out_dir.name}.zip" if job.out_dir else None
        if not z or not z.exists():
            raise HTTPException(404)
        return FileResponse(z, filename=z.name)

    # ---- settings ----
    @app.get("/settings")
    def settings(request: Request):
        c = cfg()
        return templates.TemplateResponse(request, "settings.html", {"c": c, "hosts": HOST_NAMES})

    @app.post("/settings/tracker")
    def tracker_add(name: str = Form(...), announce: str = Form(...), source: str = Form(""), private: str = Form("")):
        c = cfg()
        urls = [u.strip() for u in announce.splitlines() if u.strip()]
        if not name.strip() or not urls:
            raise HTTPException(400, "name and at least one announce URL required")
        c.trackers[name.strip()] = Tracker(urls, source.strip(), bool(private))
        return save(c)

    @app.post("/settings/tracker/{name}/delete")
    def tracker_delete(name: str):
        c = cfg()
        c.trackers.pop(name, None)
        return save(c)

    @app.post("/settings/stash")
    def stash_set(url: str = Form(""), api_key: str = Form("")):
        c = cfg()
        c.stash.url = url.strip().rstrip("/")
        if api_key.strip():  # the key is never shown again, so blank means keep
            c.stash.api_key = api_key.strip()
        return save(c)

    @app.post("/settings/map")
    def map_add(stash_path: str = Form(...), local_path: str = Form(...)):
        c = cfg()
        c.path_map.append((stash_path.strip(), local_path.strip()))
        return save(c)

    @app.post("/settings/map/{i}/delete")
    def map_delete(i: int):
        c = cfg()
        if 0 <= i < len(c.path_map):
            c.path_map.pop(i)
        return save(c)

    @app.post("/settings/stashbox")
    def box_add(name: str = Form(...), endpoint: str = Form(...), api_key: str = Form("")):
        c = cfg()
        c.stashboxes[name.strip()] = StashBoxCfg(endpoint.strip(), api_key.strip())
        return save(c)

    @app.post("/settings/stashbox/{name}/delete")
    def box_delete(name: str):
        c = cfg()
        c.stashboxes.pop(name, None)
        return save(c)

    @app.post("/settings/host")
    def host_set(name: str = Form(...), options: str = Form("")):
        c = cfg()
        if name not in HOST_NAMES:
            raise HTTPException(400, "unknown host")
        opts = {}
        for line in options.splitlines():
            k, sep, v = line.partition("=")
            if sep and k.strip():
                opts[k.strip()] = v.strip()
        if opts:
            c.hosts[name] = opts
        else:
            c.hosts.pop(name, None)
        return save(c)

    @app.post("/settings/roots")
    def roots_set(roots: str = Form("")):
        c = cfg()
        c.web_media_roots = [r.strip() for r in roots.splitlines() if r.strip()]
        return save(c)

    return app
