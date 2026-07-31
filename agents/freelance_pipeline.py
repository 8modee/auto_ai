#!/usr/bin/env python3
"""
═══════════════════════════════════════════════════════════════
THE INSTITUTION — STREAM 6: FREELANCE MICRO-TASKS
═══════════════════════════════════════════════════════════════
Autonomous freelance opportunity pipeline:
- Monitors Upwork, Fiverr, PeoplePerHour, Toptal
- Filters by skills match (technical, writing, automation)
- Drafts proposals (human approves ALL submissions)
- Tracks response rates, earnings, time investment
- Adjusts targeting based on success data
═══════════════════════════════════════════════════════════════
"""

import os
import json
import re
import hashlib
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import requests
from bs4 import BeautifulSoup

from common import get_db, get_config, get_logger, INSTITUTION_ROOT, now_iso, today_str
from agents.base import BaseAgent

logger = get_logger("freelance_pipeline")


class FreelancePipelineAgent(BaseAgent):
    AGENT_NAME = "freelance_pipeline"
    AGENT_TYPE = "revenue_stream"
    STREAM = "freelance"
    DEFAULT_INTERVAL_SECONDS = 21600  # Every 6 hours

    def __init__(self):
        super().__init__()
        self.freelance_dir = INSTITUTION_ROOT / "freelance"
        self.opportunities_dir = self.freelance_dir / "opportunities"
        self.proposals_dir = self.freelance_dir / "proposals"
        self.active_dir = self.freelance_dir / "active"
        for d in [self.opportunities_dir, self.proposals_dir, self.active_dir]:
            d.mkdir(parents=True, exist_ok=True)

        self._session = requests.Session()
        self._session.headers.update({
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        })

    def run_once(self):
        """Main cycle: scan platforms, filter, draft proposals, track responses."""
        if not self.should_run_today():
            return "Stream disabled or inactive"

        stream_cfg = self.get_
stream_config()
        platforms = stream_cfg.get("platforms", [])
        skills = stream_cfg.get("skills_match", [])
        min_budget = stream_cfg.get("min_budget", 50)
        max_proposals = stream_cfg.get("max_proposals_per_day", 3)

        results = []

        # Check daily proposal limit
        proposals_today = self.db.fetchone(
            "SELECT COUNT(*) as cnt FROM content_inventory WHERE stream = ? AND content_type = 'proposal' AND DATE(created_at) = ?",
            (self.STREAM, today_str())
        )
        current_proposals = proposals_today["cnt"] if proposals_today else 0

        if current_proposals >= max_proposals:
            # Still track responses
            self._track_responses()
            return f"Daily proposal limit reached ({current_proposals}/{max_proposals}). Tracking responses."

        # Phase 1: Scan platforms for opportunities
        opportunities = []
        for platform in platforms:
            platform_opps = self._scan_platform(platform, skills, min_budget)
            opportunities.extend(platform_opps)

        if opportunities:
            results.append(f"Found {len(opportunities)} matching opportunities")

        # Phase 2: Score and rank opportunities
        scored = self._score_opportunities(opportunities, skills)

        # Phase 3: Draft proposals for top opportunities
        remaining_slots = max_proposals - current_proposals
        drafted = self._draft_proposals(scored[:remaining_slots])
        if drafted:
            results.append(f"Drafted {len(drafted)} proposals (pending approval)")

        # Phase 4: Track response rates
        self._track_responses()

        # Phase 5: Adjust targeting based on performance
        self._adjust_targeting()

        return "; ".join(results) if results else "No matching opportunities this cycle"

    def _scan_platform(self, platform: str, skills: list, min_budget: float) -> list:
        """Scan a freelance platform for matching opportunities."""
      
  if platform == "upwork":
            return self._scan_upwork(skills, min_budget)
        elif platform == "fiverr":
            return self._scan_fiverr(skills, min_budget)
        elif platform == "peopleperhour":
            return self._scan_peopleperhour(skills, min_budget)
        elif platform == "toptal":
            return self._scan_toptal(skills, min_budget)
        else:
            logger.warning(f"Unknown platform: {platform}")
            return []

    def _scan_upwork(self, skills: list, min_budget: float) -> list:
        """Scan Upwork for matching jobs via RSS/public feed."""
        opportunities = []

        # Upwork has a public RSS feed for job searches
        skill_query = "+OR+".join(s.replace(" ", "+") for s in skills[:3])
        rss_url = f"https://www.upwork.com/ab/feed/jobs/rss?q={skill_query}&sort=recency"

        try:
            resp = self._session.get(rss_url, timeout=30)
            if resp.status_code != 200:
                logger.debug(f"Upwork RSS returned {resp.status_code}")
                return opportunities

            soup = BeautifulSoup(resp.text, "lxml-xml")
            items = soup.find_all("item")

            for item in items[:15]:
                title = item.find("title")
                description = item.find("description")
                link = item.find("link")
                pub_date = item.find("pubDate")

                if not title:
                    continue

                title_text = title.get_text(strip=True)
                desc_text = description.get_text(strip=True) if description else ""
                link_text = link.get_text(strip=True) if link else ""

                # Extract budget
                budget = self._extract_budget(desc_text)
                if budget and budget < min_budget:
                    continue

                # Check skills match
                match_score = self._calculate_skill_match(f"{title_text} {desc_text}", skills)
                if match_sc
