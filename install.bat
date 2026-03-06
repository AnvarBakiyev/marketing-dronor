@echo off
chcp 65001 >nul
setlocal EnableDelayedExpansion

cls
echo.
echo   ╔══════════════════════════════════════════════╗
echo   ║      Marketing Dronor — Установка            ║
echo   ║      Windows Edition                         ║
echo   ╚══════════════════════════════════════════════╝
echo.
echo   Этот скрипт установит всё необходимое.
echo   Время установки: /Users/anvarbakiyev10-15 минут.
echo.
pause

set STATE=%USERPROFILE%\.marketing_dronor
set PROJECT=%USERPROFILE%\MarketingDronor
if not exist "%STATE%" mkdir "%STATE%"

:: ── 1. Проверяем права администратора ─────────────
echo.
echo ━━━  Проверка прав  ━━━
net session >nul 2>&1
if %errorLevel% neq 0 (
    echo   ОШИБКА: Запусти установщик от имени Администратора!
    echo   Правая кнопка мыши на файле → "Запуск от имени администратора"
    pause
    exit /b 1
)
echo   ✓  Права администратора есть

:: ── 2. Winget (встроен в Windows 10/11) ──────────
echo.
echo ━━━  Менеджер пакетов  ━━━
winget --version >nul 2>&1
if %errorLevel% neq 0 (
    echo   → Устанавливаем App Installer (winget)...
    start ms-appinstaller:
    echo   Дождись установки, затем нажми любую клавишу
    pause
)
echo   ✓  winget доступен

:: ── 3. Python 3.11 ────────────────────────────────
echo.
echo ━━━  Python 3.11  ━━━
python --version >nul 2>&1
if %errorLevel% equ 0 (
    for /f "tokens=2" %%v in ('python --version 2^>^&1') do set PYVER=%%v
    echo   ✓  Python !PYVER! найден
) else (
    echo   → Устанавливаем Python 3.11...
    winget install Python.Python.3.11 --silent --accept-package-agreements --accept-source-agreements
    if !errorLevel! neq 0 (
        echo   ОШИБКА: Не удалось установить Python
        echo   Скачай вручную: https://python.org/downloads
        pause
        exit /b 1
    )
    echo   ✓  Python установлен
    :: Обновляем PATH
    set PATH=%LOCALAPPDATA%\Programs\Python\Python311;%LOCALAPPDATA%\Programs\Python\Python311\Scripts;%PATH%
)

:: ── 4. Git ─────────────────────────────────────────
echo.
echo ━━━  Git  ━━━
git --version >nul 2>&1
if %errorLevel% neq 0 (
    echo   → Устанавливаем Git...
    winget install Git.Git --silent --accept-package-agreements --accept-source-agreements
    set PATH=%ProgramFiles%\Git\cmd;%PATH%
    echo   ✓  Git установлен
) else (
    echo   ✓  Git уже есть
)

:: ── 5. PostgreSQL 16 ───────────────────────────────
echo.
echo ━━━  PostgreSQL 16  ━━━
if exist "%ProgramFiles%\PostgreSQL\16\bin\psql.exe" (
    echo   ✓  PostgreSQL 16 уже установлен
    set PG_BIN=%ProgramFiles%\PostgreSQL\16\bin
) else (
    echo   → Скачиваем PostgreSQL 16...
    set PG_INSTALLER=%TEMP%\pg16_install.exe
    powershell -Command "Invoke-WebRequest -Uri 'https://get.enterprisedb.com/postgresql/postgresql-16.8-1-windows-x64.exe' -OutFile '%TEMP%\pg16_install.exe'" 2>nul
    if not exist "%TEMP%\pg16_install.exe" (
        echo   ОШИБКА: Не удалось скачать PostgreSQL
        echo   Скачай вручную: https://postgresql.org/download/windows/
        pause
        exit /b 1
    )
    echo   → Устанавливаем PostgreSQL (займёт 2-3 минуты)...
    "%TEMP%\pg16_install.exe" --mode unattended --unattendedmodeui minimal --superpassword "postgres" --servicename "postgresql-16" --servicepassword "postgres" --serverport 5432
    set PG_BIN=%ProgramFiles%\PostgreSQL\16\bin
    echo   ✓  PostgreSQL установлен
)
set PATH=%PG_BIN%;%PATH%

:: Запускаем сервис
nет start "postgresql-16" >nul 2>&1
sc start "postgresql-16" >nul 2>&1
timeout /t 3 /nobreak >nul
echo   ✓  PostgreSQL запущен

:: ── 6. Код проекта ─────────────────────────────────
echo.
echo ━━━  Marketing Dronor (код)  ━━━
if exist "%PROJECT%\.git" (
    echo   → Обновляем до последней версии...
    cd /d "%PROJECT%"
    git pull origin main
    echo   ✓  Обновлён
) else (
    echo   → Скачиваем код...
    git clone https://github.com/AnvarBakiyev/marketing-dronor.git "%PROJECT%"
    echo   ✓  Скачан в %PROJECT%
)

