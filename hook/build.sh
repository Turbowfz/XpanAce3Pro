#!/usr/bin/env bash
# build.sh -- 编译当前仓库的 XPan hook（Windows Git Bash）
set -euo pipefail
export MSYS_NO_PATHCONV=1

B="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
SCRIPT="$B/../tools/build_hook.py"
if command -v cygpath >/dev/null 2>&1; then
    SCRIPT="$(cygpath -m "$SCRIPT")"
fi
exec python "$SCRIPT" "$@"
