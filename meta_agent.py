#!/usr/bin/env python3
"""
═══════════════════════════════════════════════════════════════
THE INSTITUTION — META-AGENT (THE BRAIN)
═══════════════════════════════════════════════════════════════
The meta-agent is the central coordinator. It:
- Spawns and manages all child agents
- Runs the self-improvement loop
- Allocates resources to highest-performing streams
- Generates strategic reports and daily digests
- Manages autonomy levels per stream
- Runs health checks and handles failover
- Queries the Reflection Database before decisions
- Kills underperforming streams
- Spawns new agents for validated opportunities
═══════════════════════════════════════════════════════════════
"""

import os
import sys
import json
import time
import signal
import threading
import traceback
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from common import (
    get_db, get_config, get_logger, now_iso, today_str,
    INSTITUTION_ROOT, safe_json_loads
)
from providers import get_ai_provider
from constitutional_court import get_court, AuditResult

logger = get_logger("meta_agent")


class MetaAgent:
    """
    The brain of The Institution. Coordinates all agents,
    runs the self-improvement loop, and maintains organisational coherence.
    """

    def __init__(self):
        self.db = get_db()
        self.config = get_config()
        self.ai = get_ai_provider()
        self.court = get_court()
        self._running = False
        self._threads = {}
        self._agent_instances = {}
        self._loop_interval = self.config.get(
            "meta_agent", "loop_interval_seconds", default=300
        )
        self._health_interval = self.config.get(
            "meta_agent", "health_check_interval_seconds", default=60
        )
        self._shutdown_event = threading.Event()

        # Register signal handlers for graceful shutdown
        signal.signal(signal.SIGTERM, self._handle_shutdown)
        signal.signal(signal.SIGINT, se
lf._handle_shutdown)

    def _handle_shutdown(self, signum, frame):
        """Graceful shutdown handler."""
        logger.info(f"Received signal {signum}. Initiating graceful shutdown...")
        self._running = False
        self._shutdown_event.set()

    # ═══════════════════════════════════════════════════════════
    # MAIN EXECUTION LOOP
    # ═══════════════════════════════════════════════════════════

    def run_forever(self):
        """Main entry point. Runs until shutdown signal."""
        logger.info("═══ THE INSTITUTION META-AGENT STARTING ═══")
        self._running = True
        self._initialize_agents()
        self._log_startup()

        cycle = 0
        while self._running:
            cycle += 1
            cycle_start = time.time()

            try:
                # ─── EVERY CYCLE (5 min default) ──────────────
                self._process_task_queue()
                self._check_agent_heartbeats()
                self._check_stream_health()

                # ─── EVERY 12 CYCLES (~1 hour) ────────────────
                if cycle % 12 == 0:
                    self._run_self_improvement_loop()
                    self._check_kill_criteria()
                    self._check_approval_queue()

                # ─── EVERY 288 CYCLES (~24 hours) ─────────────
                if cycle % 288 == 0:
                    self._generate_digest()
                    self._rebalance_resources()
                    self._check_autonomy_elevations()

                # ─── EVERY 2016 CYCLES (~7 days) ──────────────
                if cycle % 2016 == 0:
                    self._generate_weekly_report()
                    self._strategic_review()

            except Exception as e:
                logger.error(f"Meta-agent cycle error: {e}\n{traceback.format_exc()}")
                self.db.log_incident("warning", "meta_agent", f"Cycle error: {str(e)[:500]}")

            # Sleep for remainder of interval
            elapsed = time.time() - cycle_star
