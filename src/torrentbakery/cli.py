from __future__ import annotations

import tempfile
from pathlib import Path
from urllib.parse import urlparse

import typer

from . import config as config_mod
from .config import StashBoxCfg, Tracker
from .pipeline import bake
from .sources import stash as stash_source
from .sources import stashbox as stashbox_source

app = typer.Typer(help="TorrentBakery – torrent, screenshot sheet and forum post from a video file or Stash scene.", no_args_is_help=True)
tracker_app = typer.Typer(help="Manage saved tracker profiles.", no_args_is_help=True)
stash_app = typer.Typer(help="Stash and stash-box connections.", no_args_is_help=True)
app.add_typer(tracker_app, name="tracker")
app.add_typer(stash_app, name="stash")


@app.callback()
def main() -> None:
    """TorrentBakery."""


@app.command("bake")
def bake_cmd(
    source: str = typer.Argument(..., help="Video file, or stash:<scene-id>"),
    out: Path = typer.Option(Path("out"), "--out", "-o", help="Output root directory"),
    tracker: str = typer.Option(None, "--tracker", "-t", help="Tracker profile (see `tb tracker list`)"),
    cover: Path = typer.Option(None, "--cover", exists=True, dir_okay=False, help="Use this image as main image"),
    stashbox: str = typer.Option(None, "--stashbox", help="Scene URL on a stash-box, e.g. https://stashdb.org/scenes/<id> (for plain files)"),
) -> None:
    cfg = config_mod.load()
    if tracker and tracker not in cfg.trackers:
        raise typer.BadParameter(f"unknown tracker profile {tracker!r}; known: {', '.join(cfg.trackers) or 'none'}")
    with tempfile.TemporaryDirectory() as tmp:
        release, path, auto_cover = None, None, None
        if source.startswith("stash:"):
            r = stash_source.resolve(cfg, source[6:], Path(tmp))
            release, path, auto_cover = r.release, r.path, r.cover
        else:
            path = Path(source)
            if not path.is_file():
                raise typer.BadParameter(f"{source} is not a file (use stash:<id> for Stash scenes)")
            if stashbox:
                release, auto_cover = _from_stashbox_url(cfg, stashbox, Path(tmp))
        result = bake(path, out, cfg, release=release, tracker=tracker, cover=cover or auto_cover)
    typer.echo(f"Baked into {result} (+ {result}.zip)")


def _from_stashbox_url(cfg, url: str, tmp: Path):
    from .sources.gql import download

    u = urlparse(url)
    scene_id = u.path.rstrip("/").rsplit("/", 1)[-1]
    endpoint = f"{u.scheme}://{u.netloc}/graphql"
    box = next((b for b in cfg.stashboxes.values() if b.endpoint == endpoint), None)
    if box is None:
        raise typer.BadParameter(f"no key for {endpoint}; add one: tb stash box-add <name> {endpoint} --api-key <key>")
    release = stashbox_source.fetch(endpoint, box.api_key, scene_id)
    cover = download(release.cover_url, tmp / "cover-src.jpg") if release.cover_url else None
    return release, cover


@tracker_app.command("add")
def tracker_add(
    name: str,
    announce: list[str] = typer.Option(..., "--announce", "-a", help="Announce URL (repeatable)"),
    source: str = typer.Option("", "--source", "-s", help="Source tag written into the torrent"),
    private: bool = typer.Option(True, "--private/--public"),
) -> None:
    """Add or replace a tracker profile."""
    cfg = config_mod.load()
    cfg.trackers[name] = Tracker(announce, source, private)
    config_mod.save(cfg)
    typer.echo(f"Saved tracker {name}")


@tracker_app.command("list")
def tracker_list() -> None:
    cfg = config_mod.load()
    for name, t in cfg.trackers.items():
        typer.echo(f"{name}: {', '.join(t.announce)}  source={t.source or '-'}  {'private' if t.private else 'public'}")
    if not cfg.trackers:
        typer.echo("No tracker profiles yet; add one with `tb tracker add`.")


@tracker_app.command("remove")
def tracker_remove(name: str) -> None:
    cfg = config_mod.load()
    if cfg.trackers.pop(name, None) is None:
        raise typer.BadParameter(f"unknown tracker {name!r}")
    config_mod.save(cfg)
    typer.echo(f"Removed tracker {name}")


@stash_app.command("set")
def stash_set(url: str, api_key: str = typer.Option("", "--api-key")) -> None:
    """Set the Stash base URL (e.g. http://stash:9999) and API key."""
    cfg = config_mod.load()
    cfg.stash.url, cfg.stash.api_key = url.rstrip("/"), api_key
    config_mod.save(cfg)
    typer.echo("Stash saved")


@stash_app.command("box-add")
def stash_box_add(name: str, endpoint: str, api_key: str = typer.Option("", "--api-key")) -> None:
    """Save a stash-box endpoint (…/graphql) and key for use with --stashbox."""
    cfg = config_mod.load()
    cfg.stashboxes[name] = StashBoxCfg(endpoint, api_key)
    config_mod.save(cfg)
    typer.echo(f"Saved stash-box {name}")


@stash_app.command("map-add")
def stash_map_add(stash_path: str, local_path: str) -> None:
    """Map a path prefix as Stash sees it to where the share is mounted here."""
    cfg = config_mod.load()
    cfg.path_map.append((stash_path, local_path))
    config_mod.save(cfg)
    typer.echo(f"Mapped {stash_path} -> {local_path}")
