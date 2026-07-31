#!/usr/bin/env python3
"""
═══════════════════════════════════════════════════════════════
THE INSTITUTION — CONSTITUTIONAL COURT
═══════════════════════════════════════════════════════════════
Pre-execution audit of EVERY action against all 10 principles
plus the Revenue Constitution. Outputs PASS / FAIL /
REVIEW_REQUIRED with detailed reasoning.

This agent runs BEFORE any consequential action. It is the
first line of defence against drift, misalignment, and harm.
═══════════════════════════════════════════════════════════════
"""

import json
import re
from datetime import datetime
from typing import Optional
from dataclasses import dataclass, field

from common import get_db, get_logger, get_config, now_iso
from providers import get_ai_provider

logger = get_logger("constitutional_court")


# ─── CONSTITUTIONAL PRINCIPLES ────────────────────────────────
PRINCIPLES = {
    "stewardship": {
        "name": "Stewardship",
        "question": "Does this action serve the founder's long-term capability and flourishing?",
        "fail_if": "The action makes the founder more dependent, removes their options, or serves the system's interests over the founder's.",
        "severity": "hard",
    },
    "agency": {
        "name": "Founder Agency",
        "question": "Does this action preserve or expand the founder's choices and autonomy?",
        "fail_if": "The action reduces the founder's future options, locks them into a commitment, or acts without required approval.",
        "severity": "hard",
    },
    "continuity": {
        "name": "Continuity",
        "question": "Does this action create a single point of failure or risk system continuity?",
        "fail_if": "The action introduces an unrecoverable dependency, risks data loss, or could halt all operations.",
        "severity": "soft",
    },
    "reality": {
        "name": "Reality Always Wins",
        "question": "Is this action grounded in evidence and testable against reality?",
        "fail_if": "
The action is based on untested assumptions presented as facts, or ignores contradictory evidence.",
        "severity": "soft",
    },
    "memory": {
        "name": "Institutional Memory",
        "question": "Will the knowledge from this action be preserved for the institution?",
        "fail_if": "The action produces knowledge that will be lost, or bypasses institutional memory systems.",
        "severity": "soft",
    },
    "transparency": {
        "name": "Transparency",
        "question": "Can this action and its reasoning be fully explained and audited?",
        "fail_if": "The action is opaque, hides its reasoning, or produces unauditable outcomes.",
        "severity": "soft",
    },
    "simplicity": {
        "name": "Simplicity",
        "question": "Is this the simplest approach that achieves the goal?",
        "fail_if": "The action introduces unnecessary complexity without clear justification.",
        "severity": "soft",
    },
    "education": {
        "name": "Education",
        "question": "Does this action teach the founder something or build their capability?",
        "fail_if": "The action deliberately keeps the founder uninformed or creates learned helplessness.",
        "severity": "soft",
    },
    "security": {
        "name": "Security",
        "question": "Does this action protect secrets, use least privilege, and maintain audit trails?",
        "fail_if": "The action exposes secrets, uses excessive permissions, or bypasses security controls.",
        "severity": "hard",
    },
    "legality": {
        "name": "Legality",
        "question": "Is this action legal, ethical, and within platform terms of service?",
        "fail_if": "The action violates laws, platform ToS, ethical norms, or involves deception.",
        "severity": "hard",
    },
}

