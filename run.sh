#!/usr/bin/env bash
# 一条命令启动：散装食品称重标签错贴纠正系统
# 用法：./run.sh            启动（http://127.0.0.1:8000/static/index.html）
#       RESET=1 ./run.sh    清空数据库后重新初始化演示数据
set -e
cd "$(dirname "$0")"
PY="${PYTHON:-python3}"

echo "[1/3] 检查 Python 依赖..."
if ! $PY -c "import fastapi, uvicorn, sqlalchemy, pydantic" 2>/dev/null; then
  echo "      安装依赖（用户目录，无需 root）..."
  $PY -m pip install --user --break-system-packages -r backend/requirements.txt
fi

echo "[2/3] 初始化数据库..."
if [ "${RESET:-0}" = "1" ]; then
  rm -f backend/data/labels.db
  rm -rf backend/data/receipts
fi
(cd backend && $PY seed.py)

echo "[3/3] 启动服务..."
echo "      前端页面 : http://127.0.0.1:8000/static/index.html"
echo "      接口文档 : http://127.0.0.1:8000/docs"
echo "      端到端演示: cd backend && $PY demo.py"
cd backend
exec $PY -m uvicorn app.main:app --host 127.0.0.1 --port "${PORT:-8000}"