t
            sleep_time = max(0, self._loop_interval - elapsed)
            if sleep_time > 0:
                self._shutdown_event.wait(timeout=sleep_time)

        self._shutdown()

    def _initialize_agents(self):
        """Register and start all configured agents."""
        logger.info("Initializing agent workforce...")

        agent_registry = {
            "content_site": ("agents.content_site", "ContentSiteAgent", "content_sites"),
            "product_engine": ("agents.product_engine", "ProductEngineAgent", "digital_products"),
            "video_engine": ("agents.video_engine", "VideoEngineAgent", "video_content"),
            "newsletter": ("agents.newsletter", "NewsletterAgent", "newsletter"),
            "grant_pipeline": ("agents.grant_pipeline", "GrantPipelineAgent", "grants"),
            "freelance_pipeline": ("agents.freelance_pipeline", "FreelancePipelineAgent", "freelance"),
            "print_on_demand": ("agents.print_on_demand", "PrintOnDemandAgent", "print_on_demand"),
            "affiliate_sites": ("agents.affiliate_sites", "AffiliateSitesAgent", "affiliate_sites"),
            "social_media": ("agents.social_media", "SocialMediaAgent", "social_media"),
            "micro_saas": ("agents.micro_saas", "MicroSaasAgent", "micro_saas"),
            "niche_scout": ("agents.niche_scout", "NicheScoutAgent", None),
            "oracle": ("agents.oracle", "OracleAgent", None),
            "safety_officer": ("agents.safety_officer", "SafetyOfficer", None),
            "security_buffer": ("agents.security_buffer", "SecurityBufferAgent", None),
        }

        for agent_name, (module_path, class_name, stream) in agent_registry.items():
            try:
                # Check if stream is enabled
                if stream:
                    stream_cfg = self.db.get_stream(stream)
                    if stream_cfg and stream_cfg["status"] != "active":
                        logger.info(f"Skipping {agent_name}: stream '{stream}' is {stream_cfg[
'status']}")
                        continue

                # Dynamic import
                module = __import__(module_path, fromlist=[class_name])
                agent_class = getattr(module, class_name)
                instance = agent_class()

                self._agent_instances[agent_name] = instance
                self.db.register_agent(agent_name, class_name, stream)
                logger.info(f"Registered agent: {agent_name} → {class_name}")

            except ImportError as e:
                logger.warning(f"Agent module not available: {agent_name} ({e})")
            except Exception as e:
                logger.error(f"Failed to initialize agent {agent_name}: {e}")

        logger.info(f"Initialized {len(self._agent_instances)} agents")

    def _log_startup(self):
        """Log startup event to institutional memory."""
        self.db.log_incident("info", "meta_agent", "Meta-agent started. Institution is live.")

    # ═══════════════════════════════════════════════════════════
    # TASK QUEUE PROCESSING
    # ═══════════════════════════════════════════════════════════

    def _process_task_queue(self):
        """Process queued tasks, dispatching to appropriate agents."""
        tasks = self.db.fetchall(
            "SELECT * FROM tasks WHERE status = 'queued' ORDER BY priority ASC, created_at ASC LIMIT 10"
        )

        for task in tasks:
            if not self._running:
                break

            # Check if task requires approval
            if task["requires_approval"] and not task["approved"]:
                # Create approval request if not already created
                existing = self.db.fetchone(
                    "SELECT id FROM approvals WHERE item_type = 'task' AND description LIKE ? AND status = 'pending'",
                    (f"%{task['description'][:50]}%",)
                )
                if not existing:
                    self.court.create_approval_request(AuditResult(
                        action=ta
sk["description"],
                        agent=task["agent"],
                        stream=task["stream"] or "",
                        overall_result="REVIEW_REQUIRED",
                        requires_approval=True,
                        reasoning="Task requires founder approval before execution.",
                    ))
                continue

            # Constitutional audit
            audit = self.court.audit(
                action=task["description"],
                agent=task["agent"],
                stream=task["stream"],
            )

            if audit.overall_result == "FAIL":
                self.db.fail_task(task["id"], f"Blocked by Constitutional Court: {audit.reasoning[:200]}")
                continue

            if audit.overall_result == "REVIEW_REQUIRED" and audit.requires_approval:
                self.court.create_approval_request(audit)
                continue

            # Dispatch to agent
            self._dispatch_task(task)

    def _dispatch_task(self, task: dict):
        """Dispatch a task to the appropriate agent."""
        agent_name = task["agent"]
        instance = self._agent_instances.get(agent_name)

        if not instance:
            # Try steward as fallback
            logger.warning(f"No agent '{agent_name}' available for task {task['id']}. Queuing.")
            return

        # Mark task in progress
        self.db.update("tasks", {
            "status": "in_progress",
            "started_at": now_iso(),
            "updated_at": now_iso(),
        }, "id = ?", (task["id"],))

        self.db.agent_heartbeat(agent_name, task["description"][:100])

        try:
            # Execute task via agent
            if hasattr(instance, "execute_task"):
                result = instance.execute_task(task)
            elif hasattr(instance, "run_once"):
                result = instance.run_once()
            else:
                result = "No execution method available"

            self.db.complete_task(
task["id"], str(result)[:500] if result else "Completed")
            self.db.agent_completed_task(agent_name, task["description"][:100], "Task executed successfully")

        except Exception as e:
            error_msg = f"{type(e).__name__}: {str(e)[:300]}"
            self.db.fail_task(task["id"], error_msg)
            self.db.agent_failed_task(agent_name, error_msg)
            logger.error(f"Task {task['id']} failed in {agent_name}: {error_msg}")

    # ═══════════════════════════════════════════════════════════
    # AGENT HEALTH MONITORING
    # ═══════════════════════════════════════════════════════════

    def _check_agent_heartbeats(self):
        """Check if agents are responsive. Restart if stale."""
        agents = self.db.get_all_agents()
        now = datetime.now()
        stale_threshold = timedelta(minutes=15)

        for agent in agents:
            if agent["status"] in ("killed", "paused"):
                continue

            last_hb = agent.get("last_heartbeat")
            if last_hb:
                try:
                    hb_time = datetime.fromisoformat(last_hb)
                    if now - hb_time > stale_threshold:
                        logger.warning(f"Agent {agent['name']} heartbeat stale ({last_hb}). Attempting restart.")
                        self._restart_agent(agent["name"])
                except (ValueError, TypeError):
                    pass

    def _restart_agent(self, agent_name: str):
        """Restart a failed agent."""
        agent_info = self.db.get_agent_status(agent_name)
        if not agent_info:
            return

        restart_count = agent_info.get("restart_count", 0) + 1
        max_restarts = self.config.get("safety", "auto_restart", "max_restarts_per_hour", default=3)

        if restart_count > max_restarts:
            logger.error(f"Agent {agent_name} exceeded max restarts ({max_restarts}). Marking as error.")
            self.db.update("agents", {"status": "error"}, "name = ?", (agent_name
,))
            self.db.log_incident("critical", agent_name, f"Exceeded {max_restarts} restarts. Manual intervention needed.")
            return

        self.db.update("agents", {
            "status": "idle",
            "restart_count": restart_count,
            "last_heartbeat": now_iso(),
            "updated_at": now_iso(),
        }, "name = ?", (agent_name,))

        logger.info(f"Agent {agent_name} restarted (attempt {restart_count})")

    # ═══════════════════════════════════════════════════════════
    # STREAM HEALTH & KILL CRITERIA
    # ═══════════════════════════════════════════════════════════

    def _check_stream_health(self):
        """Monitor all active streams for health issues."""
        streams = self.db.get_active_streams()
        for stream in streams:
            # Check if stream has any active agents
            agents = self.db.fetchall(
                "SELECT COUNT(*) as cnt FROM agents WHERE stream = ? AND status IN ('idle', 'running')",
                (stream["slug"],)
            )
            if agents and agents[0]["cnt"] == 0:
                logger.warning(f"Stream '{stream['slug']}' has no active agents.")

    def _check_kill_criteria(self):
        """Evaluate kill criteria for all active streams."""
        streams = self.db.get_active_streams()

        for stream in streams:
            if not stream.get("kill_criterion"):
                continue

            should_kill = self._evaluate_kill_criterion(stream)
            if should_kill:
                logger.warning(
                    f"KILL CRITERION MET for stream '{stream['slug']}': {stream['kill_criterion']}"
                )
                self._kill_stream(stream)

    def _evaluate_kill_criterion(self, stream: dict) -> bool:
        """Evaluate whether a stream's kill criterion has been met."""
        slug = stream["slug"]
        window_days = stream.get("kill_window_days", 30)
        metric = stream.get("kill_metric", "")
        threshold = stre