:: ── 7. Python-зависимости ──────────────────────────
echo.
echo ━━━  Python-зависимости  ━━━
:: Виртуальное окружение
if not exist "%STATE%\venv\Scripts\python.exe" (
    echo   → Создаём виртуальное окружение...
    python -m venv "%STATE%\venv"
    echo   ✓  Создано
) else (
    echo   ✓  Виртуальное окружение уже есть
)
set PY="%STATE%\venv\Scripts\python.exe"
set PIP="%STATE%\venv\Scripts\pip.exe"

echo   → Устанавливаем пакеты (/Users/anvarbakiyev3 минуты)...
%PIP% install --upgrade pip --quiet
%PIP% install --quiet psycopg2-binary flask flask-cors anthropic python-dotenv loguru requests playwright pyotp
echo   → Устанавливаем браузер Chromium...
%PY% -m playwright install chromium
echo   ✓  Все зависимости установлены

:: ── 8. База данных ─────────────────────────────────
echo.
echo ━━━  База данных  ━━━
set PGPASSWORD=postgres

"%PG_BIN%\psql" -U postgres -d postgres -tc "SELECT 1 FROM pg_database WHERE datname='marketing_dronor'" 2>nul | findstr /C:"1" >nul
if %errorLevel% neq 0 (
    echo   → Создаём базу данных...
    "%PG_BIN%\psql" -U postgres -d postgres -c "CREATE DATABASE marketing_dronor;" >nul
    echo   ✓  База создана
) else (
    echo   ✓  База уже существует
)

echo   → Применяем схему...
"%PG_BIN%\psql" -U postgres -d marketing_dronor -f "%PROJECT%\infra\db\schema.sql" >nul 2>&1
for %%M in ("%PROJECT%\infra\db\0*.sql") do (
    "%PG_BIN%\psql" -U postgres -d marketing_dronor -f "%%M" >nul 2>&1
)
echo   ✓  Схема применена

:: ── 9. API-ключи (.env) ────────────────────────────
echo.
echo ━━━  Настройка API-ключей  ━━━
set ENV_FILE=%PROJECT%\.env
if exist "%ENV_FILE%" (
    echo   ✓  .env уже настроен — пропускаем
) else (
    echo.
    echo   Введи API-ключи (Enter — пропустить, добавить позже в файл .env):
    echo.
    set /p TW_KEY="  [1/3] TwitterAPI.io ключ (ta_...): "
    set /p ANT_KEY="  [2/3] Anthropic API ключ (sk-ant-...): "
    set /p CC_PASS="  [3/3] Пароль для Command Center UI: "
    python -c "import secrets; print(secrets.token_hex(32))" > "%STATE%\secret.txt" 2>nul
    set /p SECRET=<"%STATE%\secret.txt"
    (
        echo # Marketing Dronor — конфиг
        echo DB_HOST=localhost
        echo DB_PORT=5432
        echo DB_NAME=marketing_dronor
        echo DB_USER=postgres
        echo DB_PASSWORD=postgres
        echo TWITTERAPI_IO_KEY=!TW_KEY!
        echo ANTHROPIC_API_KEY=!ANT_KEY!
        echo GOLOGIN_API_URL=http://localhost:36912
        echo CC_HOST=127.0.0.1
        echo CC_PORT=8899
        echo CC_SECRET_KEY=!SECRET!
        echo CC_ADMIN_PASSWORD=!CC_PASS!
        echo LOG_LEVEL=INFO
        echo DRY_RUN=true
    ) > "%ENV_FILE%"
    echo   ✓  .env создан
)

:: ── 10. Ярлык запуска ──────────────────────────────
echo.
echo ━━━  Создаём ярлык запуска  ━━━
set LAUNCH_BAT=%USERPROFILE%\Desktop\Запустить Marketing Dronor.bat
(
    echo @echo off
    echo cd /d "%PROJECT%"
    echo net start postgresql-16 ^>nul 2^>^&1
    echo start "Marketing Dronor" %PY% command_center/cc_backend.py
    echo timeout /t 3 /nobreak ^>nul
    echo start http://localhost:8899
) > "%LAUNCH_BAT%"
echo   ✓  Ярлык создан на рабочем столе

:: ── Готово ──────────────────────────────────────────
echo.
echo   ╔══════════════════════════════════════════════╗
echo   ║      ✓  Установка завершена!                 ║
echo   ╚══════════════════════════════════════════════╝
echo.
echo   Что дальше:
echo.
echo   1. Установи GoLogin: https://gologin.com/download
echo   2. Два раза кликни на рабочем столе:
echo      'Запустить Marketing Dronor.bat'
echo   3. Откроется браузер с Command Center
echo.
echo   Для изменения API-ключей: открой файл
echo   %ENV_FILE%
echo.
pause
