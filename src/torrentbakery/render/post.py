from __future__ import annotations

from pathlib import Path

from jinja2 import ChoiceLoader, Environment, FileSystemLoader, PackageLoader, StrictUndefined

from ..models import MediaInfo, Release
from ..media.probe import fmt_codec, fmt_duration, fmt_language, fmt_size


def format_tags(tags: list[str]) -> str:
    """Tags for an upload form's tag field: space separated, so a space inside a tag becomes a dot ("Face Fuck" -> "Face.Fuck")."""
    seen, out = set(), []
    for t in tags:
        t = ".".join(t.split())
        if t and t.lower() not in seen:
            seen.add(t.lower())
            out.append(t)
    return " ".join(out)


def render_post(release: Release, info: MediaInfo, images: dict[str, str], template: str = "default.bbcode.j2", extra_dir: Path | None = None, links: dict[str, str] | None = None) -> str:
    """`images` maps role (cover, sheet) to a URL or, without an upload, a file name; `links` maps role to the host's viewer page the image should link to."""
    loaders = [PackageLoader("torrentbakery", "render/templates")]
    if extra_dir:
        loaders.insert(0, FileSystemLoader(extra_dir))
    env = Environment(loader=ChoiceLoader(loaders), undefined=StrictUndefined, trim_blocks=True, lstrip_blocks=True, keep_trailing_newline=True)
    env.filters["duration"] = fmt_duration
    env.filters["filesize"] = fmt_size
    env.filters["codec"] = fmt_codec
    env.filters["language"] = fmt_language
    return env.get_template(template).render(release=release, info=info, images=images, links=links or {})
