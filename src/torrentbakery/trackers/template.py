"""Tracker templates: one small TOML file per tracker site (see docs/tracker-templates.md). Built-in ones ship in templates/,
the community's go into <config dir>/tracker-templates/ and win over built-in ones with the same id. A template holds only data
for one of the known engines (gazelle, unit3d), never code, so a downloaded file cannot run anything."""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

ENGINES = ("gazelle", "unit3d")
CREDENTIAL_FIELDS = ("cookie", "session_cookie")  # where the Tracker profile keeps secrets
BUILTIN_DIR = Path(__file__).parent / "templates"


def user_dir() -> Path:
    return Path(os.environ.get("TB_CONFIG", Path.home() / ".config/torrentbakery/config.toml")).parent / "tracker-templates"


class TemplateError(ValueError):
    pass


@dataclass(frozen=True)
class Credential:
    field: str
    label: str
    required: bool = True
    help: str = ""


@dataclass(frozen=True)
class TrackerTemplate:
    id: str
    name: str
    engine: str
    description: str = ""
    base_url: str = ""
    source: str = ""
    private: bool = True
    credentials: tuple[Credential, ...] = ()
    image_hosts: tuple[str, ...] = ()  # empty = every host works
    gazelle: dict = field(default_factory=dict)
    unit3d: dict = field(default_factory=dict)
    origin: str = "built-in"  # built-in | community

    @property
    def category_table(self) -> dict[int, str]:
        return self.unit3d.get("categories", {})


def parse(data: dict, origin: str = "built-in") -> TrackerTemplate:
    def need(key: str) -> str:
        v = data.get(key)
        if not isinstance(v, str) or not v.strip():
            raise TemplateError(f"missing {key!r}")
        return v.strip()

    if data.get("schema") != 1:
        raise TemplateError("unsupported schema (this version reads schema = 1)")
    tid, engine = need("id"), need("engine")
    if not tid.replace("-", "").replace("_", "").isalnum() or tid != tid.lower():
        raise TemplateError("id must be lowercase letters, digits, - or _")
    if engine not in ENGINES:
        raise TemplateError(f"engine must be one of: {', '.join(ENGINES)}")
    creds = []
    for c in data.get("auth", {}).get("credentials", []):
        if c.get("field") not in CREDENTIAL_FIELDS:
            raise TemplateError(f"credential field must be one of: {', '.join(CREDENTIAL_FIELDS)}")
        creds.append(Credential(c["field"], c.get("label", c["field"]), bool(c.get("required", True)), c.get("help", "")))
    if not any(c.field == "cookie" for c in creds):
        raise TemplateError("auth.credentials needs an entry with field = \"cookie\" (the main secret)")
    u = dict(data.get("unit3d", {}))
    if engine == "unit3d":
        for key in ("categories", "resolutions"):
            u[key] = {int(k): str(v) for k, v in u.get(key, {}).items()}
        if not u["categories"] or not u["resolutions"] or "type_id" not in u:
            raise TemplateError("a unit3d template needs [unit3d] type_id, categories and resolutions")
    base = data.get("base_url", "")
    if base and not base.startswith(("http://", "https://")):
        raise TemplateError("base_url must start with http:// or https://")
    return TrackerTemplate(tid, need("name"), engine, data.get("description", ""), base.rstrip("/"), data.get("source", ""), bool(data.get("private", True)), tuple(creds),
                           tuple(data.get("images", {}).get("hosts", [])), dict(data.get("gazelle", {})), u, origin)


def load_templates(extra_dir: Path | None = None) -> tuple[dict[str, TrackerTemplate], dict[str, str]]:
    """(templates by id, file name -> error for the files that could not be read)."""
    found: dict[str, TrackerTemplate] = {}
    errors: dict[str, str] = {}
    for folder, origin in ((BUILTIN_DIR, "built-in"), (extra_dir if extra_dir is not None else user_dir(), "community")):
        for f in sorted(folder.glob("*.toml")) if folder.is_dir() else []:
            try:
                t = parse(tomllib.loads(f.read_text()), origin)
            except (TemplateError, tomllib.TOMLDecodeError, ValueError, OSError) as e:
                errors[f.name] = str(e)
                continue
            found[t.id] = t
    return found, errors


def get_template(site: str) -> TrackerTemplate | None:
    return load_templates()[0].get(site)


def template_ids() -> list[str]:
    return list(load_templates()[0])
