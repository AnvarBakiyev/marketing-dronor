#!/bin/bash
set -e

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; NC='\033[0m'; BOLD='\033[1m'
header() { echo -e "\n${BOLD}${BLUE}▶ $1${NC}"; }
ok()     { echo -e "  ${GREEN}✓ $1${NC}"; }
info()   { echo -e "  ${YELLOW}→ $1${NC}"; }
fail()   { echo -e "\n${RED}✗ Ошибка: $1${NC}\n"; exit 1; }

echo -e "${BOLD}"
echo "╔══════════════════════════════════════════╗"
echo "║     Marketing Dronor — Установка         ║"
echo "╚══════════════════════════════════════════╝"
echo -e "${NC}"

STATE="$HOME/.marketing_dronor"
PROJECT="$HOME/MarketingDronor"
LOGS="$HOME/Library/Logs/MarketingDronor"
mkdir -p "$STATE" "$LOGS"

# 1. Homebrew
header "Homebrew"
if [ -f /opt/homebrew/bin/brew ]; then
    BREW=/opt/homebrew/bin/brew; ok "уже установлен"
elif [ -f /usr/local/bin/brew ]; then
    BREW=/usr/local/bin/brew; ok "уже установлен"
else
    info "устанавливаем..."
    NONINTERACTIVE=1 /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
    [ -f /opt/homebrew/bin/brew ] && BREW=/opt/homebrew/bin/brew || BREW=/usr/local/bin/brew
    ok "установлен"
fi
eval "$($BREW shellenv)" 2>/dev/null || true

# 2. Python
header "Python"
PYTHON=""
for PY in python3.12 python3.11 python3; do
    if command -v $PY &>/dev/null; then
        PYTHON=$(command -v $PY); ok "найден: $PYTHON ($($PYTHON --version))"; break
    fi
done
[ -z "$PYTHON" ] && { info "устанавливаем python@3.12..."; $BREW install python@3.12; PYTHON=$($BREW --prefix python@3.12)/bin/python3.12; ok "установлен"; }

if [ ! -f "$STATE/venv/bin/python" ]; then
    info "создаём venv..."; $PYTHON -m venv "$STATE/venv"; ok "venv создан"
else
    ok "venv уже есть"
fi
PYTHON="$STATE/venv/bin/python"
echo "$PYTHON" > "$STATE/python_path"

# 3. PostgreSQL
header "PostgreSQL"
if command -v psql &>/dev/null; then
    ok "уже установлен"; PG_BIN=$(dirname $(command -v psql))
elif [ -d "$($BREW --prefix postgresql@16 2>/dev/null)/bin" ]; then
    PG_BIN=$($BREW --prefix postgresql@16)/bin; ok "найден"
else
    info "устанавливаем..."; $BREW install postgresql@16; PG_BIN=$($BREW --prefix postgresql@16)/bin; ok "установлен"
fi
echo "$PG_BIN" > "$STATE/pg_bin"
export PATH="$PG_BIN:$PATH"
pg_isready -q 2>/dev/null || { info "запускаем PostgreSQL..."; $BREW services start postgresql@16 2>/dev/null || true; sleep 2; }
ok "PostgreSQL запущен"

# 4. Код
header "Marketing Dronor (код)"
if [ -d "$PROJECT/.git" ]; then
    info "обновляем..."; cd "$PROJECT" && git pull origin main 2>/dev/null || true; ok "обновлён"
else
    info "клонируем..."; git clone https://github.com/AnvarBakiyev/marketing-dronor.git "$PROJECT"; ok "склонирован"
fi
echo "$PROJECT" > "$STATE/project_dir"

# 5. Зависимости
header "Python-зависимости"
info "устанавливаем пакеты..."
$PYTHON -m pip install --upgrade pip --quiet
$PYTHON -m pip install --quiet psycopg2-binary flask flask-cors anthropic python-dotenv loguru requests aiohttp playwright
info "устанавливаем браузер Playwright..."
$PYTHON -m playwright install chromium
ok "все зависимости установлены"

# 6. База данных
header "База данных"
DB_USER=$(whoami)
echo "$DB_USER" > "$STATE/db_user"
DB_EXISTS=$(psql -U "$DB_USER" -d postgres -tc "SELECT 1 FROM pg_database WHERE datname='marketing_dronor'" 2>/dev/null | tr -d ' ')
[ "$DB_EXISTS" != "1" ] && { info "создаём базу..."; psql -U "$DB_USER" -d postgres -c "CREATE DATABASE marketing_dronor;" >/dev/null; ok "база создана"; } || ok "база уже есть"
info "применяем схемы..."
psql -U "$DB_USER" -d marketing_dronor -f "$PROJECT/infra/db/schema_v001.sql" >/dev/null 2>&1 || true
psql -U "$DB_USER" -d marketing_dronor -f "$PROJECT/infra/db/schema_v002.sql" >/dev/null 2>&1 || true
ok "схемы применены"

# 7. API-ключи
header "API-ключи"
ENV_FILE="$PROJECT/.env"
if [ -f "$ENV_FILE" ]; then
    ok ".env уже есть — пропускаем"
else
    echo ""
    echo -e "${BOLD}Введи API-ключи (или Enter чтобы добавить позже):${NC}"
    read -rp "  TwitterAPI.io key (ta_...): " TW_KEY
    read -rp "  Anthropic API key (sk-ant-...): " ANT_KEY
    SECRET=$(python3 -c "import secrets; print(secrets.token_hex(32))")
    cat > "$ENV_FILE" << ENV
DB_HOST=localhost
DB_PORT=5432
DB_NAME=marketing_dronor
DB_USER=$DB_USER
DB_PASSWORD=
TWITTERAPI_IO_KEY=$TW_KEY
ANTHROPIC_API_KEY=$ANT_KEY
ADSPOWER_API_URL=http://localhost:50325
CC_HOST=127.0.0.1
CC_PORT=5555
CC_SECRET_KEY=$SECRET
LOG_LEVEL=INFO
DRY_RUN=true
ENV
    ok ".env создан"
fi

touch "$STATE/installed"
echo ""
echo -e "${BOLD}${GREEN}╔══════════════════════════════════════════╗"
echo "║     ✓ Установка завершена!            ║"
echo "╚══════════════════════════════════════════╝${NC}"
echo ""
echo -e "  Дальше: открой ${BOLD}Marketing Dronor.app${NC} на рабочем столе"
echo ""
[ -d "$HOME/Desktop/Marketing Dronor.app" ] && open "$HOME/Desktop/Marketing Dronor.app"
