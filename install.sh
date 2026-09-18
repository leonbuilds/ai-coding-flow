#!/usr/bin/env bash
# 兼容入口：等同于 python3 workbench/wb.py sync（参数原样透传，例如 --host codex、--dry-run）
set -euo pipefail
exec python3 "$(cd "$(dirname "$0")" && pwd)/workbench/wb.py" sync "$@"
