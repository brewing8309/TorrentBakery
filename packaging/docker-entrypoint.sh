#!/bin/sh
# Container entrypoint: as root, fix up /data and drop to the torrentbakery user (PUID/PGID);
# started with --user, only check that /data is usable.
set -e
DATA=$(dirname "$TB_CONFIG")
umask 077
if [ "$(id -u)" = 0 ]; then
    PUID=${PUID:-$(id -u torrentbakery)}
    PGID=${PGID:-$(id -g torrentbakery)}
    [ "$PGID" = "$(id -g torrentbakery)" ] || groupmod -o -g "$PGID" torrentbakery
    [ "$PUID" = "$(id -u torrentbakery)" ] || usermod -o -u "$PUID" torrentbakery
    mkdir -p "$DATA" "$TB_OUT"
    # a changed PUID/PGID (or a fresh bind mount) needs the whole tree handed over; otherwise only the top level
    if [ "$(stat -c %u "$DATA")" = "$PUID" ]; then chown "$PUID:$PGID" "$DATA" "$TB_OUT"; else chown -R "$PUID:$PGID" "$DATA"; fi
    chmod 700 "$DATA"
    exec setpriv --reuid "$PUID" --regid "$PGID" --init-groups "$@"
fi
mkdir -p "$DATA" "$TB_OUT" 2>/dev/null && [ -w "$DATA" ] || {
    echo "docker-entrypoint: $DATA is not writable for uid $(id -u); fix the volume's owner or drop --user" >&2
    exit 1
}
exec "$@"
