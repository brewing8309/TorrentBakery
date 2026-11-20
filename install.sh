#!/bin/sh
# Install or update TorrentBakery on a Debian/Ubuntu host or LXC. Run as root from the unpacked release:
#   ./install.sh            (port 8080; another port: PORT=9000 ./install.sh)
# Updating is the same command. Settings and output in /var/lib/torrentbakery are kept.
set -e
APP=/opt/torrentbakery
DATA=/var/lib/torrentbakery
UNIT=/etc/systemd/system/torrentbakery.service
SRC=$(cd "$(dirname "$0")" && pwd)

[ "$(id -u)" = 0 ] || { echo "Please run as root." >&2; exit 1; }
VERSION=$(sed -n 's/^__version__ = "\(.*\)"/\1/p' "$SRC/src/torrentbakery/__init__.py")
echo "TorrentBakery $VERSION"

# system packages: python3 + venv for the Python libraries, ffmpeg/ffprobe for frames and probing, mediainfo for the post
MISSING=""
command -v python3 >/dev/null || MISSING="$MISSING python3"
python3 -c 'import venv, ensurepip' 2>/dev/null || MISSING="$MISSING python3-venv"
command -v ffmpeg >/dev/null && command -v ffprobe >/dev/null || MISSING="$MISSING ffmpeg"
command -v mediainfo >/dev/null || MISSING="$MISSING mediainfo"
if [ -n "$MISSING" ]; then
    command -v apt-get >/dev/null || { echo "Please install:$MISSING" >&2; exit 1; }
    echo "Installing$MISSING …"
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -qq >/dev/null
    apt-get install -y -qq $MISSING >/dev/null
fi
python3 -c 'import sys; sys.exit(sys.version_info < (3, 11))' || { echo "Python 3.11 or newer is required." >&2; exit 1; }

id torrentbakery >/dev/null 2>&1 || useradd --system --home-dir "$DATA" --shell /usr/sbin/nologin torrentbakery
mkdir -p "$DATA/out" "$APP"

# Python libraries and the app live in one venv; pip replaces the package, so no stale file survives an update
[ -x "$APP/venv/bin/pip" ] || python3 -m venv "$APP/venv"
"$APP/venv/bin/pip" install --quiet --upgrade pip
"$APP/venv/bin/pip" install --quiet --upgrade "$SRC[web]" || { echo "pip install failed (needs internet access to pypi.org)." >&2; exit 1; }
rm -rf "$SRC/build" "$SRC"/src/*.egg-info
for f in README.md CHANGELOG.md uninstall.sh; do [ -f "$SRC/$f" ] && cp "$SRC/$f" "$APP/"; done
chown -R root:root "$APP"
chown -R torrentbakery:torrentbakery "$DATA"
chmod 700 "$DATA"

install -m 644 "$SRC/packaging/torrentbakery.service" "$UNIT"
install -m 755 "$SRC/packaging/tb" /usr/local/bin/tb
[ -f /etc/default/torrentbakery ] || echo "TB_PORT=${PORT:-8080}" > /etc/default/torrentbakery
[ -n "$PORT" ] && sed -i '/^TB_PORT=/d' /etc/default/torrentbakery && echo "TB_PORT=$PORT" >> /etc/default/torrentbakery
systemctl daemon-reload
systemctl enable torrentbakery.service >/dev/null 2>&1
systemctl restart torrentbakery.service
sleep 2
if systemctl is-active --quiet torrentbakery.service; then
    PORT_NOW=$(sed -n 's/^TB_PORT=//p' /etc/default/torrentbakery)
    echo "Running: http://$(hostname -I 2>/dev/null | awk '{print $1}'):${PORT_NOW:-8080}/"
    echo "Note: there is no login until you turn it on (Settings → Login & 2FA). Keep it on a trusted network"
    echo "(local only: TB_HOST=127.0.0.1 in /etc/default/torrentbakery, then systemctl restart torrentbakery)."
    echo "Set up trackers, Stash and media folders in the browser under Settings. CLI: tb --help"
else
    echo "The service did not start:" >&2
    journalctl -u torrentbakery.service -n 20 --no-pager -o cat >&2
    exit 1
fi