# ─── REVENUE CONSTITUTION (HARD RED LINES) ────────────────────
REVENUE_RED_LINES = [
    {"id": "R1", "rule": "No illegal income", "keywords": ["illegal", "fraud", "launder"
, "evade", "black market"]},
    {"id": "R2", "rule": "No deception or misleading claims", "keywords": ["fake", "mislead", "deceive", "false claim", "scam", "fabricate"]},
    {"id": "R3", "rule": "No spam", "keywords": ["spam", "mass unsolicited", "bulk cold", "blast email"]},
    {"id": "R4", "rule": "No platform ToS violations", "keywords": ["scrape login", "bypass rate", "fake account", "bot detection evasion", "circumvent"]},
    {"id": "R5", "rule": "No autonomous money movement", "keywords": ["transfer funds", "wire money", "auto-pay", "autonomous purchase", "send payment"]},
    {"id": "R6", "rule": "No autonomous contracts", "keywords": ["sign contract", "agree to terms", "bind legally", "autonomous commitment", "accept agreement"]},
    {"id": "R7", "rule": "No autonomous tax filings", "keywords": ["file tax", "submit tax", "lodge return", "tax filing"]},
    {"id": "R8", "rule": "No high-risk speculation", "keywords": ["crypto trading", "options trading", "leverage", "margin trade", "gamble", "speculate"]},
    {"id": "R9", "rule": "No professional advice without qualification", "keywords": ["medical advice", "legal advice", "financial advice", "diagnose", "prescribe"]},
    {"id": "R10", "rule": "No public publishing without approval", "keywords": ["publish without review", "auto-post public", "deploy without approval", "go live"]},
    {"id": "R11", "rule": "No spending above threshold without approval", "keywords": ["spend without approval", "purchase over", "autonomous spending", "buy without"]},
    {"id": "R12", "rule": "No irreversible actions without approval", "keywords": ["delete permanently", "cannot undo", "irreversible", "destroy data", "wipe"]},
]

# ─── APPROVAL-REQUIRED ACTION PATTERNS ────────────────────────
APPROVAL_TRIGGERS = [
    "money", "spend", "purchase", "pay", "invoice", "transfer",
    "contract", "legal", "sign", "agree", "commit",
    "publish", "post public", "deploy production",
    "delete", "remove permanently", "destroy
",
    "identity", "use name", "impersonate",
    "email stranger", "cold outreach", "contact new",
    "tax", "government", "regulatory",
    "autonomous", "without approval", "auto-execute",
    "submit", "send to", "upload public",
]


@dataclass
class AuditResult:
    """Result of a constitutional audit."""
    action: str
    agent: str
    stream: str
    overall_result: str  # PASS, FAIL, REVIEW_REQUIRED
    principle_results: dict = field(default_factory=dict)
    revenue_violations: list = field(default_factory=list)
    requires_approval: bool = False
    reasoning: str = ""
    timestamp: str = field(default_factory=now_iso)

    def to_dict(self) -> dict:
        return {
            "action": self.action,
            "agent": self.agent,
            "stream": self.stream,
            "overall_result": self.overall_result,
            "principle_results": self.principle_results,
            "revenue_violations": self.revenue_violations,
            "requires_approval": self.requires_approval,
            "reasoning": self.reasoning,
            "timestamp": self.timestamp,
        }


