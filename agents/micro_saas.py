#!/usr/bin/env python3
"""
═══════════════════════════════════════════════════════════════
THE INSTITUTION — STREAM 10: MICRO-SAAS / API SERVICES
═══════════════════════════════════════════════════════════════
Phase 2 revenue stream with strict validation gate:
- Small tools solving specific problems
- Built ONLY after demand validation:
  * 5+ conversations with potential users
  * 2+ preorders or expressions of intent
  * Interest score >= 7/10
- Scaffold project structures
- Deploy to Oracle Cloud free tier or Cloudflare Workers
- Charged per use or monthly subscription
═══════════════════════════════════════════════════════════════
"""

import os
import json
import subprocess
import hashlib
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import requests

from common import get_db, get_config, get_logger, INSTITUTION_ROOT, now_iso, today_str, slugify
from agents.base import BaseAgent

logger = get_logger("micro_saas")


class MicroSaasAgent(BaseAgent):
    AGENT_NAME = "micro_saas"
    AGENT_TYPE = "revenue_stream"
    STREAM = "micro_saas"
    DEFAULT_INTERVAL_SECONDS = 86400  # Daily

    def __init__(self):
        super().__init__()
        self.saas_dir = INSTITUTION_ROOT / "saas"
        self.projects_dir = self.saas_dir / "projects"
        self.deployed_dir = self.saas_dir / "deployed"
        self.validation_dir = self.saas_dir / "validation"
        for d in [self.projects_dir, self.deployed_dir, self.validation_dir]:
            d.mkdir(parents=True, exist_ok=True)

        # Validation gate thresholds (from config)
        stream_cfg = self.get_stream_config()
        gate = stream_cfg.get("validation_gate", {})
        self.min_conversations = gate.get("min_conversations", 5)
        self.min_preorders = gate.get("min_preorders", 2)
        self.min_interest_score = gate.get("min_interest_score", 7)

    def run_once(self):
        """Main cycle: validate ideas, scaffold validated ones, deploy ready ones
."""
        if not self.should_run_today():
            return "Stream disabled or inactive"

        stream_cfg = self.get_stream_config()

        # Stream should be 'pending' until first validation passes
        stream = self.db.get_stream(self.STREAM)
        if stream and stream["status"] == "pending":
            # Focus on validation
            return self._validation_phase(stream_cfg)

        # If active, manage existing projects
        results = []

        # Check validation status of ideas
        validation_results = self._check_validations()
        if validation_results:
            results.append(validation_results)

        # Scaffold newly validated projects
        scaffolded = self._scaffold_validated()
        if scaffolded:
            results.append(f"Scaffolded {scaffolded} projects")

        # Check deployment readiness
        deployed = self._check_deployments()
        if deployed:
            results.append(f"Deployed {deployed} services")

        # Monitor deployed services
        self._monitor_services()

        return "; ".join(results) if results else "Micro-SaaS cycle complete"

    def _validation_phase(self, stream_cfg: dict) -> str:
        """Run validation activities for potential micro-SaaS ideas."""
        ideas = stream_cfg.get("ideas", [])
        if not ideas:
            return "No micro-SaaS ideas configured"

        results = []

        for idea in ideas:
            idea_slug = slugify(idea)
            validation_file = self.validation_dir / f"{idea_slug}.json"

            # Load or create validation record
            if validation_file.exists():
                validation = json.loads(validation_file.read_text(encoding="utf-8"))
            else:
                validation = {
                    "idea": idea,
                    "slug": idea_slug,
                    "status": "researching",
                    "conversations": [],
                    "preorders": [],
                    "interest_score
s": [],
                    "research_notes": [],
                    "created_at": now_iso(),
                    "updated_at": now_iso(),
                }

            # Conduct research on this idea
            if validation["status"] == "researching":
                research = self._research_idea(idea)
                if research:
                    validation["research_notes"].append(research)
                    validation["updated_at"] = now_iso()

                    # Generate validation questions
                    questions = self._generate_validation_questions(idea, research)
                    validation["validation_questions"] = questions

                    # Create approval request for outreach
                    existing_approval = self.db.fetchone(
                        "SELECT id FROM approvals WHERE item_type = 'saas_validation' AND description LIKE ? AND status = 'pending'",
                        (f"%{idea_slug}%",)
                    )
                    if not existing_approval:
                        self.db.add_approval(
                            item_type="saas_validation",
                            description=f"Validate micro-SaaS idea: '{idea}' — conduct {self.min_conversations} conversations",
                            details=json.dumps({
                                "idea": idea,
                                "questions": questions,
                                "target_audience": research.get("target_audience", ""),
                                "competitors": research.get("competitors", []),
                            }, default=str),
                            stream=self.STREAM,
                            agent=self.AGENT_NAME,
                            priority=3,
                        )

                    validation["status"] = "awaiting_conversations"
                    results.append(f"Research complete for '{idea}'")

            # Check if validation criteria met
            elif valida
