#!/usr/bin/env python3
"""
═══════════════════════════════════════════════════════════════
THE INSTITUTION — BASE AGENT CLASS
═══════════════════════════════════════════════════════════════
All agents inherit from this. Provides:
- Scheduling and heartbeat
- Error handling with retry
- AI provider access
- Constitutional Court integration
- Database access
- Logging
- Metrics reporting
═══════════════════════════════════════════════════════════════
"""

import time
import threading
import traceback
from datetime import datetime, timedelta
from typing import Optional, Any
from abc import ABC, abstractmethod

from common import get_db, get_config, get_logger, now_iso
from providers import get_ai_provider
from constitutional_court import get_court


class BaseAgent(ABC):
    """
    Base class for all Institution agents.
    Provides scheduling, heartbeat, error handling, and shared infrastructure.
    """

    # Override in subclasses
    AGENT_NAME = "base_agent"
    AGENT_TYPE = "generic"
    STREAM = None
    DEFAULT_INTERVAL_SECONDS = 3600  # 1 hour

    def __init__(self):
        self.db = get_db()
        self.config = get_config()
        self.ai = get_ai_provider()
        self.court = get_court()
        self.logger = get_logger(self.AGENT_NAME)
        self._running = False
        self._thread = None
        self._interval = self.DEFAULT_INTERVAL_SECONDS
        self._consecutive_failures = 0
        self._max_consecutive_failures = 5
        self._last_run = None
        self._tasks_this_session = 0

        # Register with database
        self.db.register_agent(self.AGENT_NAME, self.AGENT_TYPE, self.STREAM)

    # ─── ABSTRACT METHODS (must implement) ────────────────────

    @abstractmethod
    def run_once(self) -> Any:
        """
        Execute one cycle of this agent's work.
        Must be implemented by every agent.
        Returns a result string or dict.
        """
        pass

    # ─── OPTIONAL OVERRIDES ──────────────────────────────────
─

    def on_start(self):
        """Called when agent starts. Override for initialization."""
        pass

    def on_stop(self):
        """Called when agent stops. Override for cleanup."""
        pass

    def execute_task(self, task: dict) -> Any:
        """
        Execute a specific task from the queue.
        Override for task-specific behavior.
        Default: calls run_once().
        """
        return self.run_once()

    def get_status(self) -> dict:
        """Return agent status for dashboard."""
        return {
            "name": self.AGENT_NAME,
            "type": self.AGENT_TYPE,
            "stream": self.STREAM,
            "running": self._running,
            "last_run": self._last_run.isoformat() if self._last_run else None,
            "consecutive_failures": self._consecutive_failures,
            "tasks_this_session": self._tasks_this_session,
            "interval_seconds": self._interval,
        }

    # ─── EXECUTION LOOP ───────────────────────────────────────

    def run_forever(self):
        """Run the agent in a continuous loop with scheduling."""
        self._running = True
        self.on_start()
        self.logger.info(f"Agent '{self.AGENT_NAME}' started. Interval: {self._interval}s")

        while self._running:
            start_time = time.time()

            try:
                # Heartbeat
                self.db.agent_heartbeat(self.AGENT_NAME, f"Running cycle at {now_iso()}")

                # Execute
                result = self.run_once()
                self._last_run = datetime.now()
                self._tasks_this_session += 1
                self._consecutive_failures = 0

                # Log success
                self.db.agent_completed_task(
                    self.AGENT_NAME,
                    f"Cycle completed: {str(result)[:100] if result else 'OK'}",
                    "Scheduled execution"
                )

            except Exception as e:
                self._consecutive_failures +