class ConstitutionalCourt:
    """
    Audits every proposed action against the Constitution before execution.
    Three possible outcomes: PASS, FAIL, REVIEW_REQUIRED.
    Hard principle violations (stewardship, agency, security, legality) = auto-FAIL.
    Soft principle violations = REVIEW_REQUIRED.
    Revenue red line violations = auto-FAIL.
    Approval-trigger patterns = REVIEW_REQUIRED (escalated to founder).
    """

    def __init__(self):
        self.db = get_db()
        self.config = get_config()
        self.ai = get_ai_provider()

    def audit(self, action: str, agent: str = "unknown",
              stream: str = None, context: dict = None) -> AuditResult:
        """
        Perform a full constitutional audit of a proposed action.
        This is the main entry point. Every consequential action passes through here.
        """
        context = context or 
{}
        action_lower = action.lower()

        principle_results = {}
        revenue_violations = []
        requires_approval = False
        reasoning_parts = []

        # ─── PHASE 1: Revenue Red Line Check ──────────────────
        for red_line in REVENUE_RED_LINES:
            triggered = self._check_red_line(action_lower, red_line)
            if triggered:
                revenue_violations.append({
                    "id": red_line["id"],
                    "rule": red_line["rule"],
                    "trigger": triggered,
                })
                reasoning_parts.append(
                    f"REVENUE VIOLATION [{red_line['id']}]: {red_line['rule']} "
                    f"(triggered by: '{triggered}')"
                )

        # ─── PHASE 2: Principle Checks ────────────────────────
        for key, principle in PRINCIPLES.items():
            result = self._check_principle(key, principle, action, context)
            principle_results[key] = result
            if result["status"] != "PASS":
                reasoning_parts.append(
                    f"PRINCIPLE [{principle['name']}]: {result['status']} — {result['reasoning']}"
                )

        # ─── PHASE 3: Approval Trigger Check ──────────────────
        approval_triggers_found = self._check_approval_triggers(action_lower)
        if approval_triggers_found:
            requires_approval = True
            reasoning_parts.append(
                f"APPROVAL REQUIRED: Action matches triggers: {', '.join(approval_triggers_found)}"
            )

        # ─── PHASE 4: Determine Overall Result ────────────────
        overall_result = self._determine_overall(
            principle_results, revenue_violations, requires_approval
        )

        # ─── PHASE 5: Build Reasoning Summary ─────────────────
        if overall_result == "PASS" and not requires_approval:
            reasoning = "Action passes all constitutional checks. No approval required."
        elif overall_result
 == "PASS" and requires_approval:
            reasoning = (
                "Action passes constitutional checks but requires founder approval "
                f"due to: {', '.join(approval_triggers_found)}"
            )
        elif overall_result == "FAIL":
            reasoning = "ACTION BLOCKED. " + " | ".join(reasoning_parts)
        else:
            reasoning = "REVIEW REQUIRED. " + " | ".join(reasoning_parts)

        # ─── PHASE 6: Log the Audit ───────────────────────────
        result = AuditResult(
            action=action,
            agent=agent,
            stream=stream,
            overall_result=overall_result,
            principle_results=principle_results,
            revenue_violations=revenue_violations,
            requires_approval=requires_approval,
            reasoning=reasoning,
        )

        self._log_audit(result)

        if overall_result == "FAIL":
            logger.warning(f"BLOCKED: [{agent}] {action[:100]} — {reasoning[:200]}")
        elif requires_approval:
            logger.info(f"APPROVAL NEEDED: [{agent}] {action[:100]}")
        else:
            logger.debug(f"PASSED: [{agent}] {action[:80]}")

        return result

    def audit_with_llm(self, action: str, agent: str = "unknown",
                       stream: str = None, context: dict = None) -> AuditResult:
        """
        Enhanced audit using LLM for nuanced principle evaluation.
        Falls back to rule-based audit if LLM unavailable.
        Use for complex or ambiguous actions.
        """
        # First run rule-based audit
        rule_result = self.audit(action, agent, stream, context)

        # If rule-based already FAILs, no need for LLM
        if rule_result.overall_result == "FAIL":
            return rule_result

        # Use LLM for deeper analysis on PASS/REVIEW cases
        try:
            llm_analysis = self._llm_principle_check(action, context or {})
            if llm_analysis:
                # Merge LLM findings with rule-base
