from __future__ import annotations

from pathlib import Path

import typer

from . import config as config_mod
from .pipeline import bake

app = typer.Typer(help="TorrentBakery – torrent, screenshot sheet and forum post from a video file.", no_args_is_help=True)


@app.callback()
def main() -> None:
    """TorrentBakery."""


@app.command("bake")
def bake_cmd(
    source: Path = typer.Argument(..., exists=True, dir_okay=False, help="Video file"),
    out: Path = typer.Option(Path("out"), "--out", "-o", help="Output root directory"),
    tracker: str = typer.Option(None, "--tracker", "-t", help="Tracker profile from config.toml"),
    cover: Path = typer.Option(None, "--cover", exists=True, dir_okay=False, help="Use this image as main image"),
) -> None:
    cfg = config_mod.load()
    if tracker and tracker not in cfg.trackers:
        raise typer.BadParameter(f"unknown tracker profile {tracker!r}; known: {', '.join(cfg.trackers) or 'none'}")
    result = bake(source, out, cfg, tracker=tracker, cover=cover)
    typer.echo(f"Baked into {result} (+ {result}.zip)")
