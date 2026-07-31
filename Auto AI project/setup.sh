#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════
# THE INSTITUTION + THE FOUNDRY — ONE-TIME SETUP
# ═══════════════════════════════════════════════════════════════
# Run as: curl -sL https://your-repo/setup.sh | bash
#    or:  chmod +x setup.sh && ./setup.sh
#
# Idempotent. Safe to re-run. Handles ARM (Oracle Cloud) and x86.
# After this completes, the system is LIVE and AUTONOMOUS.
# ═══════════════════════════════════════════════════════════════

set -euo pipefail

# ─── CONFIGURATION ────────────────────────────────────────────
INSTITUTION_ROOT="/opt/institution"
PYTHON_VERSION="3.11"
HUGO_VERSION="0.139.0"
NODE_VERSION="20"
OLLAMA_ENABLED="${OLLAMA_ENABLED:-true}"
DASHBOARD_PORT="${DASHBOARD_PORT:-8080}"
GIT_REPO="${GIT_REPO:-}"  # Optional: git remote for institutional memory

# ─── COLORS ───────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

# ─── LOGGING ──────────────────────────────────────────────────
LOG_FILE="/tmp/institution_setup_$(date +%Y%m%d_%H%M%S).log"
exec > >(tee -a "$LOG_FILE") 2>&1

log_info()  { echo -e "${BLUE}[INFO]${NC}  $(date '+%H:%M:%S') $*"; }
log_ok()    { echo -e "${GREEN}[OK]${NC}    $(date '+%H:%M:%S') $*"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC}  $(date '+%H:%M:%S') $*"; }
log_error() { echo -e "${RED}[ERROR]${NC} $(date '+%H:%M:%S') $*"; }
log_step()  { echo -e "\n${BLUE}══════════════════════════════════════════════════${NC}"; echo -e "${BLUE}  $*${NC}"; echo -e "${BLUE}══════════════════════════════════════════════════${NC}"; }

# ─── ERROR HANDLING ───────────────────────────────────────────
error_exit() {
    log_error "$1"
    log_error "Setup failed. Log saved to: $LOG_FILE"
    log_error "Fix the issue and re-run this script. It is idempotent."
    exit 1
}

trap 'error_exit "Unexpected error on line $LINENO"' ERR

# ─── PRIVILEGE CHECK ──────────────────────────────────────────
if [[ $EUID -ne 0 ]]; then
    log_warn "Not running as root. Attempting sudo for privileged operations."
    SUDO="sudo"
else
    SUDO=""
fi

# ─── ARCHITECTURE DETECTION ───────────────────────────────────
ARCH=$(uname -m)
case "$ARCH" in
    x86_64)  ARCH_TAG="amd64"; HUGO_ARCH="amd64" ;;
    aarch64) ARCH_TAG="arm64"; HUGO_ARCH="arm64" ;;
    arm*)    ARCH_TAG="arm64"; HUGO_ARCH="arm64" ;;
    *)       error_exit "Unsupported architecture: $ARCH" ;;
esac
log_info "Detected architecture: $ARCH ($ARCH_TAG)"

# ─── OS DETECTION ─────────────────────────────────────────────
if [[ -f /etc/os-release ]]; then
    . /etc/os-release
    OS_ID="$ID"
    OS_VERSION="$VERSION_ID"
else
    error_exit "Cannot detect OS. /etc/os-release not found."
fi
log_info "Detected OS: $OS_ID $OS_VERSION"

# ═══════════════════════════════════════════════════════════════
log_step "PHASE 1: SYSTEM DEPENDENCIES"
# ═══════════════════════════════════════════════════════════════

log_info "Updating package lists..."
$SUDO apt-get update -qq || error_exit "apt-get update failed"

log_info "Installing core system packages..."
$SUDO apt-get install -y -qq \
    python3 python3-pip python3-venv python3-dev \
    git curl wget jq sqlite3 \
    ffmpeg imagemagick fonts-dejavu-core \
    build-essential libffi-dev libssl-dev \
    nginx certbot python3-certbot-nginx \
    htop iotop ncdu \
    cron rsyslog \
    unzip tar gzip \
    ca-certificates gnupg lsb-release \
    2>/dev/null || error_exit "Failed to install system packages"

log_ok "System packages installed"