am.get("kill_threshold", 0)

        if not metric:
            return False

        # Check revenue-based kill criteria
        if "revenue" in metric or "sales" in metric:
            revenue = self.db.fetchone(
                "SELECT COALESCE(SUM(amount), 0) as total FROM revenue WHERE stream = ? AND recorded_at > datetime('now', ?)",
                (slug, f"-{window_days} days")
            )
            if revenue and revenue["total"] <= threshold:
                # Check if stream has been active long enough
                created = stream.get("created_at", "")
                if created:
                    try:
                        created_dt = datetime.fromisoformat(created)
                        if (datetime.now() - created_dt).days >= window_days:
                            return True
                    except (ValueError, TypeError):
                        pass

        # Check task-based metrics
        if "response_rate" in metric:
            completed = self.db.fetchone(
                "SELECT COUNT(*) as cnt FROM tasks WHERE stream = ? AND status = 'completed' AND created_at > datetime('now', ?)",
                (slug, f"-{window_days} days")
            )
            failed = self.db.fetchone(
                "SELECT COUNT(*) as cnt FROM tasks WHERE stream = ? AND status = 'failed' AND created_at > datetime('now', ?)",
                (slug, f"-{window_days} days")
            )
            total = (completed["cnt"] if completed else 0) + (failed["cnt"] if failed else 0)
            if total > 10:  # Minimum sample size
                success_rate = (completed["cnt"] / total) * 100 if completed else 0
                if success_rate < threshold:
                    return True

        return False

    def _kill_stream(self, stream: dict):
        """Kill an underperforming stream."""
        slug = stream["slug"]

        # Constitutional audit for killing
        audit = self.court.audit(
            action=f"Kill revenue stream 
'{slug}' due to: {stream['kill_criterion']}",
            agent="meta_agent",
            stream=slug,
        )

        if audit.overall_result == "FAIL":
            logger.warning(f"Constitutional Court blocked killing stream '{slug}': {audit.reasoning}")
            return

        # Pause the stream (not delete — preserve data)
        self.db.update_stream_status(slug, "killed")

        # Pause associated agents
        self.db.execute(
            "UPDATE agents SET status = 'paused' WHERE stream = ?", (slug,)
        )

        # Log the decision
        self.db.insert("decisions", {
            "description": f"Killed stream '{slug}'",
            "decision": "KILL",
            "reasoning": f"Kill criterion met: {stream['kill_criterion']}. Threshold: {stream.get('kill_threshold')}. Window: {stream.get('kill_window_days')} days.",
            "agent": "meta_agent",
            "stream": slug,
        })

        # Record learning
        self.db.add_learning(
            prediction=f"Stream '{slug}' would generate revenue within {stream.get('kill_window_days', 30)} days",
            outcome=f"Stream killed after {stream.get('kill_window_days', 30)} days. Criterion: {stream['kill_criterion']}",
            lesson=f"Stream type '{slug}' with current strategy did not meet targets. Review approach before retrying.",
            confidence=70,
            stream=slug,
            tags=["kill", "stream_failure", slug],
        )

        logger.info(f"Stream '{slug}' killed and agents paused.")

    # ═══════════════════════════════════════════════════════════
    # SELF-IMPROVEMENT LOOP
    # ═══════════════════════════════════════════════════════════

    def _run_self_improvement_loop(self):
        """
        The machine that makes machines.
        1. Query Reflection Database for lessons
        2. Compare predictions with outcomes
        3. Adjust agent configurations
        4. Identify patterns to replicate
        """
        logger.info("Running s
elf-improvement loop...")

        # Get recent lessons
        lessons = self.db.get_lessons(limit=10)
        if not lessons:
            return

        # Check for prediction calibration
        predictions = self.db.fetchall(
            "SELECT * FROM predictions WHERE actual_value IS NULL AND created_at < datetime('now', '-7 days') LIMIT 5"
        )

        for pred in predictions:
            # Try to measure actual outcome
            actual = self._measure_prediction_outcome(pred)
            if actual is not None:
                self.db.record_prediction_outcome(pred["id"], str(actual))

                # Calculate error and log learning
                try:
                    predicted_val = float(pred["predicted_value"])
                    actual_val = float(actual)
                    error = abs(predicted_val - actual_val) / max(abs(actual_val), 0.001) * 100

                    if error > 30:  # Significant miss
                        self.db.add_learning(
                            prediction=pred["description"],
                            outcome=f"Predicted: {pred['predicted_value']}, Actual: {actual}",
                            lesson=f"Prediction error {error:.0f}%. Review assumptions for {pred.get('stream', 'general')} predictions.",
                            confidence=pred.get("predicted_confidence", 50),
                            stream=pred.get("stream"),
                            tags=["prediction_error", "calibration"],
                        )
                except (ValueError, TypeError):
                    pass

        # Analyze lessons for actionable adjustments
        if lessons:
            self._apply_lessons(lessons)

    def _measure_prediction_outcome(self, prediction: dict) -> Optional[float]:
        """Attempt to measure the actual outcome of a prediction."""
        stream = prediction.get("stream")
        desc_lower = prediction.get("description", "").lower()

        if "revenue" in desc_lower and str
eam:
            row = self.db.fetchone(
                "SELECT COALESCE(SUM(amount), 0) as total FROM revenue WHERE stream = ? AND recorded_at > datetime('now', '-30 days')",
                (stream,)
            )
            return row["total"] if row else 0

        if "traffic" in desc_lower or "sessions" in desc_lower:
            # Would query analytics API — return None if unavailable
            return None

        if "task" in desc_lower and "completion" in desc_lower:
            row = self.db.fetchone(
                "SELECT COUNT(*) as cnt FROM tasks WHERE status = 'completed' AND created_at > datetime('now', '-7 days')"
            )
            return row["cnt"] if row else 0

        return None

    def _apply_lessons(self, lessons: list):
        """Apply learned lessons to adjust system behavior."""
        for lesson in lessons:
            lesson_text = lesson.get("lesson", "").lower()
            stream = lesson.get("stream")

            # If lessons indicate over-optimism, reduce autonomy
            if "optimistic" in lesson_text or "underestimate" in lesson_text:
                if stream:
                    current = self.db.get_stream(stream)
                    if current and current.get("autonomy_level", 1) > 2:
                        new_level = current["autonomy_level"] - 1
                        self.db.update("streams", {
                            "autonomy_level": new_level,
                            "updated_at": now_iso(),
                        }, "slug = ?", (stream,))
                        logger.info(f"Reduced autonomy for '{stream}' to L{new_level} based on lesson.")

            # If lessons indicate a strategy works, note for replication
            if "success" in lesson_text or "exceeded" in lesson_text or "better than" in lesson_text:
                self.db.insert("decisions", {
                    "description": f"Successful pattern identified in {stream}",
                    "decision": "REPLICATE",
   
                 "reasoning": lesson_text[:500],
                    "agent": "meta_agent",
                    "stream": stream,
                })

    # ═══════════════════════════════════════════════════════════
    # RESOURCE ALLOCATION
    # ═══════════════════════════════════════════════════════════

    def _rebalance_resources(self):
        """Allocate compute/API budget to highest-performing streams."""
        streams = self.db.get_active_streams()
        if not streams:
            return

        strategy = self.config.get("meta_agent", "resource_allocation", "strategy", default="performance_weighted")

        if strategy == "performance_weighted":
            # Score each stream by recent revenue per unit of compute
            scores = {}
            for stream in streams:
                revenue = self.db.fetchone(
                    "SELECT COALESCE(SUM(amount), 0) as total FROM revenue WHERE stream = ? AND recorded_at > datetime('now', '-30 days')",
                    (stream["slug"],)
                )
                ai_cost = self.db.fetchone(
                    "SELECT COUNT(*) as cnt FROM ai_usage WHERE stream = ? AND created_at > datetime('now', '-30 days')",
                    (stream["slug"],)
                )
                rev = revenue["total"] if revenue else 0
                cost = max(ai_cost["cnt"] if ai_cost else 1, 1)
                scores[stream["slug"]] = rev / cost  # Revenue per AI call

            # Log allocation decision
            if scores:
                sorted_streams = sorted(scores.items(), key=lambda x: x[1], reverse=True)
                self.db.insert("decisions", {
                    "description": "Resource rebalancing",
                    "decision": json.dumps(sorted_streams[:5]),
                    "reasoning": f"Performance-weighted allocation. Top: {sorted_streams[0][0] if sorted_streams else 'none'}",
                    "agent": "meta_agent",
                })

    # ══════════════════════
═════════════════════════════════════
    # AUTONOMY MANAGEMENT
    # ═══════════════════════════════════════════════════════════

    def _check_autonomy_elevations(self):
        """Check if any streams are ready for increased autonomy."""
        streams = self.db.get_active_streams()

        for stream in streams:
            current_level = stream.get("autonomy_level", 1)
            if current_level >= 4:  # Max safe level
                continue

            # Check reliability metrics
            tasks_completed = self.db.fetchone(
                "SELECT COUNT(*) as cnt FROM tasks WHERE stream = ? AND status = 'completed' AND created_at > datetime('now', '-30 days')",
                (stream["slug"],)
            )
            tasks_failed = self.db.fetchone(
                "SELECT COUNT(*) as cnt FROM tasks WHERE stream = ? AND status = 'failed' AND created_at > datetime('now', '-30 days')",
                (stream["slug"],)
            )

            completed = tasks_completed["cnt"] if tasks_completed else 0
            failed = tasks_failed["cnt"] if tasks_failed else 0
            total = completed + failed

            if total < 20:  # Not enough data
                continue

            success_rate = completed / total
            if success_rate > 0.95 and completed > 50:
                # Eligible for elevation — but requires founder approval
                new_level = current_level + 1
                self.db.add_approval(
                    item_type="autonomy_elevation",
                    description=f"Elevate '{stream['slug']}' from L{current_level} to L{new_level}",
                    details=json.dumps({
                        "stream": stream["slug"],
                        "current_level": current_level,
                        "proposed_level": new_level,
                        "success_rate": round(success_rate * 100, 1),
                        "tasks_completed": completed,
                        "tasks_failed": failed,
  
                  }),
                    stream=stream["slug"],
                    agent="meta_agent",
                    priority=3,
                )
                logger.info(f"Proposed autonomy elevation for '{stream['slug']}': L{current_level} → L{new_level}")

    # ═══════════════════════════════════════════════════════════
    # APPROVAL QUEUE MANAGEMENT
    # ═══════════════════════════════════════════════════════════

    def _check_approval_queue(self):
        """Process approved items and expire old ones."""
        # Process approved items
        approved = self.db.fetchall(
            "SELECT * FROM approvals WHERE status = 'approved' AND resolved_at > datetime('now', '-1 hour')"
        )
        for item in approved:
            self._execute_approved_item(item)

        # Expire old pending approvals (7 days)
        self.db.execute(
            "UPDATE approvals SET status = 'expired' WHERE status = 'pending' AND created_at < datetime('now', '-7 days')"
        )

    def _execute_approved_item(self, item: dict):
        """Execute an item that has been approved by the founder."""
        item_type = item.get("item_type", "")

        if item_type == "task":
            # Find and approve the associated task
            details = safe_json_loads(item.get("details", "{}"))
            if details and "action" in details:
                task = self.db.fetchone(
                    "SELECT id FROM tasks WHERE description LIKE ? AND status = 'queued'",
                    (f"%{details['action'][:50]}%",)
                )
                if task:
                    self.db.update("tasks", {"approved": 1}, "id = ?", (task["id"],))

        elif item_type == "autonomy_elevation":
            details = safe_json_loads(item.get("details", "{}"))
            if details:
                self.db.update("streams", {
                    "autonomy_level": details.get("proposed_level", 1),
                    "updated_at": now_iso(),
                }, "
slug = ?", (details.get("stream"),))
                logger.info(f"Autonomy elevated for '{details.get('stream')}' to L{details.get('proposed_level')}")

    # ═══════════════════════════════════════════════════════════
    # DIGEST & REPORTING
    # ═══════════════════════════════════════════════════════════

    def generate_digest(self):
        """Public method for cron-triggered digest generation."""
        self._generate_digest()

    def _generate_digest(self):
        """Generate the daily digest adapted to founder's energy."""
        logger.info("Generating daily digest...")

        # Get founder's latest check-in
        checkin = self.db.get_latest_checkin()
        energy = checkin["energy"] if checkin else 3
        pain = checkin["pain"] if checkin else 3
        fear = checkin["fear"] if checkin else 3

        # Get system state
        runway = self.db.get_runway_days()
        streams = self.db.get_active_streams()
        agents = self.db.get_all_agents()
        pending_approvals = self.db.get_pending_approvals()
        metrics = self.db.get_latest_metrics()

        # Get recent activity
        completed_today = self.db.fetchone(
            "SELECT COUNT(*) as cnt FROM tasks WHERE status = 'completed' AND completed_at > datetime('now', '-24 hours')"
        )
        failed_today = self.db.fetchone(
            "SELECT COUNT(*) as cnt FROM tasks WHERE status = 'failed' AND updated_at > datetime('now', '-24 hours')"
        )

        # Get recent lessons
        lessons = self.db.get_lessons(limit=3)

        # Get constitutional audit stats
        audit_stats = self.court.get_violation_stats()

        # Determine today's single action based on energy
        single_action = self._determine_single_action(energy, pain, pending_approvals)

        # Build digest
        digest = self._format_digest(
            energy=energy, pain=pain, fear=fear,
            runway=runway, streams=streams, agents=agents,
            completed=completed_to
day["cnt"] if completed_today else 0,
            failed=failed_today["cnt"] if failed_today else 0,
            pending_approvals=pending_approvals,
            lessons=lessons,
            audit_stats=audit_stats,
            metrics=metrics,
            single_action=single_action,
        )

        # Save digest
        reports_dir = INSTITUTION_ROOT / "reports" / "daily"
        reports_dir.mkdir(parents=True, exist_ok=True)
        digest_path = reports_dir / f"digest_{today_str()}.md"
        digest_path.write_text(digest, encoding="utf-8")

        logger.info(f"Digest saved to {digest_path}")
        return digest

    def _determine_single_action(self, energy: int, pain: int,
                                  pending_approvals: list) -> str:
        """Determine the ONE action to present based on founder's state."""
        if energy <= 1 or pain <= 1:
            return "Rest. The Institution is running. No action needed today. We've got this."

        if energy == 2:
            if pending_approvals:
                item = pending_approvals[0]
                return f"One tiny approval: {item['description'][:80]} (2 minutes max)"
            return "No action needed. System is running smoothly."

        if energy == 3:
            if pending_approvals:
                item = pending_approvals[0]
                return f"Review and approve: {item['description'][:100]} (5-10 minutes)"
            return "Optional: Check the dashboard for an overview of progress."

        # Energy 4-5
        if pending_approvals:
            high_priority = [a for a in pending_approvals if a.get("priority", 3) <= 2]
            if high_priority:
                return f"Strategic review: {high_priority[0]['description'][:100]}"
            return f"Review {len(pending_approvals)} pending approvals in dashboard."

        return "System autonomous. Optional: Review weekly report for strategic insights."

    def _format_digest(self, **kwargs) -> str:
        """Format t
he digest markdown."""
        energy = kwargs["energy"]
        pain = kwargs["pain"]
        fear = kwargs["fear"]
        runway = kwargs["runway"]
        streams = kwargs["streams"]
        agents = kwargs["agents"]
        completed = kwargs["completed"]
        failed = kwargs["failed"]
        pending = kwargs["pending_approvals"]
        lessons = kwargs["lessons"]
        audit_stats = kwargs["audit_stats"]
        metrics = kwargs["metrics"]
        single_action = kwargs["single_action"]

        # Energy-adaptive greeting
        if energy <= 1:
            greeting = "Rest today. The Institution continues without you. No guilt."
        elif energy <= 2:
            greeting = "Gentle day. One small thing if you feel up to it."


... [Content truncated]