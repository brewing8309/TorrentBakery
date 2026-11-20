#!/bin/sh
# Build the release archive: dist/torrentbakery-<version>.tar.gz
set -e
cd "$(dirname "$0")/.."
V=$(sed -n 's/^__version__ = "\(.*\)"/\1/p' src/torrentbakery/__init__.py)
NAME=torrentbakery-$V
rm -rf dist/$NAME
mkdir -p dist/$NAME
cp -r src packaging pyproject.toml install.sh uninstall.sh README.md CHANGELOG.md SECURITY-AUDIT.md LICENSE docs dist/$NAME/
rm -rf dist/$NAME/docs/assets   # screenshots are for GitHub only
find dist/$NAME \( -name __pycache__ -o -name '*.egg-info' \) -type d -exec rm -rf {} +
tar -C dist -czf dist/$NAME.tar.gz --owner=0 --group=0 $NAME
rm -rf dist/$NAME
echo dist/$NAME.tar.gz