# ─── NODE.JS (for Hugo extended, npm tools) ───────────────────
if ! command -v node &>/dev/null || [[ "$(node --version 2>/dev/null | cut -d. -f1 | tr -d v)" -lt "$NODE_VERSION" ]]; then
    log_info "Installing Node.js ${NODE_VERSION}.x..."
    curl -fsSL "https://deb.nodesource.com/setup_${NODE_VERSION}.x" | $SUDO -E bash - >/dev/null 2>&1
    $SUDO apt-get install -y -qq nodejs >/dev/null 2>&1
    log_ok "Node.js $(node --version) installed"
else
    log_ok "Node.js $(node --version) already present"
fi

# ─── HUGO (static site generator) ─────────────────────────────
HUGO_URL="https://github.com/gohugoio/hugo/releases/download/v${HUGO_VERSION}/hugo_extended_${HUGO_VERSION}_linux-${HUGO_ARCH}.tar.gz"
if ! command -v hugo &>/dev/null || [[ "$(hugo version 2>/dev/null | grep -oP 'v\K[0-9.]+' | head -1)" != "$HUGO_VERSION" ]]; then
    log_info "Installing Hugo ${HUGO_VERSION} (extended)..."
    cd /tmp
    wget -q "$HUGO_URL" -O hugo.tar.gz || error_exit "Failed to download Hugo"
    tar -xzf hugo.tar.gz hugo
    $SUDO mv hugo /usr/local/bin/hugo
    $SUDO chmod +x /usr/local/bin/hugo
    rm -f hugo.tar.gz
    log_ok "Hugo $(hugo version | grep -oP 'v[0-9.]+' | head -1) installed"
else
    log_ok "Hugo already present: $(hugo version | grep -oP 'v[0-9.]+' | head -1)"
fi

# ─── OLLAMA (local LLM fallback) ──────────────────────────────
if [[ "$OLLAMA_ENABLED" == "true" ]]; then
    if ! command -v ollama &>/dev/null; then
        log_info "Installing Ollama..."
        curl -fsSL https://ollama.com/install.sh | sh >/dev/null 2>&1 || {
            log_warn "Ollama install failed. Local fallback unavailable. Continuing."
            OLLAMA_ENABLED="false"
        }
        if [[ "$OLLAMA_ENABLED" == "true" ]]; then
            log_ok "Ollama installed"
            log_info "Pulling llama3.1:8b (this may take a few minutes)..."
            ollama pull llama3.1:8b >/dev/null 2>&1 || log_warn "Failed to pull llama3.1:8b. Will retry later."
            log_ok "Local model available"
        fi
    else
        log_ok "Ollama already installed"
    fi
else
    log_info "Ollama disabled by configuration"
fi

# ═══════════════════════════════════════════════════════════════
log_step "PHASE 2: DIRECTORY STRUCTURE"
# ═══════════════════════════════════════════════════════════════

log_info "Creating institutional directory structure..."
$SUDO mkdir -p "$INSTITUTION_ROOT"
$SUDO chown -R "$(whoami):$(whoami)" "$INSTITUTION_ROOT"

mkdir -p "$INSTITUTION_ROOT"/{agents,config,data,logs,reports,sites,products,videos,newsletters,grants,freelance,pod,social,saas,scout}
mkdir -p "$INSTITUTION_ROOT"/data/{db,cache,vectors,reflection}
mkdir -p "$INSTITUTION_ROOT"/config/{niches,streams}
mkdir -p "$INSTITUTION_ROOT"/logs/{agents,system,digests}
mkdir -p "$INSTITUTION_ROOT"/reports/{daily,weekly,strategic}
mkdir -p "$INSTITUTION_ROOT"/sites/{templates,output}
mkdir -p "$INSTITUTION_ROOT"/products/{templates,output,listings}
mkdir -p "$INSTITUTION_ROOT"/videos/{scripts,audio,footage,output,thumbnails}
mkdir -p "$INSTITUTION_ROOT"/newsletters/{templates,output}
mkdir -p "$INSTITUTION_ROOT"/grants/{discovered,drafts,submitted,awarded}
mkdir -p "$INSTITUTION_ROOT"/freelance/{opportunities,proposals,active}
mkdir -p "$INSTITUTION_ROOT"/pod/{designs,listings}
mkdir -p "$INSTITUTION_ROOT"/social/{posts,scheduled,analytics}
mkdir -p "$INSTITUTION_ROOT"/saas/{projects,deployed}
mkdir -p "$INSTITUTION_ROOT"/scout/{opportunities,research}

log_ok "Directory structure created at $INSTITUTION_ROOT"

# ═══════════════════════════════════════════════════════════════
log_step "PHASE 3: PYTHON ENVIRONMENT"
# ═══════════════════════════════════════════════════════════════

