#!/usr/bin/env python3
"""
═══════════════════════════════════════════════════════════════
THE INSTITUTION — SHARED UTILITIES
═══════════════════════════════════════════════════════════════
Database, logging, config, caching, metrics, agent management.
Every module imports from here. This is the foundation.
═══════════════════════════════════════════════════════════════
"""

import os
import sys
import json
import yaml
import sqlite3
import hashlib
import logging
import threading
from pathlib import Path
from datetime import datetime, timedelta
from typing import Any, Optional
from contextlib import contextmanager
from functools import wraps
import time

# ─── PATHS ────────────────────────────────────────────────────
INSTITUTION_ROOT = Path(os.environ.get("INSTITUTION_ROOT", "/opt/institution"))
CONFIG_PATH = INSTITUTION_ROOT / "config.yaml"
ENV_PATH = INSTITUTION_ROOT / ".env"
DB_PATH = INSTITUTION_ROOT / "data" / "db" / "institution.db"
LOG_DIR = INSTITUTION_ROOT / "logs"
CACHE_DIR = INSTITUTION_ROOT / "data" / "cache"

# Ensure directories exist
for d in [INSTITUTION_ROOT, DB_PATH.parent, LOG_DIR, CACHE_DIR]:
    d.mkdir(parents=True, exist_ok=True)


# ─── ENVIRONMENT LOADER ───────────────────────────────────────
def load_env():
    """Load .env file into os.environ without external dependency."""
    if not ENV_PATH.exists():
        return
    with open(ENV_PATH) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, _, value = line.partition("=")
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                if value and key not in os.environ:
                    os.environ[key] = value

load_env()


# ─── CONFIGURATION ────────────────────────────────────────────
class Config:
    """Singleton configuration manager."""
    _instance = None
    _config = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._load()
        return cls._instance

    def _load(self):
        if CONFIG_PATH.exists():
            with open(CONFIG_PATH) as f:
                self._config = yaml.safe_load(f) or {}
        else:
            self._config = {}

    def get(self, *keys, default=None):
        """Get nested config value: config.get('ai', 'providers', 'groq', 'enabled')"""
        val = self._config
        for key in keys:
            if isinstance(val, dict):
                val = val.get(key)
            else:
                return default
            if val is None:
                return default
        return val

    def reload(self):
        self._load()

    @property
    def raw(self):
        return self._config


def get_config():
    return Config()


# ─── LOGGING ──────────────────────────────────────────────────
def get_logger(name: str, level: str = None) -> logging.Logger:
    """Get a configured logger that writes to file and console."""
    if level is None:
        level = os.environ.get("LOG_LEVEL", "INFO")

    logger = logging.getLogger(f"institution.{name}")
    if logger.handlers:
        return logger

    logger.setLevel(getattr(logging, level.upper(), logging.INFO))

    # Console handler
    console = logging.StreamHandler(sys.stdout)
    console.setLevel(logging.INFO)
    console_fmt = logging.Formatter(
        "%(asctime)s │ %(name)-20s │ %(levelname)-7s │ %(message)s",
        datefmt="%H:%M:%S"
    )
    console.setFormatter(console_fmt)
    logger.addHandler(console)

    # File handler
    log_file = LOG_DIR / "agents" / f"{name}.log"
    log_file.parent.mkdir(parents=True, exist_ok=True)
    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_fmt = logging.Formatter(
        "%(asctime)s │ %(name)s │ %(levelname)s │ %(funcName)s:%(lineno)d │ %(message)s"
    )
    file_handler.setFormatter(file_fmt)
    logger.addHandler(file_handler)

    return logger