ore < 0.3:
                    continue

                opp_hash = hashlib.md5(link_text.encode()).hexdigest()

                opportunities.append({
                    "title": title_text,
                    "description": desc_text[:500],
                    "url": link_text,
                    "platform": "upwork",
                    "budget": budget,
                    "budget_type": "fixed" if budget else "hourly",
                    "skill_match": match_score,
                    "posted_at": pub_date.get_text(strip=True) if pub_date else now_iso(),
                    "hash": opp_hash,
                })

        except Exception as e:
            logger.debug(f"Upwork scan error: {e}")

        return opportunities

    def _scan_fiverr(self, skills: list, min_budget: float) -> list:
        """Scan Fiverr for buyer requests (limited public access)."""
        opportunities = []

        # Fiverr buyer requests are not publicly accessible via API without auth
        # Log that we'd need API access
        api_key = os.environ.get("FIVERR_API_KEY")
        if not api_key:
            logger.debug("No Fiverr API key. Skipping Fiverr scan.")
            return opportunities

        try:
            resp = self._session.get(
                "https://api.fiverr.com/v2/buyer_requests",
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=30,
            )
            if resp.status_code == 200:
                data = resp.json()
                requests_list = data.get("buyer_requests", [])
                for req in requests_list[:10]:
                    title = req.get("title", "")
                    desc = req.get("description", "")
                    budget = req.get("budget", {}).get("amount", 0)

                    if budget and budget < min_budget:
                        continue

                    match_score = self._calculate_skill_match(f"{title} {desc}", skills)
                    if match_score < 0.3
:
                        continue

                    opportunities.append({
                        "title": title,
                        "description": desc[:500],
                        "url": req.get("url", ""),
                        "platform": "fiverr",
                        "budget": budget,
                        "budget_type": "fixed",
                        "skill_match": match_score,
                        "posted_at": req.get("created_at", now_iso()),
                        "hash": hashlib.md5(req.get("id", title).encode()).hexdigest(),
                    })
        except Exception as e:
            logger.debug(f"Fiverr scan error: {e}")

        return opportunities

    def _scan_peopleperhour(self, skills: list, min_budget: float) -> list:
        """Scan PeoplePerHour for projects."""
        opportunities = []

        try:
            # PeoplePerHour has a public projects feed
            resp = self._session.get(
                "https://www.peopleperhour.com/freelance-jobs",
                timeout=30,
            )
            if resp.status_code != 200:
                return opportunities

            soup = BeautifulSoup(resp.text, "lxml")
            job_cards = soup.select(".job-listing, .project-card, .job-item, article")

            for card in job_cards[:15]:
                title_el = card.select_one("h2, h3, .title, a.title")
                desc_el = card.select_one(".description, .summary, p")
                link_el = card.select_one("a[href*='/freelance-jobs/']")
                budget_el = card.select_one(".budget, .price, .rate")

                if not title_el:
                    continue

                title = title_el.get_text(strip=True)
                desc = desc_el.get_text(strip=True) if desc_el else ""
                link = ""
                if link_el:
                    href = link_el.get("href", "")
                    link = f"https://www.peopleperhour.com{href}" if href.startswith("/") else h