VENV_DIR="$INSTITUTION_ROOT/venv"

if [[ ! -d "$VENV_DIR" ]]; then
    log_info "Creating Python virtual environment..."
    python3 -m venv "$VENV_DIR"
    log_ok "Virtual environment created"
else
    log_ok "Virtual environment already exists"
fi

log_info "Installing Python dependencies..."
"$VENV_DIR/bin/pip" install --upgrade pip setuptools wheel -q

"$VENV_DIR/bin/pip" install -q \
    "flask>=3.0" \
    "flask-cors>=4.0" \
    "requests>=2.31" \
    "httpx>=0.27" \
    "aiohttp>=3.9" \
    "pyyaml>=6.0" \
    "python-dotenv>=1.0" \
    "schedule>=1.2" \
    "apscheduler>=3.10" \
    "reportlab>=4.0" \
    "Pillow>=10.0" \
    "beautifulsoup4>=4.12" \
    "lxml>=5.0" \
    "feedparser>=6.0" \
    "jinja2>=3.1" \
    "markdown>=3.5" \
    "chromadb>=0.5" \
    "numpy>=1.26" \
    "psutil>=5.9" \
    "rich>=13.0" \
    "click>=8.1" \
    "tenacity>=8.2" \
    "cachetools>=5.3" \
    "python-slugify>=8.0" \
    "feedgen>=1.0" \
    "edge-tts>=6.1" \
    "google-generativeai>=0.7" \
    "groq>=0.9" \
    "mistralai>=0.4" \
    "cohere>=5.0" \
    "huggingface-hub>=0.23" \
    2>/dev/null || error_exit "Failed to install Python dependencies"

log_ok "Python dependencies installed"

# ═══════════════════════════════════════════════════════════════
log_step "PHASE 4: DATABASE SCHEMA"
# ═══════════════════════════════════════════════════════════════

DB_PATH="$INSTITUTION_ROOT/data/db/institution.db"

log_info "Initializing SQLite database..."
sqlite3 "$DB_PATH" <<'SQL'
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

-- ─── CORE TABLES ─────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    description TEXT NOT NULL,
    agent TEXT NOT NULL DEFAULT 'steward',
    stream TEXT,
    status TEXT DEFAULT 'queued' CHECK(status IN ('queued','in_progress','completed','failed','cancelled')),
    priority INTEGER DEFAULT 3 CHECK(priority BETWEEN 1 AND 5),
    autonomy_level INTEGER DEFAULT 1 CHECK(autonomy_level BETWEEN 0 AND 5),
    requires_approval INTEGER DEFAULT 0,
    approved INTEGER DEFAULT 0,
    result TEXT,
    error TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    started_at DATETIME,
    completed_at DATETIME,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    description TEXT NOT NULL,
    decision TEXT NOT NULL,
    reasoning TEXT,
    alternatives TEXT,
    outcome TEXT,
    lesson TEXT,
    agent TEXT,
    stream TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS learnings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    decision_id INTEGER,
    stream TEXT,
    prediction TEXT NOT NULL,
    outcome TEXT NOT NULL,
    confidence_score INTEGER CHECK(confidence_score >= 0 AND confidence_score <= 100),
    error_magnitude REAL,
    lesson TEXT NOT NULL,
    corrected_belief TEXT,
    tags TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (decision_id) REFERENCES decisions(id)
);

CREATE TABLE IF NOT EXISTS revenue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    stream TEXT NOT NULL,
    source TEXT NOT NULL,
    amount REAL NOT NULL,
    currency TEXT DEFAULT 'AUD',
    tax_reserve REAL DEFAULT 0,
    net_amount REAL,
    notes TEXT,
    recorded_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS expenses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    category TEXT NOT NULL,
    description TEXT NOT NULL,
    amount REAL NOT NULL,
    currency TEXT DEFAULT 'AUD',
    stream TEXT,
    approved INTEGER DEFAULT 0,
    recorded_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS ai_usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    task_type TEXT,
    stream TEXT,
    prompt_tokens INTEGER DEFAULT 0,
    completion_tokens INTEGER DEFAULT 0,
    total_tokens INTEGER DEFAULT 0,
    latency_ms INTEGER,
    quality_tier TEXT DEFAULT 'routine',
    cached INTEGER DEFAULT 0,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS ai_cache (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cache_key TEXT UNIQUE NOT NULL,
    provider TEXT,
    model TEXT,
    prompt_hash TEXT NOT NULL,
    response TEXT NOT NULL,
    quality_tier TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    expires_at DATETIME NOT NULL
);

