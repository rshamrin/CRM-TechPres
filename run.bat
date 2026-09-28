@echo off
setlocal
cd /d "%~dp0"
if errorlevel 1 goto :error

where py >nul 2>&1
if %errorlevel%==0 (
  set "CRM_PY=py"
) else (
  where python >nul 2>&1
  if errorlevel 1 (
    echo Python не найден. Установите Python 3.11 или новее и повторите запуск.
    pause
    exit /b 1
  )
  set "CRM_PY=python"
)

echo Проверяю обновления CRM...
%CRM_PY% update.py
if errorlevel 1 echo Не удалось проверить обновления. Запускаю текущую версию.

if not exist ".venv\Scripts\python.exe" (
  echo Создаю виртуальное окружение...
  %CRM_PY% -m venv .venv
  if errorlevel 1 goto :error
)

call ".venv\Scripts\activate.bat"
if errorlevel 1 goto :error

echo Устанавливаю зависимости (если нужно)...
python -m pip install -r requirements.txt
if errorlevel 1 goto :error

echo Запускаю приложение: http://127.0.0.1:8000
python -m uvicorn app.main:app --port 8000
if errorlevel 1 goto :error
echo.
echo CRM завершила работу.
pause
exit /b 0

:error
echo.
echo Не удалось запустить CRM. Текст ошибки указан выше.
pause
exit /b 1