d results
                for principle_key, llm_status in llm_analysis.items():
                    if principle_key in rule_result.principle_results:
                        if llm_status == "FAIL" and rule_result.principle_results[principle_key]["status"] == "PASS":
                            rule_result.principle_results[principle_key]["status"] = "REVIEW_REQUIRED"
                            rule_result.principle_results[principle_key]["reasoning"] += " [LLM flagged concern]"
                            rule_result.overall_result = "REVIEW_REQUIRED"
        except Exception as e:
            logger.debug(f"LLM audit enhancement unavailable: {e}")

        return rule_result

    def batch_audit(self, actions: list) -> list:
        """Audit multiple actions. Returns list of AuditResults."""
        results = []
        for action_item in actions:
            if isinstance(action_item, dict):
                result = self.audit(
                    action=action_item.get("action", ""),
                    agent=action_item.get("agent", "unknown"),
                    stream=action_item.get("stream"),
                    context=action_item.get("context"),
                )
            else:
                result = self.audit(action=str(action_item))
            results.append(result)
        return results

    def get_audit_history(self, limit: int = 50, agent: str = None,
                          result_filter: str = None) -> list:
        """Retrieve past audit records."""
        sql = "SELECT * FROM constitutional_audits WHERE 1=1"
        params = []
        if agent:
            sql += " AND agent = ?"
            params.append(agent)
        if result_filter:
            sql += " AND result = ?"
            params.append(result_filter)
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        return self.db.fetchall(sql, tuple(params))

    def get_violation_stats(self) -> dict:
        """Get statistics on constitutional
 violations for dashboard."""
        total = self.db.fetchone("SELECT COUNT(*) as cnt FROM constitutional_audits")
        fails = self.db.fetchone(
            "SELECT COUNT(*) as cnt FROM constitutional_audits WHERE result = 'FAIL'"
        )
        reviews = self.db.fetchone(
            "SELECT COUNT(*) as cnt FROM constitutional_audits WHERE result = 'REVIEW_REQUIRED'"
        )
        passes = self.db.fetchone(
            "SELECT COUNT(*) as cnt FROM constitutional_audits WHERE result = 'PASS'"
        )
        return {
            "total_audits": total["cnt"] if total else 0,
            "passes": passes["cnt"] if passes else 0,
            "fails": fails["cnt"] if fails else 0,
            "reviews": reviews["cnt"] if reviews else 0,
            "compliance_rate": round(
                (passes["cnt"] / max(total["cnt"], 1)) * 100, 1
            ) if passes and total else 100.0,
        }

    # ─── PRIVATE METHODS ──────────────────────────────────────

    def _check_red_line(self, action_lower: str, red_line: dict) -> Optional[str]:
        """Check if action triggers a revenue red line. Returns trigger keyword or None."""
        for keyword in red_line["keywords"]:
            if keyword in action_lower:
                return keyword
        return None

    def _check_principle(self, key: str, principle: dict,
                         action: str, context: dict) -> dict:
        """
        Check a single principle against the action.
        Returns dict with status and reasoning.
        """
        action_lower = action.lower()
        status = "PASS"
        reasoning = "No violation detected."

        # ─── STEWARDSHIP ──────────────────────────────────────
        if key == "stewardship":
            dependency_patterns = [
                "lock in", "cannot remove", "permanent dependency",
                "vendor lock", "irreplaceable", "cannot migrate",
            ]
            for pattern in dependency_patterns:
                if patt
ern in action_lower:
                    status = "FAIL"
                    reasoning = f"Creates dependency: '{pattern}' detected."
                    break
            if context.get("reduces_founder_capability"):
                status = "FAIL"
                reasoning = "Context indicates reduced founder capability."

        # ─── AGENCY ───────────────────────────────────────────
        elif key == "agency":
            agency_violations = [
                "lock out", "remove access", "restrict founder",
                "without consent", "override founder", "bypass approval",
                "autonomous decision", "no human review",
            ]
            for pattern in agency_violations:
                if pattern in action_lower:
                    status = "FAIL"
                    reasoning = f"Agency violation: '{pattern}' detected."
                    break
            if context.get("requires_approval") and not context.get("approved"):
                status = "REVIEW_REQUIRED"
                reasoning = "Action requires approval that has not been granted."

        # ─── CONTINUITY ───────────────────────────────────────
        elif key == "continuity":
            continuity_risks = [
                "single point", "no backup", "no failover",
                "delete only copy", "overwrite without backup",
                "single server", "no redundancy",
            ]
            for pattern in continuity_risks:
                if pattern in action_lower:
                    status = "REVIEW_REQUIRED"
                    reasoning = f"Continuity risk: '{pattern}' detected."
                    break

        # ─── REALITY ──────────────────────────────────────────
        elif key == "reality":
            reality_risks = [
                "guaranteed", "certain to", "definitely will",
                "no risk", "cannot fail", "100% success",
            ]
            for pattern in reality_risks:
                if pattern in action_lo