CREATE TABLE IF NOT EXISTS rate_limits (
    provider TEXT PRIMARY KEY,
    rpm_limit INTEGER,
    rpd_limit INTEGER,
    rpm_used INTEGER DEFAULT 0,
    rpd_used INTEGER DEFAULT 0,
    last_reset_minute DATETIME,
    last_reset_day DATETIME,
    consecutive_429s INTEGER DEFAULT 0,
    backoff_until DATETIME
);

CREATE TABLE IF NOT EXISTS agents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL,
    agent_type TEXT NOT NULL,
    stream TEXT,
    status TEXT DEFAULT 'idle' CHECK(status IN ('idle','running','paused','error','killed')),
    pid INTEGER,
    current_task TEXT,
    tasks_completed INTEGER DEFAULT 0,
    tasks_failed INTEGER DEFAULT 0,
    restart_count INTEGER DEFAULT 0,
    last_heartbeat DATETIME,
    last_action TEXT,
    last_reasoning TEXT,
    config TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS streams (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL,
    slug TEXT UNIQUE NOT NULL,
    status TEXT DEFAULT 'active' CHECK(status IN ('active','paused','killed','pending')),
    autonomy_level INTEGER DEFAULT 1,
    revenue_total REAL DEFAULT 0,
    revenue_month REAL DEFAULT 0,
    cost_total REAL DEFAULT 0,
    kill_criterion TEXT,
    kill_threshold REAL,
    kill_metric TEXT,
    kill_window_days INTEGER DEFAULT 30,
    unit_economics TEXT,
    config TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS constitutional_audits (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    action_description TEXT NOT NULL,
    agent TEXT,
    stream TEXT,
    principle_checked TEXT,
    result TEXT CHECK(result IN ('PASS','FAIL','REVIEW_REQUIRED')),
    reasoning TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS approvals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_type TEXT NOT NULL,
    description TEXT NOT NULL,
    details TEXT,
    stream TEXT,
    agent TEXT,
    status TEXT DEFAULT 'pending' CHECK(status IN ('pending','approved','rejected','expired')),
    priority INTEGER DEFAULT 3,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    resolved_at DATETIME,
    resolved_by TEXT DEFAULT 'founder'
);

CREATE TABLE IF NOT EXISTS founder_checkins (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    energy INTEGER CHECK(energy BETWEEN 1 AND 5),
    pain INTEGER CHECK(pain BETWEEN 1 AND 5),
    fear INTEGER CHECK(fear BETWEEN 1 AND 5),
    available_minutes INTEGER DEFAULT 0,
    notes TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS predictions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    stream TEXT,
    description TEXT NOT NULL,
    predicted_value TEXT NOT NULL,
    predicted_confidence INTEGER CHECK(predicted_confidence BETWEEN 0 AND 100),
    actual_value TEXT,
    actual_measured_at DATETIME,
    error_magnitude REAL,
    scenario_data TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS keywords (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    stream TEXT,
    site TEXT,
    keyword TEXT NOT NULL,
    search_volume INTEGER,
    difficulty INTEGER,
    intent TEXT,
    status TEXT DEFAULT 'discovered' CHECK(status IN ('discovered','targeted','published','ranking')),
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS content_inventory (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    stream TEXT NOT NULL,
    site TEXT,
    content_type TEXT NOT NULL,
    title TEXT NOT NULL,
    slug TEXT,
    url TEXT,
    word_count INTEGER,
    status TEXT DEFAULT 'draft' CHECK(status IN ('draft','published','updated','archived')),
    published_at DATETIME,
    metrics TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS incidents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    severity TEXT CHECK(severity IN ('info','warning','critical')),
    component TEXT NOT NULL,
    description TEXT NOT NULL,
    resolution TEXT,
    resolved INTEGER DEFAULT 0,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    resolved_at DATETIME
);

CREATE TABLE IF NOT EXISTS system_metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cpu_percent REAL,
    ram_percent REAL,
    ram_used_gb REAL,
    disk_percent REAL,
    disk_free_gb REAL,
    gpu_temp REAL,
    gpu_util REAL,
    uptime_seconds REAL,
    network_rx_mb REAL,
    network_tx_mb REAL,
    recorded_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- ─── INDEXES ─────────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
CREATE INDEX IF NOT EXISTS idx_tasks_agent ON tasks(agent);
CREATE INDEX IF NOT EXISTS idx_tasks_stream ON tasks(stream);
CREATE INDEX IF NOT EXISTS idx_revenue_stream ON revenue(stream);
CREATE INDEX IF NOT EXISTS idx_revenue_date ON revenue(recorded_at);
CREATE INDEX IF NOT EXISTS idx_ai_usage_provider ON ai_usage(provider);
CREATE INDEX IF NOT EXISTS idx_ai_usage_date ON ai_usage(created_at);
CREATE INDEX IF NOT EXISTS idx_ai_cache_key ON ai_cache(cache_key);
CREATE INDEX IF NOT EXISTS idx_ai_cache_expires ON ai_cache(expires_at);
CREATE INDEX IF NOT EXISTS idx_learnings_stream ON learnings(stream);
CREATE INDEX IF NOT EXISTS idx_learnings_tags ON learnings(tags);
CREATE INDEX IF NOT EXISTS idx_agents_status ON agents(status);
CREATE INDEX IF NOT EXISTS idx_streams_status ON streams(status);
CREATE INDEX IF NOT EXISTS idx_approvals_status ON approvals(status);
CREATE INDEX IF NOT EXISTS idx_content_site ON content_inventory(site);
CREATE INDEX IF NOT EXISTS idx_content_status ON content_inventory(status);
CREATE INDEX IF NOT EXISTS idx_incidents_resolved ON incidents(resolved);
CREATE INDEX IF NOT EXISTS idx_metrics_date ON system_metrics(recorded_at);

-- ─── INITIAL DATA ────────────────────────────────────────────
INSERT OR IGNORE INTO rate_limits (provider, rpm_limit, rpd_limit) VALUES
    ('groq', 30, 14400),
    ('gemini', 15, 1500),
    ('mistral', 1, 500),
    ('cloudflare', 10, 10000),
    ('huggingface', 60, 5000),
    ('openrouter', 20, 1000),
    ('cohere', 10, 1000),
    ('ollama', 999, 999999),
    ('offline', 999, 999999);

INSERT OR IGNORE INTO streams (name, slug, status, autonomy_level, kill_criterion, kill_threshold, kill_metric, kill_window_days) VALUES
    ('SEO Content Sites', 'content_sites', 'active', 3, 'No traffic growth after 60 days', 0, 'organic_sessions', 60),
    ('Digital Products', 'digital_products', 'active', 2, 'No sales after 45 days', 0, 'sales_count', 45),
    ('Faceless Video', 'video_content', 'active', 2, 'No views growth after 60 days', 0, 'total_views', 60),
    ('Newsletter', 'newsletter', 'active', 2, 'No subscriber growth after 30 days', 0, 'subscriber_count', 30),
    ('Grant Pipeline', 'grants', 'active', 1, 'No awards after 90 days', 0, 'awards_count', 90),
    ('Freelance Tasks', 'freelance', 'active', 1, 'Response rate below 5% after 30 days', 5, 'response_rate', 30),
    ('Print on Demand', 'print_on_demand', 'active', 3, 'No sales after 60 days', 0, 'sales_count', 60),
    ('Affiliate Sites', 'affiliate_sites', 'active', 3, 'No clicks after 45 days', 0, 'affiliate_clicks', 45),
    ('Social Media', 'social_media', 'active', 3, 'No engagement growth after 30 days', 0, 'engagement_rate', 30),
    ('Micro-SaaS', 'micro_saas', 'pending', 1, 'No validated demand after 30 days', 0, 'validation_score', 30),
    ('Stock Content', 'stock_content', 'pending', 3, 'No downloads after 60 days', 0, 'download_count', 60);

SQL

log_ok "Database initialized with schema and seed data"

# ═══════════════════════════════════════════════════════════════
log_step "PHASE 5: ENVIRONMENT FILE"
# ═══════════════════════════════════════════════════════════════

ENV_FILE="$INSTITUTION_ROOT/.env"

if [[ ! -f "$ENV_FILE" ]]; then
    log_info "Creating environment file..."
    cat > "$ENV_FILE" <<'ENV'
# ═══════════════════════════════════════════════════════════════
# THE INSTITUTION — ENVIRONMENT CONFIGURATION
# ═══════════════════════════════════════════════════════════════
# Fill in API keys as you obtain them. System works with ZERO keys.
# Keys marked OPTIONAL can be added later without restart.

# ─── CORE PATHS ───────────────────────────────────────────────
INSTITUTION_ROOT=/opt/institution
DASHBOARD_PORT=8080
LOG_LEVEL=INFO

# ─── AI PROVIDERS (all optional — system degrades gracefully) ─
GROQ_API_KEY=
GEMINI_API_KEY=
MISTRAL_API_KEY=
CLOUDFLARE_ACCOUNT_ID=
CLOUDFLARE_API_TOKEN=
HUGGINGFACE_TOKEN=
OPENROUTER_API_KEY=
COHERE_API_KEY=

# ─── CONTENT DEPLOYMENT ───────────────────────────────────────
CLOUDFLARE_PAGES_TOKEN=
CLOUDFLARE_ZONE_ID=
GITHUB_TOKEN=

# ─── VIDEO / MEDIA ────────────────────────────────────────────
YOUTUBE_API_KEY=
YOUTUBE_CLIENT_ID=
YOUTUBE_CLIENT_SECRET=
YOUTUBE_REFRESH_TOKEN=
PEXELS_API_KEY=
PIXABAY_API_KEY=

# ─── E-COMMERCE ───────────────────────────────────────────────
ETSY_API_KEY=
GUMROAD_ACCESS_TOKEN=
REDBUBBLE_SESSION=
AMAZON_ASSOCIATE_TAG=
SHAREASALE_API_TOKEN=

# ─── SOCIAL MEDIA ─────────────────────────────────────────────
TWITTER_API_KEY=
TWITTER_API_SECRET=
TWITTER_ACCESS_TOKEN=
TWITTER_ACCESS_SECRET=
LINKEDIN_ACCESS_TOKEN=
REDDIT_CLIENT_ID=
REDDIT_CLIENT_SECRET=
PINTEREST_ACCESS_TOKEN=

# ─── NEWSLETTER ───────────────────────────────────────────────
BUTTONDOWN_API_KEY=
LISTMONK_URL=
LISTMONK_USER=
LISTMONK_PASS=

# ─── FREELANCE ────────────────────────────────────────────────
UPWORK_API_KEY=
UPWORK_API_SECRET=

# ─── NOTIFICATIONS ────────────────────────────────────────────
SMTP_HOST=
SMTP_PORT=587
SMTP_USER=
SMTP_PASS=
NOTIFY_EMAIL=

# ─── ORACLE CLOUD (for failover/phase 2) ─────────────────────
OCI_COMPARTMENT_ID=
OCI_NAMESPACE=

# ─── SECURITY ─────────────────────────────────────────────────
SECRET_KEY=change-me-to-random-string
DASHBOARD_USER=founder
DASHBOARD_PASS=change-me-too
ENV
    chmod 600 "$ENV_FILE"
    log_ok "Environment file created at $ENV_FILE"
    log_warn "Edit $ENV_FILE to add API keys. System works without them."
else
    log_ok "Environment file already exists"
fi

# ═══════════════════════════════════════════════════════════════
log_step "PHASE 6: SYSTEMD SERVICES"
# ═══════════════════════════════════════════════════════════════

log_info "Creating systemd service files..."

# Main meta-agent service
$SUDO tee /etc/systemd/system/institution.service > /dev/null <<EOF
[Unit]
Description=The Institution - Meta Agent
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$(whoami)
WorkingDirectory=$INSTITUTION_ROOT
Environment=PYTHONPATH=$INSTITUTION_ROOT
ExecStart=$VENV_DIR/bin/python $INSTITUTION_ROOT/meta_agent.py
Restart=always
RestartSec=30
StartLimitBurst=5
StartLimitIntervalSec=300
StandardOutput=append:$INSTITUTION_ROOT/logs/system/meta_agent.log
StandardError=append:$INSTITUTION_ROOT/logs/system/meta_agent_error.log

[Install]
WantedBy=multi-user.target
EOF

# Dashboard service
$SUDO tee /etc/systemd/system/institution-dashboard.service > /dev/null <<EOF
[Unit]
Description=The Institution - Operations Dashboard
After=network-online.target institution.service
Wants=network-online.target

[Service]
Type=simple
User=$(whoami)
WorkingDirectory=$INSTITUTION_ROOT
Environment=PYTHONPATH=$INSTITUTION_ROOT
ExecStart=$VENV_DIR/bin/python $INSTITUTION_ROOT/dashboard.py
Restart=always
RestartSec=10
StandardOutput=append:$INSTITUTION_ROOT/logs/system/dashboard.log
StandardError=append:$INSTITUTION_ROOT/logs/system/dashboard_error.log

[Install]
WantedBy=multi-user.target
EOF

# Safety officer service (lightweight, always running)
$SUDO tee /etc/systemd/system/institution-safety.service > /dev/null <<EOF
[Unit]
Description=The Institution - Safety Officer
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$(whoami)
WorkingDirectory=$INSTITUTION_ROOT
Environment=PYTHONPATH=$INSTITUTION_ROOT
ExecStart=$VENV_DIR/bin/python -c "import sys; sys.path.insert(0,'$INSTITUTION_ROOT'); from agents.safety_officer import SafetyOfficer; SafetyOfficer().run_forever()"
Restart=always
RestartSec=15
StandardOutput=append:$INSTITUTION_ROOT/logs/system/safety.log
StandardError=append:$INSTITUTION_ROOT/logs/system/safety_error.log

[Install]
WantedBy=multi-user.target
EOF

$SUDO systemctl daemon-reload
log_ok "Systemd services created"

# ═══════════════════════════════════════════════════════════════
log_step "PHASE 7: CRON JOBS"
# ═══════════════════════════════════════════════════════════════

log_info "Setting up cron jobs..."
CRON_FILE="/tmp/institution_cron"
crontab -l 2>/dev/null | grep -v "institution" > "$CRON_FILE" || true

cat >> "$CRON_FILE" <<EOF
# ─── THE INSTITUTION CRON JOBS ────────────────────────────────
# Daily digest generation (7:00 AM)
0 7 * * * cd $INSTITUTION_ROOT && $VENV_DIR/bin/python -c "from meta_agent import MetaAgent; MetaAgent().generate_digest()" >> $INSTITUTION_ROOT/logs/system/cron.log 2>&1
# Weekly strategic report (Sunday 8:00 AM)
0 8 * * 0 cd $INSTITUTION_ROOT && $VENV_DIR/bin/python -c "from meta_agent import MetaAgent; MetaAgent().generate_weekly_report()" >> $INSTITUTION_ROOT/logs/system/cron.log 2>&1
# Hourly metrics collection
0 * * * * cd $INSTITUTION_ROOT && $VENV_DIR/bin/python -c "from agents.safety_officer import SafetyOfficer; SafetyOfficer().collect_metrics()" >> $INSTITUTION_ROOT/logs/system/cron.log 2>&1
# Daily database backup (3:00 AM)
0 3 * * * sqlite3 $INSTITUTION_ROOT/data/db/institution.db ".backup $INSTITUTION_ROOT/data/db/backups/institution_\$(date +\%Y\%m\%d).db" >> $INSTITUTION_ROOT/logs/system/cron.log 2>&1
# Weekly cache cleanup (Monday 4:00 AM)
0 4 * * 1 cd $INSTITUTION_ROOT && $VENV_DIR/bin/python -c "from common import InstitutionDB; InstitutionDB().cleanup_expired_cache()" >> $INSTITUTION_ROOT/logs/system/cron.log 2>&1
# Daily log rotation
0 2 * * * find $INSTITUTION_ROOT/logs -name "*.log" -mtime +30 -delete >> $INSTITUTION_ROOT/logs/system/cron.log 2>&1
EOF

crontab "$CRON_FILE"
rm -f "$CRON_FILE"

# Create backup directory
mkdir -p "$INSTITUTION_ROOT/data/db/backups"

log_ok "Cron jobs installed"

# ═══════════════════════════════════════════════════════════════
log_step "PHASE 8: GIT INITIALIZATION"
# ═══════════════════════════════════════════════════════════════

if [[ ! -d "$INSTITUTION_ROOT/.git" ]]; then
    log_info "Initializing Git repository for institutional memory..."
    cd "$INSTITUTION_ROOT"
    git init -q
    git config user.email "institution@localhost"
    git config user.name "The Institution"

    cat > .gitignore <<'GITIGNORE'
venv/
__pycache__/
*.pyc
.env
data/db/*.db
data/db/*.db-wal
data/db/*.db-shm
data/cache/
data/vectors/
logs/
sites/output/
products/output/
videos/output/
videos/footage/
videos/audio/
*.log
node_modules/
GITIGNORE

    git add -A
    git commit -m "Initial institution structure" -q

    if [[ -n "$GIT_REPO" ]]; then
        git remote add origin "$GIT_REPO" 2>/dev/null || true
        git push -u origin main 2>/dev/null || log_warn "Could not push to remote. Set GIT_REPO and push manually."
    fi

    log_ok "Git repository initialized"
else
    log_ok "Git repository already exists"
fi

# ═══════════════════════════════════════════════════════════════
log_step "PHASE 9: FIREWALL & NETWORK"
# ═══════════════════════════════════════════════════════════════

if command -v ufw &>/dev/null; then
    log_info "Configuring firewall..."
    $SUDO ufw allow 22/tcp comment "SSH" 2>/dev/null || true
    $SUDO ufw allow "$DASHBOARD_PORT/tcp" comment "Institution Dashboard" 2>/dev/null || true
    $SUDO ufw --force enable 2>/dev/null || true
    log_ok "Firewall configured"
else
    log_warn "ufw not available. Ensure port $DASHBOARD_PORT is accessible."
fi

# ═══════════════════════════════════════════════════════════════
log_step "PHASE 10: START SERVICES"
# ═══════════════════════════════════════════════════════════════

log_info "Enabling and starting services..."
$SUDO systemctl enable institution.service 2>/dev/null || true
$SUDO systemctl enable institution-dashboard.service 2>/dev/null || true
$SUDO systemctl enable institution-safety.service 2>/dev/null || true

# Only start if the code files exist
if [[ -f "$INSTITUTION_ROOT/meta_agent.py" ]]; then
    $SUDO systemctl restart institution.service
    log_ok "Meta-agent service started"
else
    log_warn "meta_agent.py not yet deployed. Service will start on next boot or manual start."
fi

if [[ -f "$INSTITUTION_ROOT/dashboard.py" ]]; then
    $SUDO systemctl restart institution-dashboard.service
    log_ok "Dashboard service started"
else
    log_warn "dashboard.py not yet deployed. Service will start on next boot or manual start."
fi

if [[ -f "$INSTITUTION_ROOT/agents/safety_officer.py" ]]; then
    $SUDO systemctl restart institution-safety.service
    log_ok "Safety officer service started"
else
    log_warn "safety_officer.py not yet deployed. Service will start on next boot or manual start."
fi

# ═══════════════════════════════════════════════════════════════
log_step "PHASE 11: VERIFICATION"
# ═══════════════════════════════════════════════════════════════

log_info "Running verification checks..."

PASS=0
FAIL=0

check() {
    if eval "$2" &>/dev/null; then
        log_ok "$1"
        ((PASS++))
    else
        log_warn "$1 — NOT READY (will activate when code is deployed)"
        ((FAIL++))
    fi
}

check "Python venv" "[[ -f $VENV_DIR/bin/python ]]"
check "SQLite DB" "[[ -f $DB_PATH ]]"
check "Hugo" "command -v hugo"
check "ffmpeg" "command -v ffmpeg"
check "Git repo" "[[ -d $INSTITUTION_ROOT/.git ]]"
check "Directory structure" "[[ -d $INSTITUTION_ROOT/agents ]]"
check "Environment file" "[[ -f $ENV_FILE ]]"
check "Systemd services" "systemctl list-unit-files | grep institution"
check "Cron jobs" "crontab -l | grep institution"
check "Ollama" "command -v ollama"
check "Node.js" "command -v node"
check "Pillow (images)" "$VENV_DIR/bin/python -c 'import PIL'"
check "ReportLab (PDFs)" "$VENV_DIR/bin/python -c 'import reportlab'"
check "Flask (dashboard)" "$VENV_DIR/bin/python -c 'import flask'"
check "Requests (HTTP)" "$VENV_DIR/bin/python -c 'import requests'"

echo ""
echo -e "${GREEN}══════════════════════════════════════════════════════════════${NC}"
echo -e "${GREEN}  SETUP COMPLETE${NC}"
echo -e "${GREEN}══════════════════════════════════════════════════════════════${NC}"
echo ""
echo -e "  Passed: ${GREEN}$PASS${NC}  |  Pending: ${YELLOW}$FAIL${NC}"
echo ""
echo -e "  Institution root:  ${BLUE}$INSTITUTION_ROOT${NC}"
echo -e "  Dashboard:         ${BLUE}http://$(hostname -I | awk '{print $1}'):$DASHBOARD_PORT${NC}"
echo -e "  Database:          ${BLUE}$DB_PATH${NC}"
echo -e "  Logs:              ${BLUE}$INSTITUTION_ROOT/logs/${NC}"
echo -e "  Setup log:         ${BLUE}$LOG_FILE${NC}"
echo ""
echo -e "  ${YELLOW}NEXT STEPS:${NC}"
echo -e "  1. Deploy the Python code files to $INSTITUTION_ROOT/"
echo -e "  2. Edit $ENV_FILE to add API keys (optional)"
echo -e "  3. Run: sudo systemctl restart institution institution-dashboard institution-safety"
echo -e "  4. Open the dashboard URL above"
echo ""
echo -e "  ${GREEN}The Institution is ready to receive its code.${NC}"
echo ""