ref

                budget_text = budget_el.get_text(strip=True) if budget_el else ""
                budget = self._extract_budget(budget_text)

                if budget and budget < min_budget:
                    continue

                match_score = self._calculate_skill_match(f"{title} {desc}", skills)
                if match_score < 0.3:
                    continue

                opportunities.append({
                    "title": title,
                    "description": desc[:500],
                    "url": link,
                    "platform": "peopleperhour",
                    "budget": budget,
                    "budget_type": "fixed" if budget else "hourly",
                    "skill_match": match_score,
                    "posted_at": now_iso(),
                    "hash": hashlib.md5((link or title).encode()).hexdigest(),
                })

        except Exception as e:
            logger.debug(f"PeoplePerHour scan error: {e}")

        return opportunities

    def _scan_toptal(self, skills: list, min_budget: float) -> list:
        """Scan Toptal (limited public access, mostly for awareness)."""
        # Toptal is invitation-only and doesn't have public job listings
        # Log for future reference
        logger.debug("Toptal has no public job feed. Skipping.")
        return []

    def _extract_budget(self, text: str) -> Optional[float]:
        """Extract budget amount from text."""
        patterns = [
            r'\$([0-9,]+(?:\.[0-9]{2})?)\s*(?:-\s*\$([0-9,]+))?',
            r'(?:budget|price|rate|pay)[\s:]*\$?([0-9,]+)',
            r'([0-9,]+)\s*(?:aud|usd|per hour|/hr|/hour)',
        ]
        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                try:
                    return float(match.group(1).replace(",", ""))
                except (ValueError, IndexError):
                    continue
        return None

    def _calculate_skill_match(self, t
ext: str, skills: list) -> float:
        """Calculate how well a job matches our skills (0.0 to 1.0)."""
        text_lower = text.lower()
        matches = 0
        for skill in skills:
            skill_lower = skill.lower()
            # Check for skill or related terms
            if skill_lower in text_lower:
                matches += 1
            else:
                # Check partial matches
                skill_words = skill_lower.split()
                if any(w in text_lower for w in skill_words if len(w) > 3):
                    matches += 0.5

        return matches / max(len(skills), 1)

    def _score_opportunities(self, opportunities: list, skills: list) -> list:
        """Score and rank opportunities."""
        if not opportunities:
            return []

        scored = []
        for opp in opportunities:
            # Check if already seen
            existing = self.db.fetchone(
                "SELECT id FROM content_inventory WHERE stream = ? AND slug = ?",
                (self.STREAM, opp["hash"])
            )
            if existing:
                continue

            # Composite score
            skill_score = opp.get("skill_match", 0) * 40  # 0-40 points
            budget = opp.get("budget") or 100
            budget_score = min(30, (budget / 500) * 30)  # 0-30 points
            platform_score = {"upwork": 15, "peopleperhour": 12, "fiverr": 10, "toptal": 20}.get(opp.get("platform", ""), 5)
            recency_score = 15  # Assume recent since we just scraped

            total = skill_score + budget_score + platform_score + recency_score
            opp["total_score"] = round(total, 1)
            scored.append(opp)

        scored.sort(key=lambda o: o["total_score"], reverse=True)

        # Store discovered opportunities
        for opp in scored:
            self.db.insert("content_inventory", {
                "stream": self.STREAM,
                "content_type": "opportunity",
                "title": opp["title"],
    
            "slug": opp["hash"],
                "status": "draft",
                "metrics": json.dumps({
                    "platform": opp.get("platform"),
                    "budget": opp.get("budget"),
                    "skill_match": opp.get("skill_match"),
                    "total_score": opp.get("total_score"),
                }),
            })

            # Save to opportunities directory
            opp_file = self.opportunities_dir / f"{opp['hash']}.json"
            opp_file.write_text(json.dumps(opp, indent=2, default=str), encoding="utf-8")

        return scored

    def _draft_proposals(self, opportunities: list) -> list:
        """Draft proposals for top opportunities. ALL require founder approval."""
        drafted = []

        for opp in opportunities:
            # Constitutional audit — ALL proposals require approval
            audit = self.audit_action(
                f"Draft freelance proposal for: {opp['title']} on {opp.get('platform', 'unknown')}",
                context={"requires_approval": True, "type": "freelance_proposal"}
            )

            if audit.overall_result == "FAIL":
                logger.warning(f"Constitutional Court blocked proposal: {opp['title']}")
                continue

            # Generate proposal
            proposal = self._generate_proposal(opp)
            if not proposal:
                continue

            # Save proposal
            proposal_path = self.proposals_dir / f"{opp['hash']}_proposal.md"
            proposal_path.write_text(proposal, encoding="utf-8")

            # Create approval request — NEVER auto-submit
            approval_id = self.db.add_approval(
                item_type="freelance_proposal",
                description=f"Review and approve proposal: {opp['title'][:60]} ({opp.get('platform', 'unknown')})",
                details=json.dumps({
                    "opportunity_title": opp["title"],
                    "platform": opp.get("platform"),
             
       "budget": opp.get("budget"),
                    "url": opp.get("url"),
                    "proposal_path": str(proposal_path),
                    "skill_match": opp.get("skill_match"),
                }, default=str),
                stream=self.STREAM,
                agent=self.AGENT_NAME,
                priority=2,
            )

            # Record proposal
            self.db.insert("content_inventory", {
                "stream": self.STREAM,
                "content_type": "proposal",
                "title": f"Proposal: {opp['title'][:80]}",
                "slug": f"{opp['hash']}_proposal",
                "status": "draft",
                "metrics": json.dumps({
                    "platform": opp.get("platform"),
                    "approval_id": approval_id,
                    "opportunity_hash": opp["hash"],
                }),
            })

            drafted.append({
                "title": opp["title"],
                "platform": opp.get("platform"),
                "proposal_path": str(proposal_path),
                "approval_id": approval_id,
            })

            logger.info(f"Drafted proposal for: {opp['title']} (approval #{approval_id})")

        return drafted

    def _generate_proposal(self, opp: dict) -> Optional[str]:
        """Generate a freelance proposal using AI."""
        stream_cfg = self.get_stream_config()
        template_style = stream_cfg.get("proposal_template", "professional_concise")

        prompt = f"""Write a freelance proposal for this opportunity.

OPPORTUNITY:
Title: {opp.get('title', 'Unknown')}
Description: {opp.get('description', 'No description')}
Platform: {opp.get('platform', 'unknown')}
Budget: ${opp.get('budget', 'Not specified')}
URL: {opp.get('url', '')}

APPLICANT PROFILE:
- Technical specialist: Python automation, Linux administration, AI integration
- Strong technical writing and documentation skills
- Experience with homelab infrastructure, data analysis, system design
- Based
 in Australia (AEST timezone)
- Available for remote work
- Prefers fixed-scope projects with clear deliverables

STYLE: {template_style}

REQUIREMENTS:
- Professional but personable tone
- Address the client's specific needs (reference their description)
- Demonstrate understanding of the problem
- Propose a clear approach with deliverables
- Include realistic timeline
- State pricing clearly (fixed price preferred)
- Keep under 300 words
- Do NOT overpromise or fabricate experience
- Do NOT use generic filler ("I am a hard worker...")
- Mark any claims that need founder verification with [VERIFY]

OUTPUT FORMAT:
# Proposal: [Opportunity Title]

## Understanding
[Show you understand their problem — 2-3 sentences]

## Approach
[How you'll solve it — 3-5 bullet points]

## Deliverables
[What they'll receive — numbered list]

## Timeline
[Realistic estimate]

## Investment
[Clear pricing]

## Why Me
[2-3 specific, honest reasons]

---
*DRAFT — Requires founder review before sending*
"""
        response = self.generate_text(
            prompt=prompt,
            quality_tier="routine",
            temperature=0.6,
            max_tokens=1500,
        )

        return response

    def _track_responses(self):
        """Track response rates for sent proposals."""
        # Get all proposals with status tracking
        proposals = self.db.fetchall(
            "SELECT * FROM content_inventory WHERE stream = ? AND content_type = 'proposal' AND status != 'draft' ORDER BY created_at DESC LIMIT 50",
            (self.STREAM,)
        )

        total_sent = 0
        total_responses = 0
        total_awarded = 0

        for prop in proposals:
            metrics = {}
            try:
                metrics = json.loads(prop.get("metrics", "{}"))
            except (json.JSONDecodeError, TypeError):
                pass

            status = prop.get("status", "")
            if status in ("published", "submitted"):
                total_sent += 1
            if metrics
