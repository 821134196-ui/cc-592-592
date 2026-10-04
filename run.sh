#!/usr/bin/env bash
# 一条命令启动：称重标签错贴纠正系统（FastAPI + SQLite + Vue 本地页面）
set -euo pipefail
cd "$(dirname "$0")"

PY="${PYTHON:-python3}"
PORT="${PORT:-8000}"

# ---------- 演示模式：不启动服务，直接跑三大场景脚本 ----------
if [ "${1:-}" = "demo" ]; then
  exec .venv/bin/python scripts/demo.py
fi

# ---------- 重置演示数据 ----------
if [ "${1:-}" = "reset" ]; then
  rm -f data/scale.db
  echo "已删除 data/scale.db"
fi

# ---------- 虚拟环境（兼容缺少 ensurepip 的系统） ----------
if [ ! -x .venv/bin/python ]; then
  echo "▶ 创建虚拟环境 .venv ..."
  $PY -m venv .venv 2>/dev/null || $PY -m venv --without-pip .venv
  if [ ! -x .venv/bin/pip ]; then
    echo "▶ 系统 venv 缺 pip，使用 get-pip.py 引导 ..."
    curl -sSL https://bootstrap.pypa.io/get-pip.py -o /tmp/get-pip-scale.py
    .venv/bin/python /tmp/get-pip-scale.py -q
  fi
fi

echo "▶ 检查 Python 依赖 ..."
.venv/bin/python -m pip install -q --disable-pip-version-check -r requirements.txt

echo "▶ 初始化数据库与演示种子（已存在数据则跳过）..."
.venv/bin/python -m backend.seed

echo "▶ 启动服务： http://127.0.0.1:${PORT}"
echo "  账号（页面右上角切换，无需密码）："
echo "    cashier01 收银员小王  /  mgr01 主管李姐"
echo "  命令行演示三大场景： ./run.sh demo"
exec .venv/bin/python -m uvicorn backend.app.main:app --host 127.0.0.1 --port "${PORT}"
