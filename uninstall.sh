#!/bin/sh
# Remove TorrentBakery. Settings and baked releases stay unless called with --purge.
set -e
[ "$(id -u)" = 0 ] || { echo "Please run as root." >&2; exit 1; }
systemctl disable --now torrentbakery.service >/dev/null 2>&1 || true
rm -f /etc/systemd/system/torrentbakery.service /etc/default/torrentbakery /usr/local/bin/tb
systemctl daemon-reload
rm -rf /opt/torrentbakery
if [ "$1" = "--purge" ]; then
    rm -rf /var/lib/torrentbakery
    userdel torrentbakery 2>/dev/null || true
    echo "Removed, including settings and baked releases."
else
    echo "Removed. Settings and releases are still in /var/lib/torrentbakery (--purge deletes them too)."
fi
