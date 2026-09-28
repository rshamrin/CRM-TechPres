#!/bin/bash
set -e
cd "$(dirname "$0")"

PY=python3

if ! command -v $PY >/dev/null 2>&1; then
  echo "Не найден python3. Установи Python 3.11+ и попробуй снова."
  exit 1
fi

echo "Проверяю обновления CRM..."
$PY update.py || echo "Не удалось проверить обновления. Запускаю текущую версию."

if [ ! -d ".venv" ]; then
  echo "Создаю виртуальное окружение..."
  $PY -m venv .venv
fi

source .venv/bin/activate

echo "Устанавливаю зависимости (если нужно)..."
pip install -r requirements.txt

echo "Запускаю приложение..."
uvicorn app.main:app --port 8000