# ─── DATABASE ─────────────────────────────────────────────────
class InstitutionDB:
    """Thread-safe SQLite database manager."""

    def __init__(self, db_path: Path = None):
        self.db_path = str(db_path or DB_PATH)
        self._local = threading.local()

    @property
    def _conn(self) -> sqlite3.Connection:
        if not hasattr(self._local, "conn") or self._local.conn is None:
            self._local.conn = sqlite3.connect(self.db_path, timeout=30)
            self._local.conn.row_factory = sqlite3.Row
            self._local.conn.execute("PRAGMA journal_mode=WAL")
            self._local.conn.execute("PRAGMA foreign_keys=ON")
            self._local.conn.execute("PRAGMA busy_timeout=5000")
        return self._local.conn

    @contextmanager
    def transaction(self):
        """Context manager for database transactions."""
        conn = self._conn
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    def execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        with self.transaction() as conn:
            return conn.execute(sql, params)

    def executemany(self, sql: str, params_list: list) -> sqlite3.Cursor:
        with self.transaction() as conn:
            return conn.executemany(sql, params_list)

    def fetchone(self, sql: str, params: tuple = ()) -> Optional[dict]:
        cursor = self._conn.execute(sql, params)
        row = cursor.fetchone()
        return dict(row) if row else None

    def fetchall(self, sql: str, params: tuple = ()) -> list:
        cursor = self._conn.execute(sql, params)
        return [dict(row) for row in cursor.fetchall()]

    def insert(self, table: str, data: dict) -> int:
        columns = ", ".join(data.keys())
        placeholders = ", ".join(["?"] * len(data))
        sql = f"INSERT INTO {table} ({columns}) VALUES ({placeholders})"
        with self.transaction() as conn:
            cursor = conn.execute(sql, tuple(data.values()))
            return cursor.lastrowid

    def update(self, table: str, data: dict, where: str, where_params: tuple = ()) -> int:
        set_clause = ", ".join([f"{k} = ?" for k in data.keys()])
        sql = f"UPDATE {table} SET {set_clause} WHERE {where}"
        with self.transaction() as conn:
            cursor = conn.execute(sql, tuple(data.values()) + where_params)
            return cursor.rowcount

    # ─── TASK MANAGEMENT ──────────────────────────────────────
    def add_task(self, description: str, agent: str = "steward", stream: str = None,
                 priority: int = 3, autonomy_level: int = 1, requires_approval: bool = False) -> int:
        return self.insert("tasks", {
            "description": description,
            "agent": agent,
            "stream": stream,
            "priority": priority,
            "autonomy_level": autonomy_level,
            "requires_approval": 1 if requires_approval else 0,
            "status": "queued",
        })

    def get_next_task(self, agent: str = None) -> Optional[dict]:
        if agent:
            return self.fetchone(
                "SELECT * FROM tasks WHERE status='queued' AND agent=? ORDER BY priority ASC, created_at ASC LIMIT 1",
                (agent,)
            )
        return self.fetchone(
            "SELECT * FROM tasks WHERE status='queued' ORDER BY priority ASC, created_at ASC LIMIT 1"
        )

    def complete_task(self, task_id: int, result: str = None):
        self.update("tasks", {
            "status": "completed",
            "result": result,
            "completed_at": datetime.now().isoformat(),
            "updated_at": datetime.now().isoformat(),
        }, "id = ?", (task_id,))

    def fail_task(self, task_id: int, error: str):
        self.update("tasks", {
            "status": "failed",
            "error": error,
            "updated_at": datetime.now().isoformat(),
        }, "id = ?", (task_id,))

    # ─── REVENUE TRACKING ─────────────────────────────────────
    def record_revenue(self, stream: str, source: str, amount: float, notes: str = ""):
        tax_reserve = amount * 0.30
        net = amount - tax_reserve
        self.insert("revenue", {
            "stream": stream,
            "source": source,
            "amount": amount,
            "tax_reserve": tax_reserve,
            "net_amount": net,
            "notes": notes,
        })
        # Update stream totals
        self.execute(
            "UPDATE streams SET revenue_total = revenue_total + ?, updated_at = ? WHERE slug = ?",
            (amount, datetime.now().isoformat(), stream)
        )

    def get_runway_days(self) -> float:
        """Calculate financial runway in days."""
        # Get total available funds (simplified — real impl would check bank APIs)
        revenue_row = self.fetchone("SELECT COALESCE(SUM(net_amount), 0) as total FROM revenue")
        expense_row = self.fetchone("SELECT COALESCE(SUM(amount), 0) as total FROM expenses")
        total_revenue = revenue_row["total"] if revenue_row else 0
        total_expenses = expense_row["total"] if expense_row else 0
        balance = total_revenue - total_expenses

        # Calculate daily burn rate from last 30 days
        burn_row = self.fetchone(
            "SELECT COALESCE(SUM(amount), 0) / 30.0 as daily_burn FROM expenses WHERE recorded_at > datetime('now', '-30 days')"
        )
        daily_burn = burn_row["daily_burn"] if burn_row and burn_row["daily_burn"] > 0 else 5.0  # Default $5/day

        return max(0, balance / daily_burn) if daily_burn > 0 else 999

    # ─── AI USAGE TRACKING ────────────────────────────────────
    def log_ai_usage(self, provider: str, model: str, task_type: str = None,
                     stream: str = None, prompt_tokens: int = 0,
                     completion_tokens: int = 0, latency_ms: int = 0,
                     quality_tier: str = "routine", cached: bool = False):
        self.insert("ai_usage", {
            "provider": provider,
            "model": model,
            "task_type": task_type,
            "stream": stream,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
            "latency_ms": latency_ms,
            "quality_tier": quality_tier,
            "cached": 1 if cached else 0,
        })

    # ─── CACHE MANAGEMENT ─────────────────────────────────────
    def get_cached_response(self, cache_key: str) -> Optional[str]:
        row = self.fetchone(
            "SELECT response FROM ai_cache WHERE cache_key = ? AND expires_at > datetime('now')",
            (cache_key,)
        )
        return row["response"] if row else None

    def set_cached_response(self, cache_key: str, response: str, provider: str = None,
                            model: str = None, prompt_hash: str = None,
                            quality_tier: str = "routine", ttl_hours: int = 168):
        expires = (datetime.now() + timedelta(hours=ttl_hours)).isoformat()
        self.execute(
            """INSERT OR REPLACE INTO ai_cache
               (cache_key, provider, model, prompt_hash, response, quality_tier, expires_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (cache_key, provider, model, prompt_hash, response, quality_tier, expires)
        )

    def cleanup_expired_cache(self):
        self.execute("DELETE FROM ai_cache WHERE expires_at < datetime('now')")

    # ─── LEARNINGS / REFLECTION ───────────────────────────────
    def add_learning(self, prediction: str, outcome: str, lesson: str,
                     confidence: int = 50, stream: str = None,
                     corrected_belief: str = None, tags: list = None,
                     decision_id: int = None):
        self.insert("learnings", {
            "decision_id": decision_id,
            "stream": stream,
            "prediction": prediction,
            "outcome": outcome,
            "confidence_score": confidence,
            "lesson": lesson,
            "corrected_belief": corrected_belief,
            "tags": json.dumps(tags or []),
        })

    def get_lessons(self, stream: str = None, limit: int = 20) -> list:
        if stream:
            return self.fetchall(
                "SELECT * FROM learnings WHERE stream = ? ORDER BY created_at DESC LIMIT ?",
                (stream, limit)
            )
        return self.fetchall(
            "SELECT * FROM learnings ORDER BY created_at DESC LIMIT ?", (limit,)
        )

    # ─── AGENT MANAGEMENT ─────────────────────────────────────
    def register_agent(self, name: str, agent_type: str, stream: str = None, config: dict = None):
        existing = self.fetchone("SELECT id FROM agents WHERE name = ?", (name,))
        if existing:
            self.update("agents", {
                "status": "idle",
                "last_heartbeat": datetime.now().isoformat(),
                "updated_at": datetime.now().isoformat(),
            }, "name = ?", (name,))
        else:
            self.insert("agents", {
                "name": name,
                "agent_type": agent_type,
                "stream": stream,
                "status": "idle",
                "config": json.dumps(config or {}),
            })

    def agent_heartbeat(self, name: str, current_task: str = None):
        self.update("agents", {
            "last_heartbeat": datetime.now().isoformat(),
            "current_task": current_task,
            "updated_at": datetime.now().isoformat(),
        }, "name = ?", (name,))

    def agent_completed_task(self, name: str, action: str = None, reasoning: str = None):
        self.execute(
            """UPDATE agents SET tasks_completed = tasks_completed + 1,
               last_action = ?, last_reasoning = ?, updated_at = ? WHERE name = ?""",
            (action, reasoning, datetime.now().isoformat(), name)
        )

    def agent_failed_task(self, name: str, error: str = None):
        self.execute(
            """UPDATE agents SET tasks_failed = tasks_failed + 1,
               status = 'error', last_action = ?, updated_at = ? WHERE name = ?""",
            (error, datetime.now().isoformat(), name)
        )

    def get_agent_status(self, name: str) -> Optional[dict]:
        return self.fetchone("SELECT * FROM agents WHERE name = ?", (name,))

    def get_all_agents(self) -> list:
        return self.fetchall("SELECT * FROM agents ORDER BY name")

    # ─── APPROVALS ────────────────────────────────────────────
    def add_approval(self, item_type: str, description: str, details: str = None,
                     stream: str = None, agent: str = None, priority: int = 3) -> int:
        return self.insert("approvals", {
            "item_type": item_type,
            "description": description,
            "details": details,
            "stream": stream,
            "agent": agent,
            "priority": priority,
            "status": "pending",
        })

    def get_pending_approvals(self) -> list:
        return self.fetchall(
            "SELECT * FROM approvals WHERE status = 'pending' ORDER BY priority ASC, created_at ASC"
        )

    def resolve_approval(self, approval_id: int, status: str):
        self.update("approvals", {
            "status": status,
            "resolved_at": datetime.now().isoformat(),
        }, "id = ?", (approval_id,))

    # ─── FOUNDER CHECK-IN ─────────────────────────────────────
    def record_checkin(self, energy: int, pain: int, fear: int,
                       available_minutes: int = 0, notes: str = None):
        self.insert("founder_checkins", {
            "energy": energy,
            "pain": pain,
            "fear": fear,
            "available_minutes": available_minutes,
            "notes": notes,
        })

    def get_latest_checkin(self) -> Optional[dict]:
        return self.fetchone(
            "SELECT * FROM founder_checkins ORDER BY created_at DESC LIMIT 1"
        )

    def get_checkin_streak(self) -> int:
        """How many consecutive days has the founder checked in."""
        checkins = self.fetchall(
            "SELECT DATE(created_at) as day FROM founder_checkins ORDER BY day DESC LIMIT 30"
        )
        if not checkins:
            return 0
        streak = 1
        for i in range(1, len(checkins)):
            prev = datetime.strptime(checkins[i-1]["day"], "%Y-%m-%d")
            curr = datetime.strptime(checkins[i]["day"], "%Y-%m-%d")
            if (prev - curr).days == 1:
                streak += 1
            else:
                break
        return streak

    # ─── SYSTEM METRICS ───────────────────────────────────────
    def record_metrics(self, metrics: dict):
        self.insert("system_metrics", metrics)

    def get_latest_metrics(self) -> Optional[dict]:
        return self.fetchone("SELECT * FROM system_metrics ORDER BY recorded_at DESC LIMIT 1")

    # ─── INCIDENTS ────────────────────────────────────────────
    def log_incident(self, severity: str, component: str, description: str):
        self.insert("incidents", {
            "severity": severity,
            "component": component,
            "description": description,
        })

    def resolve_incident(self, incident_id: int, resolution: str):
        self.update("incidents", {
            "resolved": 1,
            "resolution": resolution,
            "resolved_at": datetime.now().isoformat(),
        }, "id = ?", (incident_id,))

    # ─── STREAM MANAGEMENT ────────────────────────────────────
    def get_stream(self, slug: str) -> Optional[dict]:
        return self.fetchone("SELECT * FROM streams WHERE slug = ?", (slug,))

    def get_active_streams(self) -> list:
        return self.fetchall("SELECT * FROM streams WHERE status = 'active'")

    def update_stream_status(self, slug: str, status: str):
        self.update("streams", {
            "status": status,
            "updated_at": datetime.now().isoformat(),
        }, "slug = ?", (slug,))

    # ─── CONSTITUTIONAL AUDITS ────────────────────────────────
    def log_audit(self, action: str, agent: str, stream: str,
                  principle: str, result: str, reasoning: str):
        self.insert("constitutional_audits", {
            "action_description": action,
            "agent": agent,
            "stream": stream,
            "principle_checked": principle,
            "result": result,
            "reasoning": reasoning,
        })

    # ─── PREDICTIONS ──────────────────────────────────────────
    def add_prediction(self, stream: str, description: str, predicted_value: str,
                       confidence: int, scenario_data: dict = None) -> int:
        return self.insert("predictions", {
            "stream": stream,
            "description": description,
            "predicted_value": predicted_value,
            "predicted_confidence": confidence,
            "scenario_data": json.dumps(scenario_data or {}),
        })

    def record_prediction_outcome(self, prediction_id: int, actual_value: str):
        pred = self.fetchone("SELECT * FROM predictions WHERE id = ?", (prediction_id,))
        if not pred:
            return
        try:
            predicted = float(pred["predicted_value"])
            actual = float(actual_value)
            error = abs(predicted - actual) / max(abs(actual), 0.001) * 100
        except (ValueError, TypeError):
            error = None

        self.update("predictions", {
            "actual_value": actual_value,
            "actual_measured_at": datetime.now().isoformat(),
            "error_magnitude": error,
        }, "id = ?", (prediction_id,))


# ─── UTILITY FUNCTIONS ────────────────────────────────────────
def generate_cache_key(prompt: str, model: str = "", quality_tier: str = "") -> str:
    """Generate a deterministic cache key from prompt content."""
    content = f"{model}:{quality_tier}:{prompt}"
    return hashlib.sha256(content.encode()).hexdigest()[:32]


def slugify(text: str) -> str:
    """Convert text to URL-safe slug."""
    import re
    text = text.lower().strip()
    text = re.sub(r'[^\w\s-]', '', text)
    text = re.sub(r'[\s_]+', '-', text)
    text = re.sub(r'-+', '-', text)
    return text.strip('-')


def now_iso() -> str:
    return datetime.now().isoformat()


def today_str() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def retry(max_attempts: int = 3, delay: float = 2.0, backoff: float = 2.0, exceptions: tuple = (Exception,)):
    """Decorator for retry logic with exponential backoff."""
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            _delay = delay
            last_exception = None
            for attempt in range(max_attempts):
                try:
                    return func(*args, **kwargs)
                except exceptions as e:
                    last_exception = e
                    if attempt < max_attempts - 1:
                        time.sleep(_delay)
                        _delay *= backoff
            raise last_exception
        return wrapper
    return decorator


def safe_json_loads(text: str, default: Any = None) -> Any:
    """Parse JSON safely, returning default on failure."""
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return default


def truncate(text: str, max_length: int = 500) -> str:
    if len(text) <= max_length:
        return text
    return text[:max_length - 3] + "..."


# ─── SINGLETON ACCESS ─────────────────────────────────────────
_db_instance = None
_db_lock = threading.Lock()

def get_db() -> InstitutionDB:
    global _db_instance
    if _db_instance is None:
        with _db_lock:
            if _db_instance is None:
                _db_instance = InstitutionDB()
    return _db_instance