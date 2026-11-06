#!/usr/bin/env bash
# 一键检查：lint + 测试 + 编译。本地与 CI 用同一个入口。
#   ./check.sh                      # 默认用本机 anaconda
#   PYTHON=python ./check.sh        # CI / 其他解释器
set -euo pipefail
cd "$(dirname "$0")"

PY="${PYTHON:-/Applications/anaconda3/bin/python3}"
if [ ! -x "$PY" ]; then
  PY="python3"
fi

echo "== ruff"
"$PY" -m ruff check src tests

echo "== pytest"
"$PY" -m pytest

echo "== compileall"
"$PY" -m compileall -q src

echo "全部通过"
