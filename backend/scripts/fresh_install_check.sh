#!/usr/bin/env bash
# Fresh-install integration check.
#
# Creates a throwaway virtual environment, installs the backend exclusively from
# pyproject.toml/uv.lock, then runs the application-level integration check and
# the deterministic test suite inside that environment.
#
#   ./scripts/fresh_install_check.sh
#
# Nothing is written inside the repository: the environment, uv cache and the
# runtime STORAGE_DIR all live under a temporary directory that is removed on exit.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
backend="$(dirname "$here")"
work="$(mktemp -d "${TMPDIR:-/tmp}/planetx-fresh-XXXXXX")"
export UV_PROJECT_ENVIRONMENT="$work/venv"
export UV_CACHE_DIR="$work/uv-cache"
export UV_LINK_MODE=copy
trap 'rm -rf "$work"' EXIT

cd "$backend"

echo "== fresh environment: $UV_PROJECT_ENVIRONMENT"
python3 --version
uv --version

echo "== installing from uv.lock (frozen)"
uv sync --frozen

python="$UV_PROJECT_ENVIRONMENT/bin/python"

echo "== application integration check"
"$python" scripts/fresh_install_check.py

echo "== deterministic test suite in the fresh environment"
"$python" -m pytest -q

echo "== compile + lint in the fresh environment"
"$python" -m compileall -q app scripts
"$python" -m pyflakes app scripts

echo "== frontend untouched"
if git -C "$backend" rev-parse --verify --quiet upstream/main >/dev/null; then
    repo_root="$(git -C "$backend" rev-parse --show-toplevel)"
    frontend=(src public index.html package.json pnpm-lock.yaml vite.config.ts
              tsconfig.json tsconfig.app.json tsconfig.node.json .oxlintrc.json)
    changed="$(git -C "$repo_root" diff --name-only upstream/main -- "${frontend[@]}" || true)"
    untracked="$(git -C "$repo_root" ls-files --others --exclude-standard -- "${frontend[@]}" || true)"
    if [ -n "$changed" ] || [ -n "$untracked" ]; then
        echo "FAIL  frontend files differ from upstream/main:" >&2
        printf '%s\n%s\n' "$changed" "$untracked" >&2
        exit 1
    fi
    echo "PASS  frontend sources and lockfile identical to upstream/main"
else
    echo "SKIP  upstream/main ref not fetched"
fi

echo
echo "Fresh-install check complete."
