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
class CookieSpec:
    """One named cookie the user has to supply: the name is fixed by the site, the user only pastes the value."""
    name: str
    label: str = ""
    level: str = "required"  # required | recommended | optional
    help: str = ""


@dataclass(frozen=True)
class Credential:
    field: str
    label: str
    required: bool = True
    help: str = ""
    cookies: tuple[CookieSpec, ...] = ()  # empty = a single secret value (API token); else the profile stores "name=value; name2=value2"


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
        specs = []
        for k in c.get("cookies", []):
            if not isinstance(k.get("name"), str) or not k["name"].strip() or "=" in k["name"] or ";" in k["name"]:
                raise TemplateError("every cookie needs a plain name (no = or ;)")
            if k.get("level", "required") not in ("required", "recommended", "optional"):
                raise TemplateError("cookie level must be required, recommended or optional")
            specs.append(CookieSpec(k["name"].strip(), k.get("label", ""), k.get("level", "required"), k.get("help", "")))
        creds.append(Credential(c["field"], c.get("label", c["field"]), bool(c.get("required", True)), c.get("help", ""), tuple(specs)))
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


def parse_cookie_string(s: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for part in (s or "").split(";"):
        k, sep, v = part.strip().partition("=")
        if sep and k.strip():
            out[k.strip()] = v.strip()
    return out


def compose_credentials(tpl: TrackerTemplate | None, form, old: dict[str, str]) -> dict[str, str]:
    """The profile's secret fields from the form: one input per named cookie (value only) or one for a plain token. A blank input keeps what is
    saved. `old` maps field -> saved string; the result maps every credential field of the template to its new string."""
    out = dict(old)
    for cred in (tpl.credentials if tpl else ()):
        if not cred.cookies:
            out[cred.field] = (form.get(cred.field) or "").strip() or old.get(cred.field, "")
            continue
        saved = parse_cookie_string(old.get(cred.field, ""))
        pairs = []
        for spec in cred.cookies:
            v = (form.get(f"ck:{cred.field}:{spec.name}") or "").strip()
            if v.startswith(spec.name + "="):  # somebody pasted "name=value"
                v = v[len(spec.name) + 1:]
            v = v.rstrip(";").strip() or saved.get(spec.name, "")
            if v:
                pairs.append(f"{spec.name}={v}")
        pairs += [f"{k}={v}" for k, v in saved.items() if k not in {s.name for s in cred.cookies}]  # cookies saved earlier that the template does not list stay
        out[cred.field] = "; ".join(pairs)
    return out
