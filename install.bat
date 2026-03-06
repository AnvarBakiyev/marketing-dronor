@echo off
chcp 65001 >nul
setlocal EnableDelayedExpansion

cls
echo.
echo   ============================================
echo   Marketing Dronor -- Installation
echo   Windows Edition
echo   ============================================
echo.
echo   This script will install everything needed.
echo   Time: ~15 minutes.
echo.
pause

set STATE=%USERPROFILE%\.marketing_dronor
set PROJECT=%USERPROFILE%\MarketingDronor
if not exist "%STATE%" mkdir "%STATE%"

:: --- 1. Admin rights ---
echo.
echo --- Checking admin rights ---
net session >nul 2>&1
if %errorLevel% neq 0 (
    echo ERROR: Run as Administrator!
    echo Right-click the file and choose "Run as administrator"
    pause
    exit /b 1
)
echo [OK] Admin rights confirmed

:: --- 2. Winget ---
echo.
echo --- Package manager ---
winget --version >nul 2>&1
if %errorLevel% neq 0 (
    echo Installing App Installer (winget)...
    start ms-appinstaller:
    echo Wait for installation to finish, then press any key
    pause
)
echo [OK] winget available

:: --- 3. Python 3.11 ---
echo.
echo --- Python 3.11 ---
python --version >nul 2>&1
if %errorLevel% equ 0 (
    echo [OK] Python found
) else (
    echo Installing Python 3.11...
    winget install Python.Python.3.11 --silent --accept-package-agreements --accept-source-agreements
    if !errorLevel! neq 0 (
        echo ERROR: Could not install Python
        echo Download manually: https://python.org/downloads
        pause
        exit /b 1
    )
    echo [OK] Python installed
    set PATH=%LOCALAPPDATA%\Programs\Python\Python311;%LOCALAPPDATA%\Programs\Python\Python311\Scripts;%PATH%
)

:: --- 4. Git ---
echo.
echo --- Git ---
git --version >nul 2>&1
if %errorLevel% neq 0 (
    echo Installing Git...
    winget install Git.Git --silent --accept-package-agreements --accept-source-agreements
    set PATH=%ProgramFiles%\Git\cmd;%PATH%
    echo [OK] Git installed
) else (
    echo [OK] Git already installed
)

:: --- 5. PostgreSQL 16 ---
echo.
echo --- PostgreSQL 16 ---
if exist "%ProgramFiles%\PostgreSQL\16\bin\psql.exe" (
    echo [OK] PostgreSQL 16 already installed
    set PG_BIN=%ProgramFiles%\PostgreSQL\16\bin
) else (
    echo Downloading PostgreSQL 16 (~180 MB)...
    powershell -Command "Invoke-WebRequest -Uri 'https://get.enterprisedb.com/postgresql/postgresql-16.8-1-windows-x64.exe' -OutFile '%TEMP%\pg16_install.exe'" 2>nul
    if not exist "%TEMP%\pg16_install.exe" (
        echo ERROR: Could not download PostgreSQL
        echo Download manually: https://postgresql.org/download/windows/
        pause
        exit /b 1
    )
    echo Installing PostgreSQL (2-3 minutes)...
    "%TEMP%\pg16_install.exe" --mode unattended --unattendedmodeui minimal --superpassword "postgres" --servicename "postgresql-16" --servicepassword "postgres" --serverport 5432
    set PG_BIN=%ProgramFiles%\PostgreSQL\16\bin
    echo [OK] PostgreSQL installed
)
set PATH=%PG_BIN%;%PATH%

:: Start service
net start "postgresql-16" >nul 2>&1
sc start "postgresql-16" >nul 2>&1
timeout /t 3 /nobreak >nul
echo [OK] PostgreSQL running

:: --- 6. Project code ---
echo.
echo --- Marketing Dronor (code) ---
if exist "%PROJECT%\.git" (
    echo Updating to latest version...
    cd /d "%PROJECT%"
    git pull origin main
    echo [OK] Updated
) else (
    echo Downloading code...
    git clone https://github.com/AnvarBakiyev/marketing-dronor.git "%PROJECT%"
    echo [OK] Downloaded to %PROJECT%
)

:: --- 7. Python dependencies ---
echo.
echo --- Python dependencies ---
if not exist "%STATE%\venv\Scripts\python.exe" (
    echo Creating virtual environment...
    python -m venv "%STATE%\venv"
    echo [OK] Created
) else (
    echo [OK] Virtual environment already exists
)
set PY="%STATE%\venv\Scripts\python.exe"
set PIP="%STATE%\venv\Scripts\pip.exe"

echo Installing packages (~3 minutes)...
%PIP% install --upgrade pip --quiet
%PIP% install --quiet psycopg2-binary flask flask-cors anthropic python-dotenv loguru requests playwright pyotp
echo Installing Chromium browser...
%PY% -m playwright install chromium
echo [OK] All dependencies installed

:: --- 8. Database ---
echo.
echo --- Database ---
set PGPASSWORD=postgres

"%PG_BIN%\psql" -U postgres -d postgres -tc "SELECT 1 FROM pg_database WHERE datname='marketing_dronor'" 2>nul | findstr /C:"1" >nul
if %errorLevel% neq 0 (
    echo Creating database...
    "%PG_BIN%\psql" -U postgres -d postgres -c "CREATE DATABASE marketing_dronor;" >nul
    echo [OK] Database created
) else (
    echo [OK] Database already exists
)

echo Applying schema...
"%PG_BIN%\psql" -U postgres -d marketing_dronor -f "%PROJECT%\infra\db\schema.sql" >nul 2>&1
for %%M in ("%PROJECT%\infra\db\0*.sql") do (
    "%PG_BIN%\psql" -U postgres -d marketing_dronor -f "%%M" >nul 2>&1
)
echo [OK] Schema applied

:: --- 9. API keys (.env) ---
echo.
echo --- API keys setup ---
set ENV_FILE=%PROJECT%\.env
if exist "%ENV_FILE%" (
    echo [OK] .env already configured
) else (
    echo.
    echo Enter your API keys (press Enter to skip, add later to .env file):
    echo.
    set /p TW_KEY="  [1/3] TwitterAPI.io key (ta_...): "
    set /p ANT_KEY="  [2/3] Anthropic API key (sk-ant-...): "
    set /p CC_PASS="  [3/3] Password for Command Center UI: "
    python -c "import secrets; print(secrets.token_hex(32))" > "%STATE%\secret.txt" 2>nul
    set /p SECRET=<"%STATE%\secret.txt"
    (
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
    echo [OK] .env created
)

:: --- 10. Desktop shortcut ---
echo.
echo --- Creating desktop shortcut ---
set LAUNCH_BAT=%USERPROFILE%\Desktop\Start Marketing Dronor.bat
(
    echo @echo off
    echo cd /d "%PROJECT%"
    echo net start postgresql-16 ^>nul 2^>^&1
    echo start "Marketing Dronor" %PY% command_center/cc_backend.py
    echo timeout /t 3 /nobreak ^>nul
    echo start http://localhost:8899
) > "%LAUNCH_BAT%"
echo [OK] Shortcut created on Desktop

:: --- Done ---
echo.
echo   ============================================
echo   Installation complete!
echo   ============================================
echo.
echo   Next steps:
echo.
echo   1. Install GoLogin: https://gologin.com/download
echo   2. Double-click on Desktop:
echo      "Start Marketing Dronor.bat"
echo   3. Browser will open with Command Center
echo.
echo   To change API keys, edit:
echo   %ENV_FILE%
echo.
pause