wer:
                    status = "REVIEW_REQUIRED"
                    reasoning = f"Overconfidence detected: '{pattern}'. Predictions must be testable."
                    break

        # ─── MEMORY ───────────────────────────────────────────
        elif key == "memory":
            memory_risks = [
                "don't log", "skip recording", "ephemeral only",
                "no trace", "off the record",
            ]
            for pattern in memory_risks:
                if pattern in action_lower:
                    status = "REVIEW_REQUIRED"
                    reasoning = f"Memory risk: '{pattern}'. All knowledge must be preserved."
                    break

        # ─── TRANSPARENCY ─────────────────────────────────────
        elif key == "transparency":
            opacity_risks = [
                "hide", "obscure", "don't explain", "black box",
                "no reasoning", "secretly", "silently",
            ]
            for pattern in opacity_risks:
                if pattern in action_lower:
                    status = "REVIEW_REQUIRED"
                    reasoning = f"Transparency risk: '{pattern}'. Actions must be explainable."
                    break

        # ─── SIMPLICITY ───────────────────────────────────────
        elif key == "simplicity":
            complexity_indicators = context.get("complexity_score", 0)
            if complexity_indicators > 8:
                status = "REVIEW_REQUIRED"
                reasoning = f"High complexity score ({complexity_indicators}/10). Justify necessity."
            complexity_words = [
                "microservice", "kubernetes", "distributed",
                "multi-layer", "orchestration framework",
            ]
            complex_count = sum(1 for w in complexity_words if w in action_lower)
            if complex_count >= 3:
                status = "REVIEW_REQUIRED"
                reasoning = f"Multiple complexity indicators ({complex_count}). Ensure simplicity principle is 
met."

        # ─── EDUCATION ────────────────────────────────────────
        elif key == "education":
            if context.get("hides_reasoning_from_founder"):
                status = "REVIEW_REQUIRED"
                reasoning = "Action does not educate the founder."

        # ─── SECURITY ─────────────────────────────────────────
        elif key == "security":
            security_violations = [
                "api key in", "password in", "secret in",
                "token in code", "credentials in", "plaintext secret",
                "no encryption", "disable auth", "open port",
                "root access", "sudo without", "chmod 777",
            ]
            for pattern in security_violations:
                if pattern in action_lower:
                    status = "FAIL"
                    reasoning = f"Security violation: '{pattern}' detected."
                    break
            if context.get("exposes_secrets"):
                status = "FAIL"
                reasoning = "Context indicates secret exposure."

        # ─── LEGALITY ─────────────────────────────────────────
        elif key == "legality":
            legality_violations = [
                "scrape personal data", "violat", "illegal",
                "without license", "unauthorized access",
                "impersonat", "forgery", "counterfeit",
                "tax evasion", "money laundering",
            ]
            for pattern in legality_violations:
                if pattern in action_lower:
                    status = "FAIL"
                    reasoning = f"Legality violation: '{pattern}' detected."
                    break
            if context.get("violates_tos"):
                status = "FAIL"
                reasoning = "Context indicates Terms of Service violation."

        return {"status": status, "reasoning": reasoning}

    def _check_approval_triggers(self, action_lower: str) -> list:
        """Check if action matches any approval-required patterns."
""
        found = []
        for trigger in APPROVAL_TRIGGERS:
            if trigger in action_lower:
                found.append(trigger)
        return found

    def _determine_overall(self, principle_results: dict,
                           revenue_violations: list,
                           requires_approval: bool) -> str:
        """Determine the overall audit result."""
        # Revenue violations are always FAIL
        if revenue_violations:
            return "FAIL"

        # Check for hard principle failures
        for key, result in principle_results.items():
            if result["status"] == "FAIL":
                severity = PRINCIPLES[key]["severity"]
                if severity == "hard":
                    return "FAIL"

        # Check for any soft failures or review requirements
        has_review = False
        for key, result in principle_results.items():
            if result["status"] == "REVIEW_REQUIRED":
                has_review = True
            elif result["status"] == "FAIL":
                has_review = True  # Soft FAIL becomes REVIEW_REQUIRED

        if has_review:
            return "REVIEW_REQUIRED"

        if requires_approval:
            return "REVIEW_REQUIRED"

        return "PASS"

    def _llm_principle_check(self, action: str, context: dict) -> Optional[dict]:
        """
        Use LLM to perform nuanced principle evaluation.
        Returns dict of {principle_key: status} or None if unavailable.
        """
        prompt = f"""You are the Constitutional Court of The Institution.
