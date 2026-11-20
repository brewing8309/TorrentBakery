from __future__ import annotations

import hashlib
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from urllib.parse import urlparse

import httpx
from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .. import __url__, __version__
from .. import config as config_mod
from ..clients import QbitError, QBittorrent
from ..config import StashBoxCfg, Tracker
from ..hosts import API_KEY_HOSTS, HOST_NAMES, HOST_SITES
from ..media.files import is_video
from ..publish import prepare, seed, set_artwork
from ..render.post import release_tags
from ..sources import stashbox as stashbox_source
from ..sources.gql import gql
from ..tags import TagMap, load_user_map, save_user_map
from ..trackers import TrackerError, get_publisher, load_templates
from ..trackers.template import compose_credentials, parse_cookie_string
from ..trackers.template import user_dir as user_template_dir
from . import auth as auth_mod
from .jobs import STEPS, JobManager
from .login import allowed_hosts, install as install_login

SEARCH_QUERY = """
query Search($q: String!) {
  findScenes(filter: {q: $q, per_page: 12, sort: "date", direction: DESC}) {
    scenes { id title date studio { name } files { basename } }
  }
}
"""


def create_app(out_root: Path = Path("out"), config_path: Path | None = None, manager: JobManager | None = None, transport: httpx.BaseTransport | None = None, qbit_transport: httpx.BaseTransport | None = None) -> FastAPI:
    app = FastAPI(title="TorrentBakery", docs_url=None, redoc_url=None, openapi_url=None)  # no API browser: the pages are the interface
    templates = Jinja2Templates(directory=Path(__file__).parent / "templates")
    static_dir = Path(__file__).parent / "static"
    app.mount("/static", StaticFiles(directory=static_dir), name="static")
    # stylesheet links carry a content hash, so an update never leaves a browser on the old design
    templates.env.globals["asset_v"] = hashlib.sha256(b"".join(p.read_bytes() for p in sorted(static_dir.glob("*/*.[cj]s*")))).hexdigest()[:10]
    templates.env.globals.update(version=__version__, repo_url=__url__)
    # announce URLs carry the tracker passkey: the pages only ever show the host
    templates.env.filters["datetime"] = lambda ts: time.strftime("%Y-%m-%d %H:%M", time.localtime(ts)) if ts else ""
    templates.env.filters["size"] = lambda n: f"{n / 2**30:.2f} GiB" if n >= 2**30 else f"{n / 2**20:.1f} MiB" if n >= 2**20 else f"{max(1, round(n / 2**10))} KiB"
    templates.env.filters["urlhost"] = lambda u: urlparse(u).netloc or u
    jobs = manager or JobManager(out_root, config_path)
    cfg = lambda: config_mod.load(config_path)  # noqa: E731 - re-read per request so settings edits apply at once

    login_state = install_login(app, templates, config_path)

    tags_file = lambda: (config_path or config_mod.CONFIG_PATH).with_name("tags.toml")  # noqa: E731 - sits next to the config

    def kept_secrets(old: Tracker, site: str, base_url: str, tpls: dict) -> dict:
        """The saved cookie and token stay only with the site they were made for: a profile pointed at another address starts without them,
        so nobody can send them elsewhere by editing (or probing) the address."""
        new = (base_url.strip().rstrip("/") or (tpls[site].base_url if site in tpls else "")).rstrip("/")
        if new == old.effective_base_url:
            return {"cookie": old.cookie, "session_cookie": old.session_cookie}
        return {"cookie": "", "session_cookie": ""}

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

    def job_thumbs(js) -> dict[str, str]:
        """A picture per entry: the Stash scene's screenshot, else the baked cover."""
        thumbs, stash_on = {}, bool(cfg().stash.url)
        for j in js:
            if j.source.startswith("stash:") and j.source[6:].isdigit() and stash_on:
                thumbs[j.id] = f"/stash-thumb/{j.source[6:]}"
            elif j.out_dir and j.out_dir.is_dir():
                cover = next(iter(sorted(j.out_dir.glob("cover.*"))), None)
                if cover:
                    thumbs[j.id] = f"/jobs/{j.id}/files/{cover.name}"
        return thumbs

    # ---- bake ----
    @app.get("/")
    def index(request: Request):
        c = cfg()
        return templates.TemplateResponse(request, "index.html", {"default_host": c.default_host, "cover_source": c.cover_source, "cover_format": c.cover_format, "last": c.last_used, "trackers": list(c.trackers), "tracker_hosts": {n: {"site": t.site, "hosts": list(t.template.image_hosts) if t.template else []} for n, t in c.trackers.items()}, "host_sites": HOST_SITES, "publishable": [n for n, t in c.trackers.items() if t.site], "hosts": HOST_NAMES, "stash": bool(c.stash.url), "has_boxes": bool(c.stashboxes), "roots": c.web_media_roots, "jobs": jobs.recent(), "thumbs": job_thumbs(jobs.recent()), "total_jobs": len(jobs.jobs)})

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
        shared = {j.id: len(jobs.related(j)) - 1 for j in jobs.all()}  # other entries that would go with it (same output folder)
        return templates.TemplateResponse(request, "history.html", {"jobs": jobs.all(), "published": published, "shared": shared, "thumbs": job_thumbs(jobs.all())})

    @app.post("/jobs/{job_id}/delete")
    def job_delete(job_id: str):
        job_or_404(job_id)
        try:
            jobs.delete(job_id)
        except ValueError as e:
            raise HTTPException(409, str(e))
        return RedirectResponse("/history", status_code=303)

    @app.post("/history/clear")
    def history_clear():
        jobs.clear()
        return RedirectResponse("/history", status_code=303)

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

    @app.get("/stashbox/search")
    def stashbox_search(name: str, manual: bool = False):
        """Scene candidates for a picked file, from every configured stash-box; the user makes the final choice."""
        c = cfg()
        terms, date = stashbox_source.search_terms(name) if not manual else ([name.strip()] if name.strip() else [], "")

        def one(item):
            box_name, box = item
            entry = {"name": box_name, "scenes": [], "error": ""}
            if terms:
                try:
                    entry["scenes"] = stashbox_source.find(box.endpoint, box.api_key, terms, date, transport=transport)
                except Exception as exc:  # one broken box must not take the others down
                    entry["error"] = str(exc) or type(exc).__name__
            return entry

        with ThreadPoolExecutor(max_workers=max(1, len(c.stashboxes))) as pool:
            boxes = list(pool.map(one, c.stashboxes.items()))
        return {"term": " / ".join(terms), "terms": terms, "date": date, "boxes": boxes}

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
        c = cfg()  # the form comes back the way it was left: remember the choices (not the per-scene source and stash-box link)
        used = {"tracker": tracker, "upload": upload, "cover_source": cover_source, "cover_format": cover_format, "sfw": "1" if sfw else ""}
        if used != c.last_used:
            c.last_used = used
            config_mod.save(c, config_path)
        job = jobs.submit(source, label=label.strip()[:200], tracker=tracker or None, upload=upload or None, nsfw=not sfw, stashbox=stashbox.strip() or None,
                          cover_source=cover_source or None, cover_format=cover_format or None,
                          publish=False)  # the web UI always stops after the bake: the job page is the review step before the upload
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
        return templates.TemplateResponse(request, "job.html", {"job": job, "title": title, "failed_label": STEPS[job.failed_step][0] if job.failed_step in STEPS else "", "published": published, "pub_error": pub_error, "publishable": [n for n, t in c.trackers.items() if t.site],
                       "job_tracker": job.options.get("tracker") or "", "has_image_host": bool(meta.get("uploads")), "files": job_files(job), "sizes": {f: (job.out_dir / f).stat().st_size for f in job_files(job)}, "post": post, "uploads": meta.get("uploads", {}), "host": meta.get("image_host", ""), "cover_file": meta.get("cover_file", "cover.jpg"),
                       "folder_name": meta.get("folder_name", ""), "folder_path": meta.get("folder_path", ""),
                       "release_title": meta.get("release", {}).get("title") or title,
                       "tags": release_tags(meta.get("release", {}))})

    @app.get("/busy")
    def busy():
        """How many bakes are running; deploy.sh waits for 0 so an update does not kill one."""
        return {"running": sum(1 for j in jobs.jobs.values() if j.status in ("queued", "running"))}

    @app.get("/jobs/{job_id}/state")
    def job_state(job_id: str):
        return jobs.state(job_or_404(job_id))

    @app.get("/jobs/{job_id}/live/{name}")
    def job_live(job_id: str, name: str):
        f = jobs.live_file(job_or_404(job_id), name)
        if f is None:
            raise HTTPException(404)
        return FileResponse(f, headers={"Cache-Control": "no-store"})

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
        warning = ""
        shown = c.trackers[tracker].template.image_hosts if c.trackers[tracker].template else ()
        if shown and p.meta.get("image_host") not in shown:
            warning = (f"The images of this bake are on {p.meta.get('image_host') or 'no host'}, which {tracker} does not show (it blocks other hosts through a proxy). "
                       f"Bake again with {' or '.join(shown)} as the image host, or the post will show broken images.")
        return {"ok": True, "warning": warning, "title": p.title, "tags": " ".join(p.tags), "category": p.category, "categories": form.categories, "description": p.description,
                "image": bool(p.image), "torrent": p.torrent.name}

    @app.post("/jobs/{job_id}/publish")
    def publish_start(job_id: str, tracker: str = Form(...), title: str = Form(""), category: str = Form(""), tags: str = Form(""), description: str = Form("")):
        job = publishable_job(job_id, tracker)
        if job.pub.get("status") in ("queued", "running"):
            raise HTTPException(409, "a publish is already running for this release")
        jobs.submit_publish(job, name=tracker, title=title.strip() or None, category=category.strip() or None, tags=tags.split() or None, description=description.strip() or None,
                            tagmap=TagMap.load(tags_file()), transport=transport)
        return RedirectResponse(f"/jobs/{job.id}#publish", status_code=303)

    @app.post("/jobs/{job_id}/seed")
    def publish_seed(job_id: str, tracker: str = Form(...)):
        """Add an already published release to qBittorrent (again), using the tracker's own torrent file."""
        job = publishable_job(job_id, tracker)
        try:
            r = seed(cfg(), job.out_dir, tracker, transport=transport, qbit_transport=qbit_transport)
        except (TrackerError, QbitError, ValueError, KeyError) as e:
            return {"ok": False, "message": str(e)}
        return {"ok": r["ok"], "message": r["message"]}

    @app.post("/jobs/{job_id}/artwork")
    def publish_artwork(job_id: str, tracker: str = Form(...)):
        """Set the list cover and banner of a published UNIT3D release (again), e.g. after pasting a fresh web session cookie."""
        job = publishable_job(job_id, tracker)
        try:
            r = set_artwork(cfg(), job.out_dir, tracker, transport=transport)
        except (TrackerError, ValueError, KeyError) as e:
            return {"ok": False, "message": str(e)}
        return r

    @app.get("/jobs/{job_id}/publish/state")
    def publish_state(job_id: str):
        pub = job_or_404(job_id).pub
        r = pub.get("result") or {}
        return {"status": pub.get("status", ""), "stage": pub.get("stage", ""), "fraction": pub.get("fraction", 0.0), "error": pub.get("error", ""),
                "torrent_url": r.get("torrent_url", ""), "qbit": r.get("qbit")}

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
        flash, login_state.flash = login_state.flash, None
        setup = login_state.setup_secret()
        tpls, tpl_errors = load_templates()
        return templates.TemplateResponse(request, "settings.html", {"c": c, "hosts": HOST_NAMES, "host_keys": {h: bool(c.hosts.get(h, {}).get("api_key")) for h in API_KEY_HOSTS},
                                                                    "tpls": tpls, "tpl_errors": tpl_errors, "tpl_dir": str(user_template_dir()),
                                                                    "tpl_data": {i: {"name": t.name, "engine": t.engine, "description": t.description, "base_url": t.base_url, "origin": t.origin, "image_hosts": list(t.image_hosts),
                                                                                     "credentials": [{"field": x.field, "label": x.label, "required": x.required, "help": x.help, "cookies": [{"name": k.name, "label": k.label, "level": k.level, "help": k.help} for k in x.cookies]} for x in t.credentials]} for i, t in tpls.items()},
                                                                    # what the Edit button puts into the form: everything except the secrets (announce URL with passkey, cookie)
                                                                    "tracker_data": {n: {"source": t.source, "private": t.private, "site": t.site, "base_url": t.base_url, "category": t.category, "anonymous": t.anonymous,
                                                                                         "auto_publish": t.auto_publish, "title_template": t.title_template,
                                                                                         # which secrets exist (names only, never values): the form shows "saved" per cookie
                                                                                         "saved": {"cookie": {"set": bool(t.cookie), "names": sorted(parse_cookie_string(t.cookie))}, "session_cookie": {"set": bool(t.session_cookie), "names": sorted(parse_cookie_string(t.session_cookie))}}} for n, t in c.trackers.items()},
                                                                    "qbit_password": bool(c.qbit.password), "cookies": {n: bool(t.cookie) for n, t in c.trackers.items()},
                                                                    "tag_ignored": um.get("ignored", []),
                                                                    "auth_flash": flash, "setup_secret": setup and auth_mod.group(setup), "setup_uri": setup and auth_mod.totp_uri(setup, c.auth.username),
                                                                    "env_hosts": allowed_hosts(), "recovery_left": len(c.auth.recovery), "min_password": auth_mod.MIN_PASSWORD})

    @app.post("/settings/tracker")
    async def tracker_add(request: Request, name: str = Form(...), announce: str = Form(""), source: str = Form(""), private: str = Form(""), site: str = Form(""), base_url: str = Form(""),
                          category: str = Form(""), anonymous: str = Form(""), auto_publish: str = Form(""), title_template: str = Form("")):
        form = await request.form()
        c = cfg()
        name = name.strip()
        old = c.trackers.get(name, Tracker())
        urls = [u.strip() for u in announce.splitlines() if u.strip()] or old.announce  # blank keeps the saved ones (they hold the passkey)
        tpls = load_templates()[0]
        if site and site not in tpls:
            raise HTTPException(400, "unknown tracker template")
        if not name or not (urls or (site and (base_url.strip() or tpls[site].base_url))):
            raise HTTPException(400, "name and an announce URL required (or a tracker template and cookie, to fetch it)")
        creds = compose_credentials(tpls.get(site), form, kept_secrets(old, site, base_url, tpls))
        c.trackers[name] = replace(old, announce=urls, source=source.strip(), private=bool(private), site=site, base_url=base_url.strip().rstrip("/"),
                                   **creds, category=category.strip(), anonymous=bool(anonymous), auto_publish=bool(auto_publish and site),
                                   title_template=title_template.strip())
        return save(c)

    @app.post("/settings/tracker/categories")
    async def tracker_categories(request: Request, name: str = Form(""), site: str = Form(""), base_url: str = Form("")):
        """The category names of the tracker as the form is filled in right now (a blank cookie means the saved one), so a profile can be set up before it is saved."""
        c = cfg()
        old, tpls = c.trackers.get(name.strip(), Tracker()), load_templates()[0]
        creds = compose_credentials(tpls.get(site), await request.form(), kept_secrets(old, site, base_url, tpls))
        probe = replace(old, site=site, base_url=base_url.strip().rstrip("/"), **creds)
        probe = replace(probe, base_url=probe.effective_base_url)
        if not site or not probe.base_url:
            return {"ok": False, "message": "pick a tracker template (and enter the site address if the template has none) first"}
        try:
            form = get_publisher("_probe", replace(c, trackers={**c.trackers, "_probe": probe}), transport).fetch_form()
        except (TrackerError, ValueError, KeyError) as e:
            return {"ok": False, "message": str(e)}
        except httpx.HTTPError as e:
            return {"ok": False, "message": f"not reachable: {type(e).__name__}"}
        return {"ok": True, "message": f"{len(form.categories)} categories loaded", "categories": form.categories}

    @app.post("/settings/tracker/{name}/test")
    def tracker_test(name: str):
        try:
            form = get_publisher(name, cfg(), transport).fetch_form()
        except (TrackerError, ValueError, KeyError) as e:
            return {"ok": False, "message": str(e)}
        except httpx.HTTPError as e:
            return {"ok": False, "message": f"not reachable: {type(e).__name__}"}
        return {"ok": True, "message": ("Logged in, upload form found." + ("" if form.announce or form.note else " (No announce URL on the page.)") + (" " + form.note if form.note else "")),
                "categories": form.categories}

    @app.post("/settings/tracker/{name}/announce")
    def tracker_announce(name: str):
        c = cfg()
        try:
            form = get_publisher(name, c, transport).fetch_form()
        except (TrackerError, ValueError, KeyError) as e:
            raise HTTPException(400, str(e))
        if not form.announce:
            raise HTTPException(400, "the site shows no announce URL (UNIT3D sites need none: the tracker puts your passkey in itself)")
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
        new = url.strip().rstrip("/")
        if api_key.strip():  # the key is never shown again, so blank means keep
            c.stash.api_key = api_key.strip()
        elif new != c.stash.url:
            c.stash.api_key = ""  # ... but only for the Stash it was made for
        c.stash.url = new
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

    @app.post("/settings/host/{name}")
    def host_key_set(name: str, api_key: str = Form("")):
        if name not in API_KEY_HOSTS:
            raise HTTPException(404, "this host needs no API key")
        c = cfg()
        if api_key.strip():  # never shown again, so blank means keep
            c.hosts.setdefault(name, {})["api_key"] = api_key.strip()
        return save(c)

    @app.post("/settings/host/{name}/delete")
    def host_key_delete(name: str):
        c = cfg()
        c.hosts.pop(name, None)
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
    def qbit_set(url: str = Form(""), username: str = Form(""), password: str = Form(""), category: str = Form(""), start: str = Form(""), path_map: str | None = Form(None)):
        c = cfg()
        q = c.qbit
        if not password and url.strip().rstrip("/") != q.url:
            q.password = ""  # the saved password belongs to the old address
        q.url, q.username, q.category, q.start = url.strip().rstrip("/"), username.strip(), category.strip(), bool(start)
        if password:  # never shown again, so blank means keep
            q.password = password
        if path_map is not None:  # the settings page edits the table separately; the old textarea form still works
            q.path_map = [tuple(x.strip() for x in line.split("=", 1)) for line in path_map.splitlines() if "=" in line]  # type: ignore[misc]
        return save(c)

    @app.post("/settings/qbit/map")
    def qbit_map_add(local_path: str = Form(...), qbit_path: str = Form(...)):
        c = cfg()
        c.qbit.path_map.append((local_path.strip(), qbit_path.strip()))
        return save(c)

    @app.post("/settings/qbit/map/{i}/delete")
    def qbit_map_delete(i: int):
        c = cfg()
        if 0 <= i < len(c.qbit.path_map):
            c.qbit.path_map.pop(i)
        return save(c)

    @app.post("/settings/qbit/test")
    def qbit_test(url: str = Form(""), username: str = Form(""), password: str = Form("")):
        """Tests what is in the form, saved or not; a blank password means the saved one."""
        saved = cfg().qbit
        tested = url.strip().rstrip("/") or saved.url
        q = replace(saved, url=tested, username=username.strip() if url.strip() else saved.username,
                    password=password or (saved.password if tested == saved.url else ""))  # the saved password only goes to the saved address
        try:
            return {"ok": True, "message": "Connected: " + QBittorrent(q, transport).test() + ("" if tested == saved.url else " (not saved yet: use Save)")}
        except QbitError as e:
            return {"ok": False, "message": str(e)}

    @app.post("/settings/tags/ignore")
    def tags_ignore(stash_tag: str = Form(...)):
        data = load_user_map(tags_file())
        data["ignored"] = sorted({*data.get("ignored", []), stash_tag.strip()})
        save_user_map(data, tags_file())
        return RedirectResponse("/settings#tags", status_code=303)

    @app.post("/settings/tags/delete")
    def tags_delete(stash_tag: str = Form(...)):
        data = load_user_map(tags_file())
        data["ignored"] = [t for t in data.get("ignored", []) if t != stash_tag]
        save_user_map(data, tags_file())
        return RedirectResponse("/settings#tags", status_code=303)

    return app
