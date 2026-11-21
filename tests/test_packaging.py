import subprocess
import tarfile
from pathlib import Path

from torrentbakery import __version__

ROOT = Path(__file__).resolve().parent.parent


def test_build_tarball(tmp_path):
    out = subprocess.run([str(ROOT / "scripts/build.sh")], cwd=ROOT, check=True, capture_output=True, text=True).stdout.strip()
    tar = ROOT / out
    assert tar.name == f"torrentbakery-{__version__}.tar.gz"
    names = set(tarfile.open(tar).getnames())
    top = f"torrentbakery-{__version__}"
    for f in ("install.sh", "uninstall.sh", "pyproject.toml", "CHANGELOG.md", "packaging/torrentbakery.service", "packaging/tb",
              "src/torrentbakery/cli.py", "src/torrentbakery/web/templates/base.html", "src/torrentbakery/web/static/css/tokens.css", "src/torrentbakery/web/static/brand/favicon.svg", "src/torrentbakery/web/static/fonts/atkinson-next-latin.woff2", "src/torrentbakery/render/templates/default.bbcode.j2"):
        assert f"{top}/{f}" in names, f
    assert not any(Path(n).name in ("Dockerfile", ".dockerignore", "docker-entrypoint.sh") or "docker-compose" in n for n in names)  # Docker files are not part of the native tarball
    assert not any("__pycache__" in n or "egg-info" in n or ".venv" in n or "tests/" in n for n in names)


def test_version_command_and_env_defaults(tmp_path):
    from typer.testing import CliRunner

    from torrentbakery.cli import app

    assert CliRunner().invoke(app, ["version"]).stdout.strip() == __version__
    assert any(f"[env var: TB_PORT]" in line for line in CliRunner().invoke(app, ["serve", "--help"], terminal_width=200).stdout.splitlines())


def test_scripts_are_valid_shell():
    for s in ("install.sh", "uninstall.sh", "scripts/build.sh", "deploy.sh", "packaging/tb", "packaging/docker-entrypoint.sh"):
        subprocess.run(["sh", "-n", str(ROOT / s)], check=True)


def test_dockerfile_wiring():
    df = (ROOT / "Dockerfile").read_text()
    assert "packaging/docker-entrypoint.sh" in df and "docker-entrypoint.sh" in df.split("ENTRYPOINT")[1]
    assert "TB_CONFIG=/data/config.toml" in df and "TB_OUT=/data/out" in df
    assert (ROOT / "packaging/docker-entrypoint.sh").stat().st_mode & 0o111
    assert "ffmpeg" in df and "mediainfo" in df
    ign = (ROOT / ".dockerignore").read_text().split()
    assert ".git" in ign and "tests/" in ign
