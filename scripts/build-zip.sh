#!/bin/sh
# Builds lineup_manager.zip for Dispatcharr → Plugins → Import.
set -e
cd "$(dirname "$0")/.."
rm -f lineup_manager.zip
find lineup_manager -name __pycache__ -prune -o -type f -print | zip -q lineup_manager.zip -@
echo "built lineup_manager.zip"