.get("responded"):
                total_responses += 1
            if metrics.get("awarded"):
                total_awarded += 1

        # Calculate rates
        response_rate = (total_responses / max(total_sent, 1)) * 100
        award_rate = (total_awarded / max(total_responses, 1)) * 100

        # Log metrics
        if total_sent > 0:
            logger.info(
                f"Freelance metrics: {total_sent} sent, {total_responses} responses "
                f"({response_rate:.0f}%), {total_awarded} awarded ({award_rate:.0f}%)"
            )

        # Check kill criterion
        stream = self.db.get_stream(self.STREAM)
        if stream and stream.get("kill_metric") == "response_rate":
            threshold = stream.get("kill_threshold", 5)
            if total_sent >= 10 and response_rate < threshold:
                logger.warning(
                    f"Response rate {response_rate:.1f}% below kill threshold {threshold}%. "
                    f"Consider adjusting targeting."
                )
                self.log_learning(
                    prediction=f"Response rate would exceed {threshold}%",
                    outcome=f"Actual response rate: {response_rate:.1f}% after {total_sent} proposals",
                    lesson=f"Current targeting yields {response_rate:.1f}% response rate. "
                           f"{'Adjust skills targeting or proposal style.' if response_rate < threshold else 'Strategy working.'}",
                    confidence=70,
                    tags=["freelance", "response_rate", "targeting"],
                )

    def _adjust_targeting(self):
        """Adjust targeting based on historical performance."""
        # Get lessons for this stream
        lessons = self.get_lessons_for_stream(limit=5)

        for lesson in lessons:
            lesson_text = lesson.get("lesson", "").lower()

            # If lessons indicate poor targeting, log adjustment
            if "response rate" in lesson_text and "low" in lesson_tex
t:
                # Could adjust skill keywords or platform focus
                self.db.insert("decisions", {
                    "description": "Freelance targeting adjustment",
                    "decision": "REVIEW_TARGETING",
                    "reasoning": f"Lesson indicates low response rate: {lesson_text[:200]}",
                    "agent": self.AGENT_NAME,
                    "stream": self.STREAM,
                })

    def record_response(self, opportunity_hash: str, responded: bool, awarded: bool = False,
                        amount: float = 0):
        """Record a response to a proposal."""
        self.db.execute(
            """UPDATE content_inventory SET metrics = json_set(
                COALESCE(metrics, '{}'), '$.responded', ?, '$.awarded', ?, '$.amount', ?
            ) WHERE stream = ? AND slug LIKE ?""",
            (1 if responded else 0, 1 if awarded else 0, amount,
             self.STREAM, f"%{opportunity_hash}%")
        )

        if awarded and amount > 0:
            self.record_revenue("freelance", amount, f"Freelance project awarded")