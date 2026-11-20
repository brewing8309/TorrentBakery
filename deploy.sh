#!/bin/sh
# Developer shortcut: build the archive and install it into a Proxmox container via its node.
#   ./deploy.sh <vmid>      (needs root SSH to <node>-direct.<domain>)
set -e
cd "$(dirname "$0")"
CT=${1:?usage: ./deploy.sh <vmid>}
DOMAIN=${DOMAIN:-ra33.de}
ENTRY=${ENTRY:-pve1-direct.$DOMAIN}
NODE=$(ssh root@$ENTRY "pvesh get /cluster/resources --type vm --output-format json" |
       python3 -c "import json,sys;print(next(r['node'] for r in json.load(sys.stdin) if r['vmid']==$CT))")
H=root@$NODE-direct.$DOMAIN
TAR=$(./scripts/build.sh)
NAME=$(basename "$TAR" .tar.gz)
scp -q "$TAR" $H:/tmp/$NAME.tar.gz
# jobs live in memory: an update restarts the service and would kill a running bake (FORCE=1 to do it anyway)
if [ -z "$FORCE" ] && ssh $H "pct exec $CT -- /opt/torrentbakery/venv/bin/python -c \"import urllib.request,json,os;print(json.load(urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('TB_PORT','8080')+'/busy'))['running'])\"" 2>/dev/null | grep -qv '^0$'; then
  echo "A bake is running in CT $CT: not deploying (FORCE=1 ./deploy.sh $CT to override)." >&2; exit 2
fi
ssh $H "set -e
  pct push $CT /tmp/$NAME.tar.gz /root/$NAME.tar.gz && rm /tmp/$NAME.tar.gz
  pct exec $CT -- sh -c 'cd /root && rm -rf $NAME && tar xzf $NAME.tar.gz && ./$NAME/install.sh; rc=\$?; rm -rf /root/$NAME /root/$NAME.tar.gz; exit \$rc'"