Evaluate this proposed action against each principle.

ACTION: {action}
CONTEXT: {json.dumps(context, default=str)}

PRINCIPLES:
1. Stewardship: Does this serve the founder's long-term capability?
2. Agency: Does this preserve/expand the founder's choices?
3. Continuity: Does this avoid single points of failure?
4. Reality: Is this grounded in evidence?
5. Memory: Will knowledge be preserved?
6. Transparency: Can this be explained
 and audited?
7. Simplicity: Is this the simplest approach?
8. Education: Does this teach the founder?
9. Security: Does this protect secrets and use least privilege?
10. Legality: Is this legal and ethical?

For each principle, respond with ONLY one word: PASS, FAIL, or REVIEW.
Format exactly as:
stewardship: PASS
agency: PASS
continuity: PASS
reality: PASS
memory: PASS
transparency: PASS
simplicity: PASS
education: PASS
security: PASS
legality: PASS
"""
        try:
            response = self.ai.generate(
                prompt=prompt,
                quality_tier="critical",
                task_type="constitutional_audit",
                use_cache=True,
                temperature=0.1,
            )
            if not response:
                return None

            results = {}
            for line in response.strip().split("\n"):
                line = line.strip().lower()
                for key in PRINCIPLES:
                    if line.startswith(key):
                        if "fail" in line:
                            results[key] = "FAIL"
                        elif "review" in line:
                            results[key] = "REVIEW_REQUIRED"
                        else:
                            results[key] = "PASS"
                        break
            return results if results else None
        except Exception as e:
            logger.debug(f"LLM principle check failed: {e}")
            return None

    def _log_audit(self, result: AuditResult):
        """Persist audit result to database."""
        try:
            self.db.log_audit(
                action=result.action[:500],
                agent=result.agent,
                stream=result.stream or "",
                principle=",".join(
                    k for k, v in result.principle_results.items()
                    if v["status"] != "PASS"
                ) or "all_pass",
                result=result.overall_result,
                reasoning=result.reasoning[:1000],
   
         )
        except Exception as e:
            logger.error(f"Failed to log audit: {e}")

    def create_approval_request(self, result: AuditResult) -> int:
        """
        When an action requires approval, create an approval queue entry.
        Returns the approval ID.
        """
        return self.db.add_approval(
            item_type="constitutional_review",
            description=f"Action requires approval: {result.action[:200]}",
            details=json.dumps(result.to_dict(), default=str),
            stream=result.stream,
            agent=result.agent,
            priority=2 if result.overall_result == "REVIEW_REQUIRED" else 3,
        )


# ─── MODULE-LEVEL SINGLETON ───────────────────────────────────
_court_instance = None

def get_court() -> ConstitutionalCourt:
    global _court_instance
    if _court_instance is None:
        _court_instance = ConstitutionalCourt()
    return _court_instance


# ─── CONVENIENCE FUNCTION ─────────────────────────────────────
def audit_action(action: str, agent: str = "unknown",
                 stream: str = None, context: dict = None) -> AuditResult:
    """Quick audit without importing the class."""
    return get_court().audit(action, agent, stream, context)