from __future__ import annotations

import json
import os
import time
from dataclasses import replace
from pathlib import Path
from urllib.parse import urlparse

import httpx
from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from .. import config as config_mod
from ..clients import QbitError, QBittorrent
from ..config import SITE_SOURCES, StashBoxCfg, Tracker
from ..hosts import HOST_NAMES
from ..media.files import is_video
from ..publish import prepare
from ..render.post import format_tags
from ..sources.gql import gql
from ..tags import TagMap, load_user_map, save_user_map
from ..trackers import SITES, TrackerError, get_publisher
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

    tags_file = lambda: (config_path or config_mod.CONFIG_PATH).with_name("tags.toml")  # noqa: E731 - sits next to the config

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
        return templates.TemplateResponse(request, "index.html", {"default_host": c.default_host, "cover_source": c.cover_source, "cover_format": c.cover_format, "trackers": list(c.trackers), "publishable": [n for n, t in c.trackers.items() if t.site], "hosts": HOST_NAMES, "stash": bool(c.stash.url), "roots": c.web_media_roots, "jobs": jobs.recent(), "total_jobs": len(jobs.jobs)})

    @app.get("/history")
    def history(request: Request):
        published = {}
        for j in jobs.all():
            f = j.out_dir / "publish.json" if j.out_dir else None
            if f and f.exists():
                try:
                    published[j.id] = json.loads(f.read_text())
                except ValueError:
                    pass
        return templates.TemplateResponse(request, "history.html", {"jobs": jobs.all(), "published": published})

    @app.get("/browse")
    def browse(path: str = ""):
        """Directory listing for the source picker. Only inside the permitted media roots; symlinks pointing out of them are not followed."""
        roots = [Path(r).resolve() for r in cfg().web_media_roots if Path(r).is_dir()]
        if not path:
            return {"path": "", "parent": None, "roots": True, "entries": [{"name": r.name or str(r), "path": str(r), "type": "dir"} for r in roots]}
        p = Path(path).resolve()
        root = next((r for r in roots if p == r or p.is_relative_to(r)), None)
        if root is None or not p.is_dir():
            raise HTTPException(403, "not inside a permitted media folder")
        dirs, files, others = [], [], 0
        try:
            for e in os.scandir(p):
                if e.name.startswith("."):
                    continue
                real = Path(e.path).resolve()
                if not (real == root or real.is_relative_to(root)):
                    continue  # symlink leading out of the root
                if e.is_dir():
                    dirs.append({"name": e.name, "path": e.path, "type": "dir"})
                elif is_video(e.name):
                    files.append({"name": e.name, "path": e.path, "type": "file", "size": e.stat().st_size})
                else:
                    others += 1
        except OSError as exc:
            raise HTTPException(403, f"cannot read folder: {exc.strerror}")
        key = lambda x: x["name"].lower()  # noqa: E731
        parent = "" if p == root else str(p.parent)
        return {"path": str(p), "parent": parent, "roots": False, "others": others, "entries": (sorted(dirs, key=key) + sorted(files, key=key))[:2000]}

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
                   cover_source: str = Form(""), cover_format: str = Form(""), publish: str = Form("")):
        source = source.strip()
        if not source:
            raise HTTPException(400, "source required")
        if source.isdigit():
            source = f"stash:{source}"
        if cover_source not in ("", "own", "stash") or cover_format not in ("", "webp", "gif"):
            raise HTTPException(400, "invalid cover option")
        job = jobs.submit(source, label=label.strip()[:200], tracker=tracker or None, upload=upload or None, nsfw=not sfw, stashbox=stashbox.strip() or None,
                          cover_source=cover_source or None, cover_format=cover_format or None,
                          publish={"yes": True, "no": False}.get(publish))  # "" = follow the profile's auto_publish
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
        published, pub_error = {}, ""
        if job.out_dir and (job.out_dir / "publish.json").exists():
            try:
                published = json.loads((job.out_dir / "publish.json").read_text())
            except ValueError:
                pass
        if job.out_dir and (job.out_dir / "publish-error.txt").exists():
            pub_error = (job.out_dir / "publish-error.txt").read_text().strip()
        c = cfg()
        return templates.TemplateResponse(request, "job.html", {"job": job, "title": title, "published": published, "pub_error": pub_error, "publishable": [n for n, t in c.trackers.items() if t.site],
                       "job_tracker": job.options.get("tracker") or "", "has_image_host": bool(meta.get("uploads")), "files": job_files(job), "post": post, "uploads": meta.get("uploads", {}), "host": meta.get("image_host", ""), "cover_file": meta.get("cover_file", "cover.jpg"),
                       "folder_name": meta.get("folder_name", ""), "folder_path": meta.get("folder_path", ""),
                       "release_title": meta.get("release", {}).get("title") or title,
                       "tags": format_tags(meta.get("release", {}).get("tags", []))})

    @app.get("/jobs/{job_id}/state")
    def job_state(job_id: str):
        j = job_or_404(job_id)
        return {"status": j.status, "stage": j.stage, "fraction": j.fraction, "error": j.error}

    # ---- publish ----
    def publishable_job(job_id: str, tracker: str):
        job = job_or_404(job_id)
        if job.status != "done" or not job.out_dir:
            raise HTTPException(409, "the bake is not finished")
        t = cfg().trackers.get(tracker)
        if t is None or not t.site:
            raise HTTPException(400, f"{tracker!r} is not a publishing tracker profile (set a site in Settings)")
        return job

    @app.get("/jobs/{job_id}/publish/preview")
    def publish_preview(job_id: str, tracker: str):
        """Reads the tracker's upload form (category list) and shows what would be sent. Nothing is posted."""
        job = publishable_job(job_id, tracker)
        c = cfg()
        try:
            p = prepare(c, job.out_dir, tracker, tagmap=TagMap.load(tags_file()), build_torrent=False)
            form = get_publisher(tracker, c, transport).fetch_form()
        except (TrackerError, ValueError, KeyError, OSError) as e:
            return {"ok": False, "message": str(e)}
        return {"ok": True, "title": p.title, "tags": " ".join(p.tags), "category": p.category, "categories": form.categories, "suggestions": p.suggestions,
                "image": bool(p.image), "torrent": p.torrent.name}

    @app.post("/jobs/{job_id}/publish")
    def publish_start(job_id: str, tracker: str = Form(...), title: str = Form(""), category: str = Form(""), tags: str = Form("")):
        job = publishable_job(job_id, tracker)
        if job.pub.get("status") in ("queued", "running"):
            raise HTTPException(409, "a publish is already running for this release")
        jobs.submit_publish(job, name=tracker, title=title.strip() or None, category=category.strip() or None, tags=tags.split() or None,
                            tagmap=TagMap.load(tags_file()), transport=transport)
        return RedirectResponse(f"/jobs/{job.id}#publish", status_code=303)

    @app.get("/jobs/{job_id}/publish/state")
    def publish_state(job_id: str):
        pub = job_or_404(job_id).pub
        r = pub.get("result") or {}
        return {"status": pub.get("status", ""), "stage": pub.get("stage", ""), "fraction": pub.get("fraction", 0.0), "error": pub.get("error", ""),
                "torrent_url": r.get("torrent_url", ""), "qbit": r.get("qbit")}

    @app.post("/jobs/{job_id}/tags")
    def publish_tag_teach(job_id: str, stash_tag: str = Form(...), tracker_tags: str = Form(""), tracker: str = Form(""), ignore: str = Form("")):
        """Accept (map) or ignore a suggestion from the publish preview. tracker "" = for every tracker."""
        job_or_404(job_id)
        data = load_user_map(tags_file())
        if ignore:
            data["ignored"] = sorted({*data.get("ignored", []), stash_tag})
        elif tracker_tags.strip():
            (data.setdefault("tracker", {}).setdefault(tracker, {}) if tracker else data.setdefault("tags", {}))[stash_tag] = tracker_tags.strip()
        else:
            raise HTTPException(400, "tracker tags required")
        save_user_map(data, tags_file())
        return {"ok": True}

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
        um = load_user_map(tags_file())
        return templates.TemplateResponse(request, "settings.html", {"c": c, "hosts": HOST_NAMES, "hamster_key": bool(c.hosts.get("hamsterimg", {}).get("api_key")), "sites": SITES,
                                                                    "qbit_password": bool(c.qbit.password), "cookies": {n: bool(t.cookie) for n, t in c.trackers.items()},
                                                                    "tag_default": um.get("tags", {}), "tag_tracker": um.get("tracker", {}), "tag_ignored": um.get("ignored", [])})

    @app.post("/settings/tracker")
    def tracker_add(name: str = Form(...), announce: str = Form(""), source: str = Form(""), private: str = Form(""), site: str = Form(""), base_url: str = Form(""),
                    cookie: str = Form(""), category: str = Form(""), anonymous: str = Form(""), auto_publish: str = Form(""), title_template: str = Form("")):
        c = cfg()
        name = name.strip()
        old = c.trackers.get(name, Tracker())
        urls = [u.strip() for u in announce.splitlines() if u.strip()] or old.announce  # blank keeps the saved ones (they hold the passkey)
        if not name or not (urls or (site and base_url.strip())):
            raise HTTPException(400, "name and an announce URL required (or a site address and cookie, to fetch it)")
        if site and site not in SITE_SOURCES:
            raise HTTPException(400, "unknown site type")
        c.trackers[name] = replace(old, announce=urls, source=source.strip(), private=bool(private), site=site, base_url=base_url.strip().rstrip("/"),
                                   cookie=cookie.strip() or old.cookie, category=category.strip(), anonymous=bool(anonymous), auto_publish=bool(auto_publish and site),
                                   title_template=title_template.strip())
        return save(c)

    @app.post("/settings/tracker/{name}/test")
    def tracker_test(name: str):
        try:
            form = get_publisher(name, cfg(), transport).fetch_form()
        except (TrackerError, ValueError, KeyError) as e:
            return {"ok": False, "message": str(e)}
        except httpx.HTTPError as e:
            return {"ok": False, "message": f"not reachable: {type(e).__name__}"}
        return {"ok": True, "message": "Logged in, upload form found." + ("" if form.announce else " (No announce URL on the page.)"), "categories": form.categories}

    @app.post("/settings/tracker/{name}/announce")
    def tracker_announce(name: str):
        c = cfg()
        try:
            form = get_publisher(name, c, transport).fetch_form()
        except (TrackerError, ValueError, KeyError) as e:
            raise HTTPException(400, str(e))
        if not form.announce:
            raise HTTPException(400, "the upload page shows no announce URL")
        c.trackers[name].announce = [form.announce]
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

    @app.post("/settings/qbit")
    def qbit_set(url: str = Form(""), username: str = Form(""), password: str = Form(""), category: str = Form(""), start: str = Form(""), path_map: str = Form("")):
        c = cfg()
        q = c.qbit
        q.url, q.username, q.category, q.start = url.strip().rstrip("/"), username.strip(), category.strip(), bool(start)
        if password:  # never shown again, so blank means keep
            q.password = password
        q.path_map = [tuple(x.strip() for x in line.split("=", 1)) for line in path_map.splitlines() if "=" in line]  # type: ignore[misc]
        return save(c)

    @app.post("/settings/qbit/test")
    def qbit_test():
        try:
            return {"ok": True, "message": "Connected: " + QBittorrent(cfg().qbit, transport).test()}
        except QbitError as e:
            return {"ok": False, "message": str(e)}

    @app.post("/settings/tags")
    def tags_add(stash_tag: str = Form(...), tracker_tags: str = Form(...), tracker: str = Form("")):
        data = load_user_map(tags_file())
        (data.setdefault("tracker", {}).setdefault(tracker, {}) if tracker else data.setdefault("tags", {}))[stash_tag.strip()] = tracker_tags.strip()
        save_user_map(data, tags_file())
        return RedirectResponse("/settings#tags", status_code=303)

    @app.post("/settings/tags/ignore")
    def tags_ignore(stash_tag: str = Form(...)):
        data = load_user_map(tags_file())
        data["ignored"] = sorted({*data.get("ignored", []), stash_tag.strip()})
        save_user_map(data, tags_file())
        return RedirectResponse("/settings#tags", status_code=303)

    @app.post("/settings/tags/delete")
    def tags_delete(kind: str = Form(...), stash_tag: str = Form(...), tracker: str = Form("")):
        data = load_user_map(tags_file())
        if kind == "ignored":
            data["ignored"] = [t for t in data.get("ignored", []) if t != stash_tag]
        elif kind == "map":
            (data.get("tracker", {}).get(tracker, {}) if tracker else data.get("tags", {})).pop(stash_tag, None)
        save_user_map(data, tags_file())
        return RedirectResponse("/settings#tags", status_code=303)

    return app