tion["status"] == "awaiting_conversations":
                if self._validation_passed(validation):
                    validation["status"] = "validated"
                    validation["validated_at"] = now_iso()
                    results.append(f"VALIDATED: '{idea}' passed all gates!")

                    # Activate the stream if first validation
                    stream = self.db.get_stream(self.STREAM)
                    if stream and stream["status"] == "pending":
                        self.db.update_stream_status(self.STREAM, "active")
                        logger.info("Micro-SaaS stream activated! First idea validated.")

            # Save validation record
            validation_file.write_text(json.dumps(validation, indent=2, default=str), encoding="utf-8")

        return "; ".join(results) if results else "Validation in progress"

    def _research_idea(self, idea: str) -> Optional[dict]:
        """Research a micro-SaaS idea for viability."""
        prompt = f"""Research this micro-SaaS idea for viability.

IDEA: {idea}

ANALYSE:
1. Target audience: Who specifically would pay for this?
2. Existing competitors: What already exists? (name specific products)
3. Pricing landscape: What do competitors charge?
4. Differentiation: What could make this version unique?
5. Technical feasibility: Can this be built with free-tier infrastructure?
6. Market size: Is the audience large enough to sustain a micro-SaaS?
7. Acquisition channels: How would you find the first 10 customers?

OUTPUT FORMAT (JSON):
{{
  "target_audience": "Specific description of ideal customer",
  "competitors": ["Competitor 1", "Competitor 2", "Competitor 3"],
  "competitor_pricing": "What competitors charge",
  "differentiation": "What makes this version different",
  "technical_feasibility": "Can it run on free tier? What stack?",
  "market_size_estimate": "Rough TAM/SAM estimate",
  "acquisition_channels": ["Channel 1", "Channel 2"],
  "risks": ["Risk 1", "Risk 2"],
  "viabilit
y_score": 7,
  "recommended_stack": "Suggested tech stack",
  "mvp_scope": "What the minimum viable version looks like"
}}
"""
        response = self.generate_text(
            prompt=prompt,
            quality_tier="critical",
            temperature=0.5,
            max_tokens=2000,
        )

        if not response:
            return None

        try:
            json_start = response.find("{")
            json_end = response.rfind("}") + 1
            if json_start >= 0 and json_end > json_start:
                data = json.loads(response[json_start:json_end])
                if "target_audience" in data:
                    return data
        except json.JSONDecodeError:
            pass

        return None

    def _generate_validation_questions(self, idea: str, research: dict) -> list:
        """Generate questions to ask potential customers during validation."""
        return [
            f"Have you ever needed something like '{idea}'? What did you do instead?",
            f"How much time/money does this problem currently cost you?",
            f"What would you expect to pay for a solution like this?",
            f"Would you be willing to try a beta version? (Preorder commitment)",
            f"What's the one feature that would make this essential for you?",
            f"Where do you currently look for solutions to this problem?",
            f"Would you recommend this to a colleague? Who specifically?",
        ]

    def _validation_passed(self, validation: dict) -> bool:
        """Check if all validation gate criteria are met."""
        conversations = len(validation.get("conversations", []))
        preorders = len(validation.get("preorders", []))
        scores = validation.get("interest_scores", [])
        avg_score = sum(scores) / max(len(scores), 1) if scores else 0

        passed = (
            conversations >= self.min_conversations and
            preorders >= self.min_preorders and
            avg_score >= self.min_interest_sco
re
        )

        if passed:
            logger.info(
                f"Validation PASSED for '{validation['idea']}': "
                f"{conversations}/{self.min_conversations} conversations, "
                f"{preorders}/{self.min_preorders} preorders, "
                f"score {avg_score:.1f}/{self.min_interest_score}"
            )
            self.log_learning(
                prediction=f"Idea '{validation['idea']}' would pass validation",
                outcome=f"Passed: {conversations} conversations, {preorders} preorders, score {avg_score:.1f}",
                lesson=f"Validation gate works. Idea '{validation['idea']}' has demonstrated demand.",
                confidence=75,
                tags=["micro_saas", "validation", "passed"],
            )

        return passed

    def record_conversation(self, idea_slug: str, notes: str, interest_score: int,
                            preorder: bool = False):
        """Record a validation conversation (called by founder via dashboard)."""
        validation_file = self.validation_dir / f"{idea_slug}.json"
        if not validation_file.exists():
            logger.warning(f"No validation record for {idea_slug}")
            return

        validation = json.loads(validation_file.read_text(encoding="utf-8"))
        validation["conversations"].append({
            "notes": notes,
            "interest_score": interest_score,
            "preorder": preorder,
            "date": now_iso(),
        })
        validation["interest_scores"].append(interest_score)
        if preorder:
            validation["preorders"].append({
                "date": now_iso(),
                "notes": notes[:100],
            })
        validation["updated_at"] = now_iso()

        validation_file.write_text(json.dumps(validation, indent=2, default=str), encoding="utf-8")
        logger.info(f"Recorded conversation for {idea_slug}: score={interest_score}, preorder={preorder}")

    def _check_validations(self) -> str:
 
       """Check all validation records for status changes."""
        results = []
        for vf in self.validation_dir.glob("*.json"):
            try:
                validation = json.loads(vf.read_text(encoding="utf-8"))
                if validation.get("status") == "awaiting_conversations":
                    if self._validation_passed(validation):
                        validation["status"] = "validated"
                        validation["validated_at"] = now_iso()
                        vf.write_text(json.dumps(validation, indent=2, default=str), encoding="utf-8")
                        results.append(f"'{validation['idea']}' validated!")
            except (json.JSONDecodeError, IOError):
                continue

        return "; ".join(results)

    def _scaffold_validated(self) -> int:
        """Scaffold project structures for validated ideas."""
        scaffolded = 0

        for vf in self.validation_dir.glob("*.json"):
            try:
                validation = json.loads(vf.read_text(encoding="utf-8"))
                if validation.get("status") != "validated":
                    continue

                slug = validation.get("slug", "")
                project_dir = self.projects_dir / slug

                if project_dir.exists():
                    continue  # Already scaffolded

                # Constitutional audit before building
                audit = self.audit_action(
                    f"Scaffold micro-SaaS project: {validation.get('idea', 'unknown')}",
                    context={"type": "code_generation", "requires_approval": False}
                )
                if audit.overall_result == "FAIL":
                    logger.warning(f"Constitutional Court blocked scaffold: {validation.get('idea')}")
                    continue

                # Create project structure
                self._create_project_scaffold(project_dir, validation)
                scaffolded += 1
                logger.info(f"Scaffolded proje
ct: {slug}")

            except (json.JSONDecodeError, IOError) as e:
                logger.debug(f"Scaffold check error: {e}")

        return scaffolded

    def _create_project_scaffold(self, project_dir: Path, validation: dict):
        """Create a complete micro-SaaS project scaffold."""
        project_dir.mkdir(parents=True, exist_ok=True)
        idea = validation.get("idea", "micro-saas")
        slug = validation.get("slug", "project")

        # Determine deployment target
        deployment = self.get_stream_config().get("deployment", "oracle_cloud")

        # README
        readme = f"""# {idea.title()}

Micro-SaaS service built by The Institution.

## Status
- Validation: PASSED
- Conversations: {len(validation.get('conversations', []))}
- Preorders: {len(validation.get('preorders', []))}
- Interest Score: {sum(validation.get('interest_scores', [])) / max(len(validation.get('interest_scores', [])), 1):.1f}/10

## Architecture
- Runtime: Python 3.11 + Flask
- Database: SQLite (local) / Oracle Cloud (production)
- Deployment: {'Oracle Cloud Free Tier' if deployment == 'oracle_cloud' else 'Cloudflare Workers'}
- Auth: API key based

## Getting Started
```bash
cd {slug}
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python app.py