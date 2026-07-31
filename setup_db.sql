-- SQLite Database Setup for The Institution
-- Run this with: sqlite3 /opt/institution/data/db/institution.db < setup_db.sql

PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

-- ============================================
-- CORE TABLES
-- ============================================

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

-- ============================================
-- INDEXES
-- ============================================

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
CREATE INDEX IF NOT EXISTS idx_agents_status ON agents(status);
CREATE INDEX IF NOT EXISTS idx_streams_status ON streams(status);
CREATE INDEX IF NOT EXISTS idx_approvals_status ON approvals(status);
CREATE INDEX IF NOT EXISTS idx_content_site ON content_inventory(site);
CREATE INDEX IF NOT EXISTS idx_incidents_resolved ON incidents(resolved);
CREATE INDEX IF NOT EXISTS idx_metrics_date ON system_metrics(recorded_at);

-- ============================================
-- INITIAL DATA
-- ============================================

-- Rate limits for AI providers
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

-- Phase 1: 3 streams
INSERT OR IGNORE INTO streams (name, slug, status, autonomy_level, kill_criterion, kill_threshold, kill_metric, kill_window_days) VALUES
    ('Content Sites', 'content_sites', 'active', 3, 'No traffic growth after 60 days', 0, 'organic_sessions', 60),
    ('Digital Products', 'digital_products', 'active', 2, 'No sales after 45 days', 0, 'sales_count', 45),
    ('Newsletter', 'newsletter', 'active', 2, 'No subscriber growth after 30 days', 0, 'subscriber_count', 30);

-- ============================================
-- VIEWS (Optional - for easier querying)
-- ============================================

CREATE VIEW IF NOT EXISTS v_active_agents AS
SELECT a.name, a.agent_type, a.stream, a.status, s.name as stream_name
FROM agents a
LEFT JOIN streams s ON a.stream = s.slug;

CREATE VIEW IF NOT EXISTS v_recent_tasks AS
SELECT * FROM tasks
WHERE created_at > datetime('now', '-7 days')
ORDER BY created_at DESC
LIMIT 100;

CREATE VIEW IF NOT EXISTS v_stream_performance AS
SELECT s.slug, s.name, s.status, s.autonomy_level,
       COALESCE(SUM(r.amount), 0) as total_revenue,
       COUNT(DISTINCT c.id) as content_count
FROM streams s
LEFT JOIN revenue r ON s.slug = r.stream
LEFT JOIN content_inventory c ON s.slug = c.stream
GROUP BY s.slug, s.name, s.status, s.autonomy_level;
