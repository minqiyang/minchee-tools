#!/usr/bin/env bash
# Run every test_*.py file in the repository from its own folder (tool folders are not Python packages).
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
status=0
found=0
while IFS= read -r test; do
  found=1
  dir="$(dirname "$test")"
  echo "== ${dir#"$root"/}"
  (cd "$dir" && PYTHONDONTWRITEBYTECODE=1 python3 -m unittest "$(basename "$test")") || status=1
done < <(find "$root" -path "$root/.git" -prune -o -name 'test_*.py' -print | sort)
[ "$found" -eq 1 ] || echo "no tests found"
exit "$status"
