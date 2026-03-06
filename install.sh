#!/bin/bash
# Marketing Dronor — Установщик для macOS
# Запуск: bash install.sh

set -e
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; NC='\033[0m'; BOLD='\033[1m'
header() { echo -e "\n${BOLD}${BLUE}━━━  $1  ━━━${NC}"; }
ok()     { echo -e "  ${GREEN}✓  $1${NC}"; }
info()   { echo -e "  ${YELLOW}→  $1${NC}"; }
fail()   { echo -e "\n${RED}✗  ОШИБКА: $1${NC}\n"; exit 1; }

clear
echo -e "${BOLD}"
echo "  ╔══════════════════════════════════════════════╗"
echo "  ║      Marketing Dronor — Установка            ║"
echo "  ║      macOS Edition                           ║"
echo "  ╚══════════════════════════════════════════════╝"
echo -e "${NC}"
echo "  Этот скрипт установит всё необходимое."
echo "  Время установки: 5-10 минут."
echo ""
read -rp "  Нажми Enter чтобы начать..." _

STATE="$HOME/.marketing_dronor"
PROJECT="$HOME/MarketingDronor"
mkdir -p "$STATE"

# ── 1. Xcode CLI Tools ──────────────────────────────
header "Инструменты разработчика"
if xcode-select -p &>/dev/null; then
  ok "Xcode CLI Tools уже установлены"
else
  info "Устанавливаем Xcode CLI Tools (нужно подтвердить во всплывающем окне)..."
  xcode-select --install 2>/dev/null || true
  echo "  Дождись завершения установки Xcode CLI Tools,"
  read -rp "  затем нажми Enter для продолжения..." _
fi

# ── 2. Homebrew ──────────────────────────────────────
header "Homebrew (менеджер пакетов)"
if command -v brew &>/dev/null; then
  BREW=$(command -v brew); ok "уже установлен: $BREW"
elif [ -f /opt/homebrew/bin/brew ]; then
  BREW=/opt/homebrew/bin/brew; ok "найден"
elif [ -f /usr/local/bin/brew ]; then
  BREW=/usr/local/bin/brew; ok "найден"
else
  info "Устанавливаем Homebrew..."
  NONINTERACTIVE=1 /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)" || fail "Не удалось установить Homebrew"
  [ -f /opt/homebrew/bin/brew ] && BREW=/opt/homebrew/bin/brew || BREW=/usr/local/bin/brew
  ok "установлен"
fi
eval "$("$BREW" shellenv)" 2>/dev/null || true

# ── 3. Python 3.11+ ──────────────────────────────────
header "Python 3.11"
PYTHON=""
for PY in python3.12 python3.11 python3; do
  if command -v $PY &>/dev/null; then
    VER=$($PY -c "import sys; print(sys.version_info[:2])" 2>/dev/null)
    PYTHON=$(command -v $PY); ok "найден: $PYTHON"; break
  fi
done
if [ -z "$PYTHON" ]; then
  info "Устанавливаем Python 3.11..."
  "$BREW" install python@3.11
  PYTHON=$("$BREW" --prefix python@3.11)/bin/python3.11
  ok "установлен"
fi

# Виртуальное окружение
if [ ! -f "$STATE/venv/bin/python" ]; then
  info "Создаём виртуальное окружение..."
  "$PYTHON" -m venv "$STATE/venv"
  ok "создано: $STATE/venv"
else
  ok "виртуальное окружение уже есть"
fi
PY="$STATE/venv/bin/python"
PIP="$STATE/venv/bin/pip"

# ── 4. PostgreSQL ────────────────────────────────────
header "PostgreSQL 16"
if [ -d "$("$BREW" --prefix postgresql@16 2>/dev/null)/bin" ]; then
  PG_BIN=$("$BREW" --prefix postgresql@16)/bin; ok "уже установлен"
elif command -v psql &>/dev/null; then
  PG_BIN=$(dirname $(command -v psql)); ok "найден: $PG_BIN"
else
  info "Устанавливаем PostgreSQL 16..."
  "$BREW" install postgresql@16
  PG_BIN=$("$BREW" --prefix postgresql@16)/bin
  ok "установлен"
fi
export PATH="$PG_BIN:$PATH"
echo "$PG_BIN" > "$STATE/pg_bin"
# Запускаем сервис
if ! "$PG_BIN/pg_isready" -q 2>/dev/null; then
  info "Запускаем PostgreSQL..."
  "$BREW" services start postgresql@16 2>/dev/null || true
  sleep 3
fi
"$PG_BIN/pg_isready" -q && ok "PostgreSQL запущен" || fail "PostgreSQL не запустился"

# ── 5. Git & Код ─────────────────────────────────────
header "Marketing Dronor (код)"
if ! command -v git &>/dev/null; then
  "$BREW" install git
