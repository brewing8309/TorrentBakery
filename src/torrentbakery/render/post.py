from __future__ import annotations

from pathlib import Path

from jinja2 import ChoiceLoader, Environment, FileSystemLoader, PackageLoader, StrictUndefined

from ..models import MediaInfo, Release
from ..media.probe import fmt_duration, fmt_size


def render_post(release: Release, info: MediaInfo, images: dict[str, str], template: str = "default.bbcode.j2", extra_dir: Path | None = None) -> str:
    """`images` maps role (cover, sheet) to a URL or, without an upload, a placeholder/file name."""
    loaders = [PackageLoader("torrentbakery", "render/templates")]
    if extra_dir:
        loaders.insert(0, FileSystemLoader(extra_dir))
    env = Environment(loader=ChoiceLoader(loaders), undefined=StrictUndefined, trim_blocks=True, lstrip_blocks=True, keep_trailing_newline=True)
    env.filters["duration"] = fmt_duration
    env.filters["filesize"] = fmt_size
    return env.get_template(template).render(release=release, info=info, images=images)