= 1
                error_msg = f"{type(e).__name__}: {str(e)[:200]}"
                self.logger.error(f"Agent '{self.AGENT_NAME}' error: {error_msg}")
                self.db.agent_failed_task(self.AGENT_NAME, error_msg)

                # Circuit breaker
                if self._consecutive_failures >= self._max_consecutive_failures:
                    self.logger.critical(
                        f"Agent '{self.AGENT_NAME}' hit circuit breaker "
                        f"({self._max_consecutive_failures} consecutive failures). Pausing."
                    )
                    self.db.update("agents", {"status": "error"}, "name = ?", (self.AGENT_NAME,))
                    self.db.log_incident(
                        "critical", self.AGENT_NAME,
                        f"Circuit breaker: {self._consecutive_failures} consecutive failures. Last: {error_msg}"
                    )
                    # Back off exponentially
                    backoff = min(300 * (2 ** (self._consecutive_failures - self._max_consecutive_failures)), 3600)
                    time.sleep(backoff)
                    # Try to recover
                    self._consecutive_failures = 0
                    self.db.update("agents", {"status": "running"}, "name = ?", (self.AGENT_NAME,))
                    continue

            # Sleep for remainder of interval
            elapsed = time.time() - start_time
            sleep_time = max(0, self._interval - elapsed)
            if sleep_time > 0 and self._running:
                time.sleep(sleep_time)

        self.on_stop()
        self.logger.info(f"Agent '{self.AGENT_NAME}' stopped.")

    def start_background(self):
        """Start the agent in a background thread."""
        if self._thread and self._thread.is_alive():
            self.logger.warning(f"Agent '{self.AGENT_NAME}' already running.")
            return
        self._thread = threading.Thread(target=self.run_forever, daemon=True, name=self.AGENT_NAME)
        self._thre
ad.start()

    def stop(self):
        """Stop the agent gracefully."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=30)

    # ─── SHARED UTILITIES ─────────────────────────────────────

    def audit_action(self, action: str, context: dict = None):
        """Run constitutional audit on a proposed action."""
        return self.court.audit(
            action=action,
            agent=self.AGENT_NAME,
            stream=self.STREAM,
            context=context,
        )

    def generate_text(self, prompt: str, system_prompt: str = None,
                      quality_tier: str = "routine", max_tokens: int = None,
                      temperature: float = 0.7, use_cache: bool = True) -> str:
        """Generate text via AI provider with proper attribution."""
        return self.ai.generate(
            prompt=prompt,
            system_prompt=system_prompt,
            quality_tier=quality_tier,
            stream=self.STREAM,
            task_type=self.AGENT_NAME,
            max_tokens=max_tokens,
            temperature=temperature,
            use_cache=use_cache,
        )

    def record_revenue(self, source: str, amount: float, notes: str = ""):
        """Record revenue for this agent's stream."""
        if self.STREAM:
            self.db.record_revenue(self.STREAM, source, amount, notes)
            self.logger.info(f"Revenue recorded: ${amount:.2f} from {source}")

    def add_task(self, description: str, priority: int = 3,
                 requires_approval: bool = False) -> int:
        """Add a task to the queue for this agent."""
        return self.db.add_task(
            description=description,
            agent=self.AGENT_NAME,
            stream=self.STREAM,
            priority=priority,
            requires_approval=requires_approval,
        )

    def log_learning(self, prediction: str, outcome: str, lesson: str,
                     confidence: int = 50, tags: list = None):
        """Re
cord a learning in the Reflection Database."""
        self.db.add_learning(
            prediction=prediction,
            outcome=outcome,
            lesson=lesson,
            confidence=confidence,
            stream=self.STREAM,
            tags=tags or [self.AGENT_NAME],
        )

    def get_stream_config(self) -> dict:
        """Get this agent's stream configuration from config.yaml."""
        if self.STREAM:
            return self.config.get("streams", self.STREAM, default={})
        return {}

    def should_run_today(self) -> bool:
        """Check if this agent should run today based on schedule config."""
        stream_cfg = self.get_stream_config()
        if not stream_cfg.get("enabled", True):
            return False

        # Check stream status in DB
        if self.STREAM:
            stream = self.db.get_stream(self.STREAM)
            if stream and stream["status"] != "active":
                return False

        return True

    def get_lessons_for_stream(self, limit: int = 10) -> list:
        """Get recent lessons relevant to this agent's stream."""
        return self.db.get_lessons(stream=self.STREAM, limit=limit)