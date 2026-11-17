from __future__ import annotations

import json
import time
from pathlib import Path
from urllib.parse import urlparse

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
    # announce URLs carry the tracker passkey: the pages only ever show the host
    templates.env.filters["datetime"] = lambda ts: time.strftime("%Y-%m-%d %H:%M", time.localtime(ts)) if ts else ""
    templates.env.filters["urlhost"] = lambda u: urlparse(u).netloc or u
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
        return sorted(p.name for p in job.out_dir.iterdir() if p.is_file()) if job.out_dir and job.out_dir.is_dir() else []

    # ---- bake ----
    @app.get("/")
    def index(request: Request):
        c = cfg()
        return templates.TemplateResponse(request, "index.html", {"default_host": c.default_host, "cover_source": c.cover_source, "cover_format": c.cover_format, "trackers": list(c.trackers), "hosts": HOST_NAMES, "stash": bool(c.stash.url), "roots": c.web_media_roots, "jobs": jobs.recent(), "total_jobs": len(jobs.jobs)})

    @app.get("/history")
    def history(request: Request):
        return templates.TemplateResponse(request, "history.html", {"jobs": jobs.all()})

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
    def create_job(source: str = Form(...), label: str = Form(""), tracker: str = Form(""), upload: str = Form(""), sfw: str = Form(""), stashbox: str = Form(""),
                   cover_source: str = Form(""), cover_format: str = Form("")):
        source = source.strip()
        if not source:
            raise HTTPException(400, "source required")
        if source.isdigit():
            source = f"stash:{source}"
        if cover_source not in ("", "own", "stash") or cover_format not in ("", "webp", "gif"):
            raise HTTPException(400, "invalid cover option")
        job = jobs.submit(source, label=label.strip()[:200], tracker=tracker or None, upload=upload or None, nsfw=not sfw, stashbox=stashbox.strip() or None,
                          cover_source=cover_source or None, cover_format=cover_format or None)
        return RedirectResponse(f"/jobs/{job.id}", status_code=303)

    @app.get("/jobs/{job_id}")
    def job_page(request: Request, job_id: str):
        job = job_or_404(job_id)
        post, meta = "", {}
        if job.status == "done" and job.out_dir and (job.out_dir / "post.bbcode").exists():
            post = (job.out_dir / "post.bbcode").read_text()
            if (job.out_dir / "release.json").exists():
                meta = json.loads((job.out_dir / "release.json").read_text())
        title = job.label or job.title or meta.get("release", {}).get("title") or job.source.rsplit("/", 1)[-1]
        return templates.TemplateResponse(request, "job.html", {"job": job, "title": title, "files": job_files(job), "post": post, "uploads": meta.get("uploads", {}), "host": meta.get("image_host", ""), "cover_file": meta.get("cover_file", "cover.jpg")})

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
        return templates.TemplateResponse(request, "settings.html", {"c": c, "hosts": HOST_NAMES, "hamster_key": bool(c.hosts.get("hamsterimg", {}).get("api_key"))})

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

    @app.post("/settings/stash/test")
    def stash_test(url: str = Form(""), api_key: str = Form("")):
        c = cfg()
        url = url.strip().rstrip("/") or c.stash.url
        key = api_key.strip() or (c.stash.api_key if url == c.stash.url else "")  # blank key = the saved one
        if not url:
            return {"ok": False, "message": "Enter a Stash URL first."}
        try:
            d = gql(url + "/graphql", "query { version { version } configuration { general { stashBoxes { name } } } }", {}, key, transport)
        except httpx.HTTPStatusError as e:
            code = e.response.status_code
            hint = "The API key is missing or wrong." if code in (401, 403) else f"Response: HTTP {code}."
            return {"ok": False, "message": f"Stash rejected the request. {hint}"}
        except httpx.HTTPError:
            return {"ok": False, "message": f"{url} is not reachable. Check address and port, and that Stash is running."}
        except Exception as e:
            return {"ok": False, "message": f"Unexpected response: {e}"}
        boxes = [b["name"] for b in d["configuration"]["general"]["stashBoxes"]]
        extra = f" Linked stash-boxes: {', '.join(boxes)}." if boxes else " No stash-box is configured in Stash."
        return {"ok": True, "message": f"Connected to Stash {d['version']['version'] or '(development build)'}.{extra}"}

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

    @app.post("/settings/hamsterimg")
    def hamsterimg_set(api_key: str = Form("")):
        c = cfg()
        if api_key.strip():  # never shown again, so blank means keep
            c.hosts.setdefault("hamsterimg", {})["api_key"] = api_key.strip()
        return save(c)

    @app.post("/settings/hamsterimg/delete")
    def hamsterimg_delete():
        c = cfg()
        c.hosts.pop("hamsterimg", None)
        return save(c)

    @app.post("/settings/default-host")
    def default_host_set(host: str = Form("")):
        c = cfg()
        if host and host not in HOST_NAMES:
            raise HTTPException(400, "unknown host")
        c.default_host = host
        return save(c)

    @app.post("/settings/cover")
    def cover_set(source: str = Form("own"), format: str = Form("webp"), clips: int = Form(0), width: int = Form(480)):
        c = cfg()
        if source not in ("own", "stash") or format not in ("webp", "gif") or not 0 <= clips <= 30 or not 160 <= width <= 1280:
            raise HTTPException(400, "invalid cover setting")
        c.cover_source, c.cover_format, c.cover_clips, c.cover_width = source, format, clips, width
        return save(c)

    @app.post("/settings/roots")
    def roots_set(roots: str = Form("")):
        c = cfg()
        c.web_media_roots = [r.strip() for r in roots.splitlines() if r.strip()]
        return save(c)

    return app