fi
if [ -d "$PROJECT/.git" ]; then
  info "Обновляем до последней версии..."
  cd "$PROJECT" && git pull origin main 2>/dev/null || true
  ok "обновлён"
else
  info "Скачиваем код..."
  git clone https://github.com/AnvarBakiyev/marketing-dronor.git "$PROJECT"
  ok "скачан в $PROJECT"
fi

# ── 6. Python-зависимости ────────────────────────────
header "Python-зависимости"
info "Устанавливаем пакеты (2 минуты)..."
"$PIP" install --upgrade pip --quiet
"$PIP" install --quiet \
  psycopg2-binary \
  flask flask-cors \
  anthropic \
  python-dotenv \
  loguru \
  requests \
  playwright \
  pyotp
info "Устанавливаем браузер Chromium для Playwright..."
"$PY" -m playwright install chromium
ok "все зависимости установлены"

# ── 7. База данных ────────────────────────────────────
header "База данных"
DB_USER=$(whoami)
export PGUSER="$DB_USER"

DB_EXISTS=$("$PG_BIN/psql" -d postgres -tc "SELECT 1 FROM pg_database WHERE datname='marketing_dronor'" 2>/dev/null | tr -d ' ')
if [ "$DB_EXISTS" != "1" ]; then
  info "Создаём базу данных..."
  "$PG_BIN/psql" -d postgres -c "CREATE DATABASE marketing_dronor;" >/dev/null
  ok "база создана"
else
  ok "база уже существует"
fi

info "Применяем схему..."
"$PG_BIN/psql" -d marketing_dronor -f "$PROJECT/infra/db/schema.sql" >/dev/null 2>&1 || true
for MIGRATION in "$PROJECT"/infra/db/0*.sql; do
  "$PG_BIN/psql" -d marketing_dronor -f "$MIGRATION" >/dev/null 2>&1 || true
done
ok "схема применена"

# ── 8. API-ключи (.env) ───────────────────────────────
header "Настройка API-ключей"
ENV_FILE="$PROJECT/.env"
if [ -f "$ENV_FILE" ]; then
  ok ".env уже настроен — пропускаем"
else
  echo ""
  echo -e "  ${BOLD}Введи API-ключи (Enter — пропустить, добавить позже в файл .env):${NC}"
  echo ""
  read -rp "  [1/3] TwitterAPI.io ключ (начинается с ta_...): " TW_KEY
  read -rp "  [2/3] Anthropic API ключ (начинается с sk-ant-...): " ANT_KEY
  read -rsp "  [3/3] Пароль для Command Center UI: " CC_PASS; echo ""
  SECRET=$(python3 -c "import secrets; print(secrets.token_hex(32))" 2>/dev/null || echo "changeme")
  cat > "$ENV_FILE" << ENV
# Marketing Dronor — конфиг
DB_HOST=localhost
DB_PORT=5432
DB_NAME=marketing_dronor
DB_USER=$DB_USER
DB_PASSWORD=
TWITTERAPI_IO_KEY=$TW_KEY
ANTHROPIC_API_KEY=$ANT_KEY
GOLOGIN_API_URL=http://localhost:36912
CC_HOST=127.0.0.1
CC_PORT=8899
CC_SECRET_KEY=$SECRET
CC_ADMIN_PASSWORD=$CC_PASS
LOG_LEVEL=INFO
DRY_RUN=true
ENV
  ok ".env создан: $ENV_FILE"
fi

# ── 9. Ярлык запуска ─────────────────────────────────
header "Создаём ярлык запуска"
LAUNCH_SCRIPT="$HOME/Desktop/Запустить Marketing Dronor.command"
cat > "$LAUNCH_SCRIPT" << LAUNCH
#!/bin/bash
export PATH="$PG_BIN:$PATH"
cd "$PROJECT"
# Запускаем PostgreSQL если не запущен
"$PG_BIN/pg_isready" -q || "$BREW" services start postgresql@16
# Запускаем Command Center
"$PY" command_center/cc_backend.py &
sleep 2
open http://localhost:8899
LAUNCH
chmod +x "$LAUNCH_SCRIPT"
ok "ярлык создан на рабочем столе"

# ── Готово ───────────────────────────────────────────
echo ""
echo -e "${BOLD}${GREEN}"
echo "  ╔══════════════════════════════════════════════╗"
echo "  ║      ✓  Установка завершена!                 ║"
echo "  ╚══════════════════════════════════════════════╝"
echo -e "${NC}"
echo "  Что дальше:"
echo ""
echo "  1. Установи GoLogin: https://gologin.com/download"
echo "  2. Два раза кликни на рабочем столе:"
echo "     'Запустить Marketing Dronor.command'"
echo "  3. Откроется браузер с Command Center"
echo ""
echo "  Для изменения API-ключей: открой файл"
echo "  $ENV_FILE"
echo ""

