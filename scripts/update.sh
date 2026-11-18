#!/usr/bin/env bash
# Pull the latest ClippyMe and apply it the fastest safe way.
#
#   scripts/update.sh
#
# The compose file mounts the project into the containers, so a code-only
# change needs just a restart (seconds). A rebuild (minutes; the Python
# dependency layer reinstalls PyTorch & co.) runs only when a file the image
# is built from changed: requirements*, Dockerfile*, the dashboard's
# package*.json, docker-compose*.yml or the entrypoint.
#
# Restarting drops the segment a live monitor is recording, so run this right
# after a segment ends (just after :00 or :30 with 30-minute segments).
set -euo pipefail
cd "$(dirname "$0")/.."

before="$(git rev-parse HEAD)"
git pull --ff-only
after="$(git rev-parse HEAD)"
if [ "$before" = "$after" ]; then
  echo "Already up to date."
  exit 0
fi

changed="$(git diff --name-only "$before" "$after")"
if printf '%s\n' "$changed" | grep -Eq '^(requirements[^/]*|Dockerfile[^/]*|dashboard/Dockerfile|dashboard/package(-lock)?\.json|docker-compose[^/]*\.yml|docker-entrypoint\.sh)$'; then
  echo "Dependencies or the Docker setup changed: rebuilding (this takes a while)..."
  docker compose up -d --build
else
  echo "Code-only update: restarting (a few seconds)..."
  docker compose restart backend frontend
fi
echo "Updated $(git rev-parse --short "$before") -> $(git rev-parse --short "$after")."
