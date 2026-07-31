#!/usr/bin/env python3
"""
═══════════════════════════════════════════════════════════════
THE INSTITUTION — ORACLE AGENT
═══════════════════════════════════════════════════════════════
Prediction and simulation engine:
- Generates predictions with confidence scores
- Stores predictions in database
- Measures actual outcomes against predictions
- Calculates calibration error over time
- Feeds the Reflection Database with lessons
- Queries past lessons BEFORE making new predictions
- Runs scenario simulations for strategic decisions
- Reports forecast accuracy for dashboard
═══════════════════════════════════════════════════════════════
"""

import os
import json
import math
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from common import get_db, get_config, get_logger, INSTITUTION_ROOT, now_iso, today_str
from agents.base import BaseAgent

logger = get_logger("oracle")


class OracleAgent(BaseAgent):
    AGENT_NAME = "oracle"
    AGENT_TYPE = "governance"
    STREAM = None  # Cross-stream prediction agent
    DEFAULT_INTERVAL_SECONDS = 21600  # Every 6 hours

    def __init__(self):
        super().__init__()
        self.oracle_dir = INSTITUTION_ROOT / "data" / "reflection"
        self.oracle_dir.mkdir(parents=True, exist_ok=True)

        # Calibration tracking
        self.calibration_window = self.config.get(
            "oracle", "calibration_window_days", default=90
        )
        self.confidence_threshold = self.config.get(
            "oracle", "confidence_threshold", default=60
        )
        self.scenario_count = self.config.get(
            "oracle", "scenario_count", default=3
        )

    def run_once(self):
        """Main cycle: measure outcomes, calibrate, generate new predictions."""
        results = []

        # Phase 1: Measure outcomes for past predictions
        measured = self._measure_outcomes()
        if measured:
            results.append(f"Measured {measured} prediction outcomes")

        # Phase 2: Calculate calibration metrics
        calibration = self._calculate_calibration()
        if calibration:
            results.append(f"Calibration: {calibration['overall_accuracy']:.0f}% accurate")

        # Phase 3: Generate new predictions for active streams
        predicted = self._generate_predictions()
        if predicted:
            results.append(f"Generated {predicted} new predictions")

        # Phase 4: Run scenario simulations for pending decisions
        simulated = self._run_simulations()
        if simulated:
            results.append(f"Ran {simulated} scenario simulations")

        # Phase 5: Feed Reflection Database with lessons
        lessons = self._extract_lessons()
        if lessons:
            results.append(f"Extracted {lessons} lessons")

        return "; ".join(results) if results else "Oracle cycle complete"

    def _measure_outcomes(self) -> int:
        """Measure actual outcomes for predictions that are due."""
        measured = 0

        # Find predictions that are past their horizon and unmeasured
        pending = self.db.fetchall(
            """SELECT * FROM predictions
               WHERE actual_value IS NULL
               AND created_at < datetime('now', '-7 days')
               ORDER BY created_at ASC
               LIMIT 10"""
        )

        for pred in pending:
            actual = self._measure_single_outcome(pred)
            if actual is not None:
                self.db.record_prediction_outcome(pred["id"], str(actual))
                measured += 1

                # Calculate error and log if significant
                try:
                    predicted_val = float(pred["predicted_value"])
                    actual_val = float(actual)
                    if actual_val != 0:
                        error_pct = abs(predicted_val - actual_val) / abs(actual_val) * 100
                    else:
                        error_pct = 100 if predicted_val != 0 else 0

                    if error_pct > 30:
                        logger.info(
                            f"Significant prediction miss: {pred['description'][:60]} "
                            f"(predicted: {predicted_val}, actual: {actual_val}, error: {error_pct:.0f}%)"
                        )
                except (ValueError, TypeError):
                    pass

        return measured

    def _measure_single_outcome(self, prediction: dict) -> Optional[float]:
        """Attempt to measure the actual outcome of a specific prediction."""
        stream = prediction.get("stream")
        desc_lower = prediction.get("description", "").lower()
        scenario_data = {}
        try:
            scenario_data = json.loads(prediction.get("scenario_data", "{}"))
        except (json.JSONDecodeError, TypeError):
            pass

        # Revenue predictions
        if "revenue" in desc_lower or "income" in desc_lower or "earn" in desc_lower:
            if stream:
                row = self.db.fetchone(
                    """SELECT COALESCE(SUM(amount), 0) as total FROM revenue
                       WHERE stream = ? AND recorded_at > datetime('now', '-30 days')""",
                    (stream,)
                )
                return row["total"] if row else 0.0
            else:
                row = self.db.fetchone(
                    "SELECT COALESCE(SUM(amount), 0) as total FROM revenue WHERE recorded_at > datetime('now', '-30 days')"
                )
                return row["total"] if row else 0.0

        # Traffic/session predictions
        if "traffic" in desc_lower or "session" in desc_lower or "visitor" in desc_lower:
            # Would query analytics API — return None if unavailable
            return None

        # Task completion predictions
        if "task" in desc_lower and ("complet" in desc_lower or "finish" in desc_lower):
            row = self.db.fetchone(
                "SELECT COUNT(*) as cnt FROM tasks WHERE status = 'completed' AND completed_at > datetime('now', '-7 days')"
            )
            return float(row["cnt"]) if row else 0.0

        # Subscriber/follower predictions
        if "subscriber" in desc_lower or "follower" in desc_lower:
            # Would query platform API
            return None

        # Sales predictions
        if "sale" in desc_lower or "purchase" in desc_lower:
            if stream:
                row = self.db.fetchone(
                    """SELECT COUNT(*) as cnt FROM revenue
                       WHERE stream = ? AND recorded_at > datetime('now', '-30 days')""",
                    (stream,)
                )
                return float(row["cnt"]) if row else 0.0

        # Content production predictions
        if "article" in desc_lower or "content" in desc_lower or "publish" in desc_lower:
            row = self.db.fetchone(
                """SELECT COUNT(*) as cnt FROM content_inventory
                   WHERE status = 'published' AND published_at > datetime('now', '-7 days')"""
            )
            return float(row["cnt"]) if row else 0.0

        # Grant predictions
        if "grant" in desc_lower:
            row = self.db.fetchone(
                """SELECT COUNT(*) as cnt FROM content_inventory
                   WHERE stream = 'grants' AND content_type = 'grant_discovered'
                   AND created_at > datetime('now', '-30 days')"""
            )
            return float(row["cnt"]) if row else 0.0

        return None

    def _calculate_calibration(self) -> Optional[dict]:
        """Calculate prediction calibration metrics."""
        # Get all measured predictions within calibration window
        predictions = self.db.fetchall(
            """SELECT * FROM predictions
               WHERE actual_value IS NOT NULL
               AND created_at > datetime('now', ?)
               ORDER BY created_at DESC""",
            (f"-{self.calibration_window} days",)
        )

        if not predictions or len(predictions) < 5:
            return None

        # Calculate metrics
        total = len(predictions)
        errors = []
        confidence_buckets = {}  # confidence_range -> [errors]

        for pred in predictions:
            try:
                predicted = float(pred["predicted_value"])
                actual = float(pred["actual_value"])
                if actual != 0:
                    error_pct = abs(predicted - actual) / abs(actual) * 100
                else:
                    error_pct = 100 if predicted != 0 else 0
                errors.append(error_pct)

                # Bucket by confidence
                confidence = pred.get("predicted_confidence", 50)
                bucket = (confidence // 20) * 20  # 0-19, 20-39, 40-59, 60-79, 80-100
                if bucket not in confidence_buckets:
                    confidence_buckets[bucket] = []
                confidence_buckets[bucket].append(error_pct)

            except (ValueError, TypeError):
                continue

        if not errors:
            return None

        avg_error = sum(errors) / len(errors)
        median_error = sorted(errors)[len(errors) // 2]
        within_20 = sum(1 for e in errors if e <= 20) / len(errors) * 100
        within_50 = sum(1 for e in errors if e <= 50) / len(errors) * 100

        # Calibration: are high-confidence predictions more accurate?
        calibration_score = 0
        for bucket, bucket_errors in sorted(confidence_buckets.items()):
            if bucket_errors:
                bucket_avg_error = sum(bucket_errors) / len(bucket_errors)
                # Higher confidence buckets should have lower error
                expected_max_error = 100 - bucket  # 80% confidence should have <20% error
                if bucket_avg_error <= expected_max_error:
                    calibration_score += 1

        max_buckets = len(confidence_buckets)
        calibration_ratio = calibration_score / max(max_buckets, 1) * 100

        calibration = {
            "total_predictions": total,
            "avg_error_pct": round(avg_error, 1),
            "median_error_pct": round(median_error, 1),
            "within_20_pct": round(within_20, 1),
            "within_50_pct": round(within_50, 1),
            "calibration_score": round(calibration_ratio, 1),
            "overall_accuracy": round(100 - min(avg_error, 100), 1),
            "confidence_buckets": {
                str(k): {"count": len(v), "avg_error": round(sum(v) / len(v), 1)}
                for k, v in confidence_buckets.items()
            },
            "calculated_at": now_iso(),
        }

        # Save calibration report
        report_path = self.oracle_dir / f"calibration_{today_str()}.json"
        report_path.write_text(json.dumps(calibration, indent=2), encoding="utf-8")

        logger.info(
            f"Oracle calibration: {calibration['overall_accuracy']:.0f}% accurate, "
            f"avg error {calibration['avg_error_pct']:.0f}%, "
            f"{calibration['within_20_pct']:.0f}% within 20%"
        )

        return calibration

    def _generate_predictions(self) -> int:
        """Generate new predictions for active streams."""
        predicted = 0

        # Query Reflection Database for relevant lessons BEFORE predicting
        lessons = self.db.get_lessons(limit=10)
        lesson_context = self._format_lessons_for_prediction(lessons)

        # Get calibration data to adjust confidence
        calibration = self._get_recent_calibration()
        confidence_adjustment = self._calculate_confidence_adjustment(calibration)

        # Generate predictions for each active stream
        streams = self.db.get_active_streams()

        for stream in streams[:5]:  # Limit per cycle
            # Check if we already have a recent prediction for this stream
            existing = self.db.fetchone(
                """SELECT id FROM predictions
                   WHERE stream = ? AND created_at > datetime('now', '-7 days')""",
                (stream["slug"],)
            )
            if existing:
                continue

            prediction = self._predict_stream(stream, lesson_context, confidence_adjustment)
            if prediction:
                self.db.add_prediction(
                    stream=stream["slug"],
                    description=prediction["description"],
                    predicted_value=str(prediction["value"]),
                    confidence=prediction["confidence"],
                    scenario_data=prediction.get("scenario_data"),
                )
                predicted += 1

        # Generate system-level predictions
        system_pred = self._predict_system_metrics(lesson_context, confidence_adjustment)
        if system_pred:
            self.db.add_prediction(
                stream=None,
                description=system_pred["description"],
                predicted_value=str(system_pred["value"]),
                confidence=system_pred["confidence"],
                scenario_data=system_pred.get("scenario_data"),
            )
            predicted += 1

        return predicted

    def _format_lessons_for_prediction(self, lessons: list) -> str:
        """Format past lessons as context for new predictions."""
        if not lessons:
            return "No past lessons available."

        formatted = []
        for lesson in lessons[:5]:
            stream = lesson.get("stream", "general")
            text = lesson.get("lesson", "")
            confidence = lesson.get("confidence_score", 50)
            formatted.append(f"- [{stream}] (confidence: {confidence}%) {text[:150]}")

        return "\n".join(formatted)

    def _get_recent_calibration(self) -> Optional[dict]:
        """Get the most recent calibration report."""
        reports = sorted(self.oracle_dir.glob("calibration_*.json"), reverse=True)
        if reports:
            try:
                return json.loads(reports[0].read_text(encoding="utf-8"))
            except (json.JSONDecodeError, IOError):
                pass
        return None

    def _calculate_confidence_adjustment(self, calibration: Optional[dict]) -> int:
        """
        Adjust confidence based on historical calibration.
        If we've been overconfident, reduce confidence.
        If we've been underconfident, increase it.
        """
        if not calibration:
            return 0

        avg_error = calibration.get("avg_error_pct", 50)
        within_20 = calibration.get("within_20_pct", 50)

        # If average error is high, we're overconfident — reduce
        if avg_error > 40:
            return -15
        elif avg_error > 25:
            return -10
        elif avg_error > 15:
            return -5

        # If we're consistently accurate, we can be more confident
        if within_20 > 80:
            return +5
        elif within_20 > 90:
            return +10

        return 0

    def _predict_stream(self, stream: dict, lesson_context: str,
                        confidence_adj: int) -> Optional[dict]:
        """Generate a prediction for a specific stream."""
        # Get recent performance data
        revenue_30d = self.db.fetchone(
            "SELECT COALESCE(SUM(amount), 0) as total FROM revenue WHERE stream = ? AND recorded_at > datetime('now', '-30 days')",
            (stream["slug"],)
        )
        revenue_7d = self.db.fetchone(
            "SELECT COALESCE(SUM(amount), 0) as total FROM revenue WHERE stream = ? AND recorded_at > datetime('now', '-7 days')",
            (stream["slug"],)
        )
        tasks_7d = self.db.fetchone(
            "SELECT COUNT(*) as cnt FROM tasks WHERE stream = ? AND status = 'completed' AND completed_at > datetime('now', '-7 days')",
            (stream["slug"],)
        )

        current_30d = revenue_30d["total"] if revenue_30d else 0
        current_7d = revenue_7d["total"] if revenue_7d else 0
        task_rate = tasks_7d["cnt"] if tasks_7d else 0

        prompt = f"""You are the Oracle of The Institution. Generate a prediction.

STREAM: {stream['name']} ({stream['slug']})
CURRENT 30-DAY REVENUE: ${current_30d:.2f}
CURRENT 7-DAY REVENUE: ${current_7d:.2f}
TASKS COMPLETED (7 days): {task_rate}
AUTONOMY LEVEL: L{stream.get('autonomy_level', 1)}

PAST LESSONS (query these before predicting):
{lesson_context}

CONFIDENCE ADJUSTMENT FROM CALIBRATION: {confidence_adj} (apply to your confidence score)

PREDICT: What will this stream's revenue be in the next 30 days?

Consider:
- Current trajectory (7-day vs 30-day trend)
- Task completion rate (more tasks = more output = more revenue potential)
- Past lessons about this type of stream
- Seasonal factors
- Platform algorithm changes
- Competition

OUTPUT FORMAT (JSON):
{{
  "description": "Revenue prediction for {stream['slug']} next 30 days",
  "value": 0.00,
  "confidence": 50,
  "reasoning": "Why this prediction (2-3 sentences)",
  "scenario_data": {{
    "optimistic": 0.00,
    "pessimistic": 0.00,
    "key_assumption": "the single most important assumption",
    "risk_factor": "what could make this wrong"
  }}
}}

Be honest about uncertainty. If you don't have enough data, say so with low confidence.
"""
        response = self.generate_text(
            prompt=prompt,
            quality_tier="critical",
            temperature=0.3,
            max_tokens=800,
        )

        if not response:
            # Fallback: simple trend extrapolation
            if current_7d > 0:
                projected = current_7d * 4  # 7 days × 4 = ~30 days
                confidence = max(20, min(80, 50 + confidence_adj))
                return {
                    "description": f"Revenue prediction for {stream['slug']} next 30 days",
                    "value": round(projected, 2),
                    "confidence": confidence,
                    "scenario_data": {
                        "optimistic": round(projected * 1.5, 2),
                        "pessimistic": round(projected * 0.5, 2),
                        "key_assumption": "Current 7-day trend continues",
                        "risk_factor": "Trend may not be sustainable",
                    },
                }
            return None

        try:
            json_start = response.find("{")
            json_end = response.rfind("}") + 1
            if json_start >= 0 and json_end > json_start:
                data = json.loads(response[json_start:json_end])
                # Apply confidence adjustment
                raw_confidence = data.get("confidence", 50)
                adjusted = max(10, min(95, raw_confidence + confidence_adj))
                data["confidence"] = adjusted
                return data
        except json.JSONDecodeError:
            pass

        return None

    def _predict_system_metrics(self, lesson_context: str,
                                 confidence_adj: int) -> Optional[dict]:
        """Generate a system-level prediction."""
        # Predict task completion rate for next week
        tasks_this_week = self.db.fetchone(
            "SELECT COUNT(*) as cnt FROM tasks WHERE status = 'completed' AND completed_at > datetime('now', '-7 days')"
        )
        current_rate = tasks_this_week["cnt"] if tasks_this_week else 0

        confidence = max(20, min(80, 55 + confidence_adj))

        return {
            "description": "System task completions predicted for next 7 days",
            "value": max(1, int(current_rate * 1.05)),  # Slight growth assumption
            "confidence": confidence,
            "scenario_data": {
                "optimistic": int(current_rate * 1.3),
                "pessimistic": max(0, int(current_rate * 0.7)),
                "key_assumption": "Agent uptime remains stable",
                "risk_factor": "Hardware failure or API outage could reduce throughput",
            },
        }

    def _run_simulations(self) -> int:
        """Run scenario simulations for pending strategic decisions."""
        simulated = 0

        # Find pending decisions that need simulation
        pending_decisions = self.db.fetchall(
            """SELECT * FROM approvals
               WHERE status = 'pending' AND item_type IN ('new_opportunity', 'expansion', 'autonomy_elevation')
               ORDER BY priority ASC LIMIT 3"""
        )

        for decision in pending_decisions:
            details = {}
            try:
                details = json.loads(decision.get("details", "{}"))
            except (json.JSONDecodeError, TypeError):
                continue

            simulation = self._simulate_decision(decision, details)
            if simulation:
                # Store simulation as part of the decision context
                self.db.execute(
                    "UPDATE approvals SET details = ? WHERE id = ?",
                    (json.dumps({**details, "oracle_simulation": simulation}, default=str),
                     decision["id"])
                )
                simulated += 1

        return simulated

    def _simulate_decision(self, decision: dict, details: dict) -> Optional[dict]:
        """Run a scenario simulation for a specific decision."""
        # Query Reflection Database for relevant lessons
        stream = decision.get("stream")
        lessons = self.db.get_lessons(stream=stream, limit=5) if stream else self.db.get_lessons(limit=5)
        lesson_text = "\n".join(f"- {l.get('lesson', '')}" for l in lessons)

        prompt = f"""You are the Oracle. Simulate {self.scenario_count} scenarios for this decision.

DECISION: {decision.get('description', 'Unknown')}
TYPE: {decision.get('item_type', 'unknown')}
DETAILS: {json.dumps(details, default=str)[:500]}

RELEVANT PAST LESSONS:
{lesson_text if lesson_text else 'No relevant lessons.'}

Generate {self.scenario_count} scenarios:
1. OPTIMISTIC: Best realistic outcome (not fantasy)
2. EXPECTED: Most likely outcome
3. PESSIMISTIC: Worst realistic outcome (not catastrophe)

For each scenario, estimate:
- Revenue impact (30 days)
- Time investment required
- Risk level (1-10)
- Probability of this scenario occurring

OUTPUT FORMAT (JSON):
{{
  "scenarios": [
    {{
      "name": "optimistic|expected|pessimistic",
      "description": "What happens in this scenario",
      "revenue_impact_30d": 0.00,
      "time_investment_hours": 0,
      "risk_level": 5,
      "probability": 0.33
    }}
  ],
  "recommendation": "proceed|wait|reject",
  "recommendation_reasoning": "Why (1-2 sentences)",
  "key_uncertainty": "The single biggest unknown"
}}
"""
        response = self.generate_text(
            prompt=prompt,
            quality_tier="critical",
            temperature=0.4,
            max_tokens=1200,
        )

        if not response:
            return None

        try:
            json_start = response.find("{")
            json_end = response.rfind("}") + 1
            if json_start >= 0 and json_end > json_start:
                return json.loads(response[json_start:json_end])
        except json.JSONDecodeError:
            pass

        return None

    def _extract_lessons(self) -> int:
        """Extract lessons from prediction outcomes and feed Reflection Database."""
        lessons_extracted = 0

        # Find recently measured predictions with significant error
        recent_measured = self.db.fetchall(
            """SELECT * FROM predictions
               WHERE actual_value IS NOT NULL
               AND error_magnitude IS NOT NULL
               AND error_magnitude > 25
               AND created_at > datetime('now', '-30 days')
               ORDER BY error_magnitude DESC
               LIMIT 5"""
        )

        for pred in recent_measured:
            # Check if we've already extracted a lesson from this prediction
            existing = self.db.fetchone(
                "SELECT id FROM learnings WHERE prediction = ? AND stream = ?",
                (pred["description"], pred.get("stream"))
            )
            if existing:
                continue

            error = pred.get("error_magnitude", 0)
            try:
                predicted = float(pred["predicted_value"])
                actual = float(pred["actual_value"])
                direction = "overestimated" if predicted > actual else "underestimated"
            except (ValueError, TypeError):
                direction = "mispredicted"
                predicted = pred.get("predicted_value", "?")
                actual = pred.get("actual_value", "?")

            # Generate lesson
            lesson_text = (
                f"Oracle {direction} for {pred.get('stream', 'system')}: "
                f"predicted {predicted}, actual {actual} (error: {error:.0f}%). "
            )

            # Determine corrected belief
            if error > 50:
                corrected = f"Significantly {direction}. Reduce confidence for similar predictions by 15%."
            elif error > 30:
                corrected = f"Moderately {direction}. Adjust model for {pred.get('stream', 'general')} predictions."
            else:
                corrected = f"Slight {direction}. Within acceptable range but monitor."

            lesson_text += corrected

            self.db.add_learning(
                prediction=pred["description"],
                outcome=f"Predicted: {predicted}, Actual: {actual}, Error: {error:.0f}%",
                lesson=lesson_text,
                confidence=pred.get("predicted_confidence", 50),
                stream=pred.get("stream"),
                corrected_belief=corrected,
                tags=["oracle", "calibration", pred.get("stream", "system")],
            )

            lessons_extracted += 1

        return lessons_extracted

    def get_prediction_accuracy_report(self) -> dict:
        """Generate accuracy report for dashboard."""
        calibration = self._get_recent_calibration()
        if not calibration:
            return {"status": "insufficient_data", "message": "Not enough measured predictions yet."}

        # Get active predictions
        active = self.db.fetchall(
            "SELECT * FROM predictions WHERE actual_value IS NULL ORDER BY created_at DESC LIMIT 10"
        )

        return {
            "calibration": calibration,
            "active_predictions": [
                {
                    "description": p["description"],
                    "predicted_value": p["predicted_value"],
                    "confidence": p["predicted_confidence"],
                    "stream": p.get("stream"),
                    "created_at": p["created_at"],
                }
                for p in active
            ],
            "total_measured": calibration.get("total_predictions", 0),
            "accuracy": calibration.get("overall_accuracy", 0),
        }