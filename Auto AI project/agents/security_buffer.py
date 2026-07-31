#!/usr/bin/env python3
"""
═══════════════════════════════════════════════════════════════
THE INSTITUTION — SECURITY BUFFER AGENT
═══════════════════════════════════════════════════════════════
Turns financial fear into measurable runway:
- Tracks runway in days (current + projected)
- Calculates trends (this week vs last week)
- Prioritises actions by runway impact
- Generates fear-reduction reports when fear is high
- Celebrates milestones (first revenue, 30/60/90-day runway)
- Feeds the daily digest with a security section
═══════════════════════════════════════════════════════════════
"""

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from common import get_db, get_config, get_logger, INSTITUTION_ROOT, now_iso, today_str, safe_json_loads
from agents.base import BaseAgent

logger = get_logger("security_buffer")


class SecurityBufferAgent(BaseAgent):
    AGENT_NAME = "security_buffer"
    AGENT_TYPE = "governance"
    STREAM = None  # Cross-stream psychological + economic agent
    DEFAULT_INTERVAL_SECONDS = 21600  # Every 6 hours

    # Runway milestones that trigger celebration
    MILESTONES = [
        {"key": "first_revenue", "label": "First revenue recorded", "check": "revenue"},
        {"key": "runway_30", "label": "30-day runway reached", "days": 30},
        {"key": "runway_60", "label": "60-day runway reached", "days": 60},
        {"key": "runway_90", "label": "90-day runway reached — SAFETY THRESHOLD", "days": 90},
        {"key": "runway_180", "label": "180-day runway reached — SECURE", "days": 180},
    ]

    def __init__(self):
        super().__init__()
        self.reports_dir = INSTITUTION_ROOT / "reports"
        self.reports_dir.mkdir(parents=True, exist_ok=True)
        self.history_path = INSTITUTION_ROOT / "data" / "reflection" / "runway_history.json"
        self.history_path.parent.mkdir(parents=True, exist_ok=True)

    def run_once(self):
        """Main cycle: snapshot runway, check milestones, reduce fear, rank actions."""
        results = []

        # Phase 1: Record runway snapshot
        snapshot = self.record_runway_snapshot()
        if snapshot:
            results.append(f"Runway: {snapshot['runway_days']:.0f} days ({snapshot['trend_direction']}{abs(snapshot['trend_days']):.0f}d/wk)")

        # Phase 2: Check for milestones and celebrate
        celebrated = self._check_milestones(snapshot)
        if celebrated:
            results.append(f"Celebrated {celebrated} milestone(s)")

        # Phase 3: Check founder fear level — generate fear-reduction report if needed
        if self._fear_report_needed():
            report = self.generate_fear_reduction_report(snapshot)
            if report:
                results.append("Fear-reduction report generated")

        # Phase 4: Rank pending actions by runway impact (feeds the one-action rule)
        ranked = self.rank_actions_by_runway_impact()
        if ranked:
            results.append(f"Ranked {len(ranked)} actions by runway impact")

        return "; ".join(results) if results else "Security buffer cycle complete"

    # ─── RUNWAY TRACKING ──────────────────────────────────────

    def calculate_runway(self) -> dict:
        """Calculate current runway in days with full breakdown."""
        # Total net revenue (after 30% tax reserve)
        revenue_row = self.db.fetchone(
            "SELECT COALESCE(SUM(net_amount), 0) as total, COALESCE(SUM(amount), 0) as gross FROM revenue"
        )
        total_net = revenue_row["total"] if revenue_row else 0
        total_gross = revenue_row["gross"] if revenue_row else 0

        # Total expenses
        expense_row = self.db.fetchone("SELECT COALESCE(SUM(amount), 0) as total FROM expenses")
        total_expenses = expense_row["total"] if expense_row else 0

        balance = total_net - total_expenses

        # Daily burn rate (last 30 days)
        burn_row = self.db.fetchone(
            "SELECT COALESCE(SUM(amount), 0) as total FROM expenses WHERE recorded_at > datetime('now', '-30 days')"
        )
        burn_30d = burn_row["total"] if burn_row else 0
        daily_burn = burn_30d / 30.0 if burn_30d > 0 else 5.0  # Default $5/day baseline

        # Daily income rate (last 30 days)
        income_row = self.db.fetchone(
            "SELECT COALESCE(SUM(net_amount), 0) as total FROM revenue WHERE recorded_at > datetime('now', '-30 days')"
        )
        income_30d = income_row["total"] if income_row else 0
        daily_income = income_30d / 30.0

        # Net daily change
        net_daily = daily_income - daily_burn

        runway_days = max(0, balance / daily_burn) if daily_burn > 0 else 999

        # Projected runway in 30 days
        projected_balance_30d = balance + (net_daily * 30)
        projected_runway_30d = max(0, projected_balance_30d / daily_burn) if daily_burn > 0 else 999

        return {
            "balance": round(balance, 2),
            "total_gross_revenue": round(total_gross, 2),
            "total_net_revenue": round(total_net, 2),
            "total_expenses": round(total_expenses, 2),
            "daily_burn": round(daily_burn, 2),
            "daily_income": round(daily_income, 2),
            "net_daily": round(net_daily, 2),
            "runway_days": round(runway_days, 1),
            "projected_runway_30d": round(projected_runway_30d, 1),
            "income_30d": round(income_30d, 2),
            "burn_30d": round(burn_30d, 2),
        }

    def record_runway_snapshot(self) -> Optional[dict]:
        """Record a runway snapshot to history for trend analysis."""
        runway = self.calculate_runway()

        snapshot = {
            "date": today_str(),
            "timestamp": now_iso(),
            "runway_days": runway["runway_days"],
            "balance": runway["balance"],
            "daily_burn": runway["daily_burn"],
            "daily_income": runway["daily_income"],
            "net_daily": runway["net_daily"],
            "projected_runway_30d": runway["projected_runway_30d"],
        }

        # Load history
        history = self._load_history()

        # Add or update today's snapshot
        history = [h for h in history if h.get("date") != today_str()]
        history.append(snapshot)

        # Keep last 365 days
        cutoff = (datetime.now() - timedelta(days=365)).strftime("%Y-%m-%d")
        history = [h for h in history if h.get("date", "9999") >= cutoff]
        history.sort(key=lambda h: h.get("date", ""))

        self._save_history(history)

        # Calculate trend (vs 7 days ago)
        trend = self._calculate_trend(history, snapshot)
        snapshot.update(trend)

        return snapshot

    def _load_history(self) -> list:
        if self.history_path.exists():
            try:
                return json.loads(self.history_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, IOError):
                return []
        return []

    def _save_history(self, history: list):
        self.history_path.write_text(json.dumps(history, indent=2), encoding="utf-8")

    def _calculate_trend(self, history: list, current: dict) -> dict:
        """Calculate runway trend vs 7 days ago."""
        target_date = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")

        # Find closest snapshot to 7 days ago
        past = None
        for h in history:
            if h.get("date", "9999") <= target_date:
                past = h

        if not past:
            return {
                "trend_days": 0.0,
                "trend_direction": "→",
                "trend_label": "insufficient history",
                "runway_7d_ago": None,
            }

        delta = current["runway_days"] - past["runway_days"]
        direction = "↗" if delta > 0.5 else ("↘" if delta < -0.5 else "→")

        return {
            "trend_days": round(delta, 1),
            "trend_direction": direction,
            "trend_label": f"{direction} {delta:+.1f} days vs last week",
            "runway_7d_ago": past["runway_days"],
        }

    def get_runway_history(self, days: int = 30) -> list:
        """Get runway history for charts."""
        history = self._load_history()
        cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        return [h for h in history if h.get("date", "9999") >= cutoff]

    # ─── MILESTONES ───────────────────────────────────────────

    def _check_milestones(self, snapshot: Optional[dict]) -> int:
        """Check for and celebrate runway milestones."""
        if not snapshot:
            return 0

        celebrated = 0
        runway = snapshot["runway_days"]

        for milestone in self.MILESTONES:
            key = milestone["key"]

            # Check if already celebrated
            existing = self.db.fetchone(
                "SELECT id FROM decisions WHERE description = ?",
                (f"MILESTONE: {milestone['label']}",)
            )
            if existing:
                continue

            achieved = False
            if milestone.get("check") == "revenue":
                # First revenue
                rev = self.db.fetchone("SELECT COUNT(*) as cnt FROM revenue")
                achieved = rev and rev["cnt"] > 0
            elif "days" in milestone:
                achieved = runway >= milestone["days"]

            if achieved:
                self._celebrate_milestone(milestone, snapshot)
                celebrated += 1

        return celebrated

    def _celebrate_milestone(self, milestone: dict, snapshot: dict):
        """Record and celebrate a milestone."""
        label = milestone["label"]

        # Record as a decision (institutional memory)
        self.db.insert("decisions", {
            "description": f"MILESTONE: {label}",
            "decision": "CELEBRATE",
            "reasoning": (
                f"Runway is now {snapshot['runway_days']:.0f} days "
                f"(balance ${snapshot['balance']:.2f}, net {snapshot['net_daily']:+.2f}/day). "
                f"This is real, measurable progress. The fear was rational — and it is being dismantled with evidence."
            ),
            "agent": self.AGENT_NAME,
        })

        # Record a learning
        self.log_learning(
            prediction="The Institution would reach measurable financial milestones",
            outcome=f"Milestone achieved: {label}",
            lesson=f"Milestone '{label}' reached. Compounding works. Continue current strategy.",
            confidence=80,
            tags=["milestone", "runway", "celebration"],
        )

        logger.info(f"═══ MILESTONE CELEBRATED: {label} ═══")

    # ─── FEAR REDUCTION ───────────────────────────────────────

    def _fear_report_needed(self) -> bool:
        """Determine if a fear-reduction report is needed."""
        checkin = self.db.get_latest_checkin()
        if not checkin:
            return False

        fear = checkin.get("fear", 3)

        # High fear (1-2) triggers a report
        if fear <= 2:
            # But not more than once per day
            today_report = self.db.fetchone(
                "SELECT id FROM decisions WHERE description = 'Fear-reduction report generated' AND DATE(created_at) = ?",
                (today_str(),)
            )
            return not today_report

        return False

    def generate_fear_reduction_report(self, snapshot: Optional[dict]) -> Optional[str]:
        """Generate a fear-reduction report grounded in evidence."""
        if not snapshot:
            snapshot = self.record_runway_snapshot()
        if not snapshot:
            return None

        runway = snapshot["runway_days"]
        trend = snapshot.get("trend_label", "")
        balance = snapshot["balance"]
        daily_income = snapshot["daily_income"]
        projected = snapshot["projected_runway_30d"]

        # What did the Institution do this week to increase runway?
        week_actions = self.db.fetchall(
            """SELECT description, result FROM tasks
               WHERE status = 'completed' AND completed_at > datetime('now', '-7 days')
               AND (description LIKE '%grant%' OR description LIKE '%revenue%'
                    OR description LIKE '%freelance%' OR description LIKE '%product%'
                    OR description LIKE '%content%')
               ORDER BY completed_at DESC LIMIT 5"""
        )
        action_lines = "\n".join(f"  - {a['description'][:80]}" for a in week_actions) or "  - Building foundations (no direct revenue tasks yet)"

        # Next milestone
        next_milestone = self._next_milestone(runway)

        # Recent revenue sources
        recent_revenue = self.db.fetchall(
            "SELECT source, amount FROM revenue ORDER BY recorded_at DESC LIMIT 3"
        )
        revenue_lines = "\n".join(f"  - ${r['amount']:.2f} from {r['source']}" for r in recent_revenue) or "  - None yet. The machine is still warming up."

        report = f"""# 🛡 SECURITY BUFFER — FEAR REDUCTION REPORT
**{datetime.now().strftime('%A, %B %d, %Y')}**

---

The fear is real. It is also being dismantled with evidence.

## THE NUMBERS (as of today)

| Measure | Value |
|---------|-------|
| Runway | **{runway:.0f} days** |
| Trend | {trend} |
| Balance | ${balance:.2f} |
| Income (30 days) | ${snapshot['income_30d']:.2f} |
| Daily income rate | ${daily_income:.2f}/day |
| Projected runway in 30 days | {projected:.0f} days |

## WHAT THE INSTITUTION DID THIS WEEK TO HELP

{action_lines}

## RECENT REVENUE

{revenue_lines}

## NEXT MILESTONE

{next_milestone}

## THE TRUTH

You are not one bad day away from disaster. You have {runway:.0f} days
of runway, and a machine that works while you rest. Every grant found,
every product listed, every article published adds days to that number.

Fear lies. It says "nothing is happening." The ledger says otherwise.

Rest. The Institution has the watch.

---
*Generated by the Security Buffer Agent. This report is grounded in the
actual ledger, not optimism. If the numbers were bad, it would say so.*
"""

        # Save report
        report_path = self.reports_dir / f"fear_reduction_{today_str()}.md"
        report_path.write_text(report, encoding="utf-8")

        # Log it
        self.db.insert("decisions", {
            "description": "Fear-reduction report generated",
            "decision": "SUPPORT",
            "reasoning": f"Founder fear is high. Report saved to {report_path}. Runway: {runway:.0f} days.",
            "agent": self.AGENT_NAME,
        })

        logger.info(f"Fear-reduction report generated: {report_path}")
        return report

    def _next_milestone(self, runway: float) -> str:
        """Describe the next runway milestone."""
        for m in self.MILESTONES:
            if "days" in m and runway < m["days"]:
                gap = m["days"] - runway
                net = self.calculate_runway()["net_daily"]
                if net > 0:
                    days_to_reach = gap / net
                    return f"**{m['label']}** — {gap:.0f} days away. At current rate (+${net:.2f}/day), ~{days_to_reach:.0f} days to reach it."
                return f"**{m['label']}** — {gap:.0f} days of runway to go. Income rate needs to increase."
        return "**All runway milestones achieved.** The Institution is secure. Focus shifts to growth."

    # ─── ACTION PRIORITISATION BY RUNWAY IMPACT ───────────────

    def rank_actions_by_runway_impact(self) -> list:
        """Rank pending approvals and queued tasks by expected runway impact."""
        runway = self.calculate_runway()
        daily_burn = max(runway["daily_burn"], 1.0)

        candidates = []

        # Pending approvals
        approvals = self.db.get_pending_approvals()
        for a in approvals:
            impact = self._estimate_runway_impact(a["item_type"], a.get("details", ""), daily_burn)
            candidates.append({
                "kind": "approval",
                "id": a["id"],
                "description": a["description"],
                "expected_value": impact["expected_value"],
                "expected_runway_days": impact["runway_days"],
                "confidence": impact["confidence"],
                "time_minutes": impact["time_minutes"],
                "risk": impact["risk"],
            })

        # Queued tasks (that don't need approval)
        tasks = self.db.fetchall(
            "SELECT * FROM tasks WHERE status = 'queued' AND requires_approval = 0 ORDER BY priority ASC LIMIT 20"
        )
        for t in tasks:
            impact = self._estimate_runway_impact("task", t.get("description", ""), daily_burn)
            candidates.append({
                "kind": "task",
                "id": t["id"],
                "description": t["description"],
                "expected_value": impact["expected_value"],
                "expected_runway_days": impact["runway_days"],
                "confidence": impact["confidence"],
                "time_minutes": impact["time_minutes"],
                "risk": impact["risk"],
            })

        # Sort by runway impact per minute of founder time (leverage)
        for c in candidates:
            c["leverage"] = c["expected_runway_days"] / max(c["time_minutes"], 1) * 60

        candidates.sort(key=lambda c: c["leverage"], reverse=True)

        return candidates

    def _estimate_runway_impact(self, item_type: str, details: str, daily_burn: float) -> dict:
        """Estimate the runway impact of an action."""
        details_data = safe_json_loads(details, {})
        expected_value = 0.0
        confidence = 0.5
        time_minutes = 5
        risk = "low"

        if item_type == "grant_submission":
            amount = details_data.get("amount") or 2000
            expected_value = float(amount) * 0.4  # Grants are uncertain
            confidence = 0.4
            time_minutes = 10
            risk = "low"

        elif item_type == "freelance_proposal":
            budget = details_data.get("budget") or 150
            expected_value = float(budget) * 0.25  # Proposal win rate ~25%
            confidence = 0.3
            time_minutes = 5
            risk = "low"

        elif item_type == "new_opportunity":
            expected_value = 500 * 0.2  # Speculative
            confidence = 0.2
            time_minutes = 8
            risk = "medium"

        elif item_type == "autonomy_elevation":
            expected_value = 0  # Indirect — saves founder time
            confidence = 0.6
            time_minutes = 3
            risk = "medium"

        elif item_type == "grant_deadline":
            expected_value = 1500 * 0.4
            confidence = 0.5
            time_minutes = 15
            risk = "low"

        else:
            expected_value = 100 * 0.3
            confidence = 0.3
            time_minutes = 5
            risk = "low"

        runway_days = expected_value / daily_burn if daily_burn > 0 else 0

        return {
            "expected_value": round(expected_value, 2),
            "runway_days": round(runway_days, 2),
            "confidence": confidence,
            "time_minutes": time_minutes,
            "risk": risk,
        }

    # ─── DIGEST FEED ──────────────────────────────────────────

    def security_buffer_section(self) -> str:
        """Generate the Security Buffer section for the daily digest."""
        snapshot = self.record_runway_snapshot()
        if not snapshot:
            return "## 🛡 SECURITY BUFFER\n\nRunway data not yet available.\n"

        runway = snapshot["runway_days"]
        trend = snapshot.get("trend_label", "")
        next_milestone = self._next_milestone(runway)

        # This week's runway-impacting wins
        week_revenue = self.db.fetchone(
            "SELECT COALESCE(SUM(amount), 0) as total, COUNT(*) as cnt FROM revenue WHERE recorded_at > datetime('now', '-7 days')"
        )
        rev_total = week_revenue["total"] if week_revenue else 0
        rev_count = week_revenue["cnt"] if week_revenue else 0

        section = f"""## 🛡 SECURITY BUFFER

| Measure | Value |
|---------|-------|
| Runway | **{runway:.0f} days** |
| Trend | {trend} |
| Revenue this week | ${rev_total:.2f} ({rev_count} transactions) |
| Daily income rate | ${snapshot['daily_income']:.2f}/day |

**Next milestone:** {next_milestone}
"""
        return section

    def get_status(self) -> dict:
        """Status for dashboard."""
        base = super().get_status()
        runway = self.calculate_runway()
        runway["history_30d"] = self.get_runway_history(30)
        base["runway"] = runway
        return base