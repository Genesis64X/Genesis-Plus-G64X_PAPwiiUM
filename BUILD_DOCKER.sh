#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
PARENT="$(cd "$ROOT/.." && pwd)"
PACKAGE="$(basename "$ROOT")"
IMAGE="${G64X_DOCKER_IMAGE:-devkitpro/devkitppc:20260503}"

if ! command -v docker >/dev/null 2>&1; then
  echo "ERROR: docker not found" >&2
  exit 1
fi

DOCKER=(docker)
if ! docker info >/dev/null 2>&1; then
  DOCKER=(sudo docker)
fi

TTY=()
if [ -t 0 ] && [ -t 1 ]; then
  TTY=(-it)
fi

"${DOCKER[@]}" run --rm "${TTY[@]}" \
  -v "$PARENT:/workspace" \
  -w "/workspace/$PACKAGE" \
  "$IMAGE" \
  bash -lc '
    set -euo pipefail

    git config --global --add safe.directory "*" >/dev/null 2>&1 || true

    export DEVKITPRO="${DEVKITPRO:-/opt/devkitpro}"
    export DEVKITPPC="${DEVKITPPC:-$DEVKITPRO/devkitPPC}"
    export PATH="$DEVKITPPC/bin:$PATH"
    export G64X_WORK=/workspace/G64X_SHARED_WORK

    ./BUILD_NATIVE_LINUX.sh
  '
