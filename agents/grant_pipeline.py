#!/usr/bin/env python3
"""
═══════════════════════════════════════════════════════════════
THE INSTITUTION — STREAM 5: GRANT & REBATE PIPELINE
═══════════════════════════════════════════════════════════════
Autonomous grant discovery and application pipeline:
- Scrapes business.gov.au, state portals, energy rebates,
  disability support programs, tech innovation grants
- Filters by eligibility profile
- Scores: effort vs return vs deadline
- Drafts applications via LLM (human approves ALL submissions)
- Tracks status, follow-up reminders
- Records outcomes in Reflection Database
═══════════════════════════════════════════════════════════════
"""

import os
import json
import re
import hashlib
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from common import get_db, get_config, get_logger, INSTITUTION_ROOT, now_iso, today_str, slugify
from agents.base import BaseAgent

logger = get_logger("grant_pipeline")


class GrantPipelineAgent(BaseAgent):
    AGENT_NAME = "grant_pipeline"
    AGENT_TYPE = "revenue_stream"
    STREAM = "grants"
    DEFAULT_INTERVAL_SECONDS = 43200  # Every 12 hours

    def __init__(self):
        super().__init__()
        self.grants_dir = INSTITUTION_ROOT / "grants"
        self.discovered_dir = self.grants_dir / "discovered"
        self.drafts_dir = self.grants_dir / "drafts"
        self.submitted_dir = self.grants_dir / "submitted"
        self.awarded_dir = self.grants_dir / "awarded"
        for d in [self.discovered_dir, self.drafts_dir, self.submitted_dir, self.awarded_dir]:
            d.mkdir(parents=True, exist_ok=True)

        self._session = requests.Session()
        self._session.headers.update({
            "User-Agent": "Mozilla/5.0 (compatible; InstitutionBot/1.0; +https://institution.local)"
        })

    def run_once(self):
        """Main cycle: discover grants, score, draft applications, trac
k status."""
        if not self.should_run_today():
            return "Stream disabled or inactive"

        stream_cfg = self.get_stream_config()
        sources = stream_cfg.get("sources", [])
        eligibility = stream_cfg.get("eligibility_profile", {})
        scoring_weights = stream_cfg.get("scoring_weights", {})

        results = []

        # Phase 1: Discover new grants
        new_grants = self._discover_grants(sources)
        if new_grants:
            results.append(f"Discovered {len(new_grants)} new grants")

        # Phase 2: Filter by eligibility
        eligible_grants = self._filter_eligible(new_grants, eligibility)
        if eligible_grants:
            results.append(f"{len(eligible_grants)} pass eligibility filter")

        # Phase 3: Score and rank
        scored_grants = self._score_grants(eligible_grants, scoring_weights)
        if scored_grants:
            results.append(f"Scored {len(scored_grants)} grants")

        # Phase 4: Draft applications for top opportunities
        drafted = self._draft_applications(scored_grants[:3])
        if drafted:
            results.append(f"Drafted {len(drafted)} applications")

        # Phase 5: Track submitted applications
        self._track_submitted()

        # Phase 6: Check for follow-up reminders
        self._check_followups()

        return "; ".join(results) if results else "No new grants found this cycle"

    def _discover_grants(self, sources: list) -> list:
        """Scrape grant sources for new opportunities."""
        all_grants = []

        for source in sources:
            url = source.get("url", "")
            source_type = source.get("type", "general")

            if not url:
                continue

            try:
                grants = self._scrape_source(url, source_type)
                all_grants.extend(grants)
                logger.info(f"Scraped {len(grants)} grants from {source_type}: {url}")
            except Exception as e:
                logger.w
arning(f"Failed to scrape {url}: {e}")
                self.db.log_incident("warning", self.AGENT_NAME, f"Scrape failed for {url}: {str(e)[:200]}")

        # Deduplicate by URL hash
        seen_hashes = set()
        unique_grants = []
        for grant in all_grants:
            grant_hash = hashlib.md5(grant.get("url", grant.get("title", "")).encode()).hexdigest()
            if grant_hash not in seen_hashes:
                seen_hashes.add(grant_hash)
                # Check if already in database
                existing = self.db.fetchone(
                    "SELECT id FROM content_inventory WHERE stream = ? AND slug = ?",
                    (self.STREAM, grant_hash)
                )
                if not existing:
                    unique_grants.append(grant)

        return unique_grants

    def _scrape_source(self, url: str, source_type: str) -> list:
        """Scrape a single grant source."""
        grants = []

        try:
            resp = self._session.get(url, timeout=30)
            resp.raise_for_status()
        except requests.RequestException as e:
            logger.warning(f"HTTP error for {url}: {e}")
            return grants

        soup = BeautifulSoup(resp.text, "lxml")

        if "business.gov.au" in url:
            grants = self._parse_business_gov(soup, url)
        elif "vic.gov.au" in url or "nsw.gov.au" in url or "qld.gov.au" in url:
            grants = self._parse_state_portal(soup, url, source_type)
        elif "energy.gov.au" in url:
            grants = self._parse_energy_rebates(soup, url)
        elif "servicesaustralia" in url:
            grants = self._parse_services_australia(soup, url)
        else:
            grants = self._parse_generic(soup, url, source_type)

        return grants

    def _parse_business_gov(self, soup: BeautifulSoup, base_url: str) -> list:
        """Parse business.gov.au grants page."""
        grants = []

        # Look for grant listing elements
        grant_cards = soup.select
(".card, .grant-item, .search-result, article, .list-item")
        if not grant_cards:
            # Try broader selectors
            grant_cards = soup.select("a[href*='grant'], a[href*='program']")

        for card in grant_cards[:20]:
            title_el = card.select_one("h2, h3, h4, .title, .card-title, a")
            desc_el = card.select_one("p, .description, .summary, .card-text")
            link_el = card.select_one("a[href]")

            if not title_el:
                continue

            title = title_el.get_text(strip=True)
            if len(title) < 10:
                continue

            description = desc_el.get_text(strip=True) if desc_el else ""
            link = ""
            if link_el:
                href = link_el.get("href", "")
                link = urljoin(base_url, href) if href else ""

            # Extract deadline if present
            deadline = self._extract_deadline(card.get_text())

            # Extract amount if present
            amount = self._extract_amount(card.get_text())

            grants.append({
                "title": title,
                "description": description[:500],
                "url": link,
                "source": "business.gov.au",
                "type": "federal",
                "deadline": deadline,
                "amount": amount,
                "discovered_at": now_iso(),
            })

        return grants

    def _parse_state_portal(self, soup: BeautifulSoup, base_url: str, source_type: str) -> list:
        """Parse state government grant portals."""
        grants = []

        cards = soup.select(".card, .grant-item, .program-item, article, .list-group-item, li a")

        for card in cards[:15]:
            title_el = card.select_one("h2, h3, h4, .title, a")
            if not title_el:
                if card.name == "a":
                    title_el = card
                else:
                    continue

            title = title_el.get_text(strip=True)
          
  if len(title) < 10 or "grant" not in title.lower() and "program" not in title.lower() and "rebate" not in title.lower():
                continue

            link_el = card.select_one("a[href]") or (card if card.name == "a" else None)
            link = ""
            if link_el:
                href = link_el.get("href", "")
                link = urljoin(base_url, href) if href else ""

            desc_el = card.select_one("p, .description")
            description = desc_el.get_text(strip=True) if desc_el else ""

            grants.append({
                "title": title,
                "description": description[:500],
                "url": link,
                "source": base_url,
                "type": source_type,
                "deadline": self._extract_deadline(card.get_text()),
                "amount": self._extract_amount(card.get_text()),
                "discovered_at": now_iso(),
            })

        return grants

    def _parse_energy_rebates(self, soup: BeautifulSoup, base_url: str) -> list:
        """Parse energy rebate pages."""
        grants = []

        cards = soup.select(".rebate, .card, .program, article, .list-item")

        for card in cards[:15]:
            title_el = card.select_one("h2, h3, h4, .title, a")
            if not title_el:
                continue

            title = title_el.get_text(strip=True)
            if len(title) < 8:
                continue

            link_el = card.select_one("a[href]")
            link = urljoin(base_url, link_el.get("href", "")) if link_el else ""

            desc_el = card.select_one("p, .description")
            description = desc_el.get_text(strip=True) if desc_el else ""

            grants.append({
                "title": title,
                "description": description[:500],
                "url": link,
                "source": "energy.gov.au",
                "type": "energy",
                "deadline": self._extract_deadline(card.get_text()),
                "
amount": self._extract_amount(card.get_text()),
                "discovered_at": now_iso(),
            })

        return grants

    def _parse_services_australia(self, soup: BeautifulSoup, base_url: str) -> list:
        """Parse Services Australia / disability support pages."""
        grants = []

        cards = soup.select(".card, .program, article, .list-item, .search-result")

        for card in cards[:15]:
            title_el = card.select_one("h2, h3, h4, .title, a")
            if not title_el:
                continue

            title = title_el.get_text(strip=True)
            if len(title) < 8:
                continue

            link_el = card.select_one("a[href]")
            link = urljoin(base_url, link_el.get("href", "")) if link_el else ""

            desc_el = card.select_one("p, .description")
            description = desc_el.get_text(strip=True) if desc_el else ""

            grants.append({
                "title": title,
                "description": description[:500],
                "url": link,
                "source": "servicesaustralia.gov.au",
                "type": "disability_support",
                "deadline": self._extract_deadline(card.get_text()),
                "amount": self._extract_amount(card.get_text()),
                "discovered_at": now_iso(),
            })

        return grants

    def _parse_generic(self, soup: BeautifulSoup, base_url: str, source_type: str) -> list:
        """Generic parser for unknown grant sources."""
        grants = []

        # Look for any links containing grant-related keywords
        links = soup.select("a[href]")
        grant_keywords = ["grant", "funding", "rebate", "subsidy", "program", "support", "award"]

        for link in links[:50]:
            text = link.get_text(strip=True).lower()
            href = link.get("href", "")

            if not any(kw in text for kw in grant_keywords):
                continue
            if len(text) < 15:
                conti
nue

            full_url = urljoin(base_url, href) if href else ""

            grants.append({
                "title": link.get_text(strip=True),
                "description": "",
                "url": full_url,
                "source": base_url,
                "type": source_type,
                "deadline": None,
                "amount": None,
                "discovered_at": now_iso(),
            })

        return grants[:10]

    def _extract_deadline(self, text: str) -> Optional[str]:
        """Extract deadline date from text."""
        patterns = [
            r'(?:close|deadline|due|apply by|closes?)[\s:]*([0-9]{1,2}\s+(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\s+[0-9]{4})',
            r'(?:close|deadline|due|apply by|closes?)[\s:]*([0-9]{4}-[0-9]{2}-[0-9]{2})',
            r'([0-9]{1,2}/[0-9]{1,2}/[0-9]{4})',
        ]
        text_lower = text.lower()
        for pattern in patterns:
            match = re.search(pattern, text_lower, re.IGNORECASE)
            if match:
                return match.group(1)
        return None

    def _extract_amount(self, text: str) -> Optional[float]:
        """Extract grant amount from text."""
        patterns = [
            r'\$([0-9,]+(?:\.[0-9]{2})?)\s*(?:million|m\b)',
            r'\$([0-9,]+(?:\.[0-9]{2})?)',
            r'up to \$([0-9,]+)',
            r'([0-9,]+)\s*(?:dollars|aud)',
        ]
        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                amount_str = match.group(1).replace(",", "")
                try:
                    amount = float(amount_str)
                    if "million" in text.lower() or "m " in text.lower():
                        amount *= 1_000_000
                    return amount
                except ValueError:
                    continue
        return None

    def _filter_eligible(self, grants: list, eligibility: dict) -> list:
        """Filter grants by eligibility profi
le."""
        if not grants:
            return []

        eligible = []
        location = eligibility.get("location", "Australia").lower()
        state = eligibility.get("state", "").lower()
        has_disability = eligibility.get("disability", False)
        small_business = eligibility.get("small_business", False)
        tech_sector = eligibility.get("tech_sector", False)

        for grant in grants:
            text = f"{grant.get('title', '')} {grant.get('description', '')}".lower()
            score = 0
            reasons = []

            # Location check
            if location and location in text:
                score += 1
            elif "australia" in text or "national" in text:
                score += 1

            # State check
            if state and state in text:
                score += 1
            elif not state:
                score += 1  # No state restriction

            # Disability relevance
            if has_disability:
                disability_keywords = ["disability", "accessible", "inclusion", "support", "ndis", "carer"]
                if any(kw in text for kw in disability_keywords):
                    score += 2
                    reasons.append("disability_relevant")

            # Small business relevance
            if small_business:
                sb_keywords = ["small business", "sole trader", "startup", "micro", "sme", "entrepreneur"]
                if any(kw in text for kw in sb_keywords):
                    score += 2
                    reasons.append("small_business_relevant")

            # Tech sector relevance
            if tech_sector:
                tech_keywords = ["technology", "digital", "innovation", "tech", "software", "ai", "automation", "data"]
                if any(kw in text for kw in tech_keywords):
                    score += 2
                    reasons.append("tech_relevant")

            # Energy relevance (always relevant for cost savings)
            energy_keywords = ["ener
gy", "solar", "efficiency", "renewable", "sustainability"]
            if any(kw in text for kw in energy_keywords):
                score += 1
                reasons.append("energy_relevant")

            # Must have minimum relevance
            if score >= 2:
                grant["eligibility_score"] = score
                grant["eligibility_reasons"] = reasons
                eligible.append(grant)

        return eligible

    def _score_grants(self, grants: list, weights: dict) -> list:
        """Score grants by effort vs return vs deadline urgency."""
        if not grants:
            return []

        w_value = weights.get("value", 0.35)
        w_effort = weights.get("effort", 0.25)
        w_deadline = weights.get("deadline_urgency", 0.20)
        w_eligibility = weights.get("eligibility_confidence", 0.20)

        scored = []
        for grant in grants:
            # Value score (0-10)
            amount = grant.get("amount") or 5000  # Default assumption
            if amount >= 50000:
                value_score = 10
            elif amount >= 20000:
                value_score = 8
            elif amount >= 10000:
                value_score = 7
            elif amount >= 5000:
                value_score = 6
            elif amount >= 1000:
                value_score = 5
            else:
                value_score = 3

            # Effort score (0-10, higher = less effort = better)
            desc_len = len(grant.get("description", ""))
            if desc_len < 100:
                effort_score = 8  # Simple
            elif desc_len < 300:
                effort_score = 6  # Moderate
            else:
                effort_score = 4  # Complex

            # Adjust for type
            grant_type = grant.get("type", "")
            if grant_type == "energy":
                effort_score = min(10, effort_score + 2)  # Rebates are usually simple
            elif grant_type == "disability_support":
                effort_score = min(10, ef
fort_score + 1)

            # Deadline urgency (0-10, higher = more urgent = higher priority)
            deadline = grant.get("deadline")
            if deadline:
                try:
                    # Try to parse deadline
                    deadline_dt = self._parse_date(deadline)
                    if deadline_dt:
                        days_until = (deadline_dt - datetime.now()).days
                        if days_until < 0:
                            deadline_score = 0  # Already passed
                        elif days_until < 7:
                            deadline_score = 10  # Urgent
                        elif days_until < 14:
                            deadline_score = 8
                        elif days_until < 30:
                            deadline_score = 6
                        elif days_until < 60:
                            deadline_score = 4
                        else:
                            deadline_score = 2
                    else:
                        deadline_score = 5  # Unknown deadline
                except (ValueError, TypeError):
                    deadline_score = 5
            else:
                deadline_score = 5  # No deadline = ongoing

            # Eligibility confidence (0-10)
            elig_score = grant.get("eligibility_score", 0)
            eligibility_confidence = min(10, elig_score * 2)

            # Weighted total
            total_score = (
                value_score * w_value +
                effort_score * w_effort +
                deadline_score * w_deadline +
                eligibility_confidence * w_eligibility
            )

            grant["score"] = round(total_score, 2)
            grant["score_breakdown"] = {
                "value": value_score,
                "effort": effort_score,
                "deadline": deadline_score,
                "eligibility": eligibility_confidence,
            }
            scored.append(grant)

        # Sort by score descending
      
  scored.sort(key=lambda g: g["score"], reverse=True)

        # Store discovered grants
        for grant in scored:
            grant_hash = hashlib.md5(grant.get("url", grant.get("title", "")).encode()).hexdigest()
            self.db.insert("content_inventory", {
                "stream": self.STREAM,
                "content_type": "grant_discovered",
                "title": grant["title"],
                "slug": grant_hash,
                "status": "draft",
                "metrics": json.dumps({
                    "score": grant["score"],
                    "amount": grant.get("amount"),
                    "type": grant.get("type"),
                    "source": grant.get("source"),
                }),
            })

            # Save to discovered directory
            grant_file = self.discovered_dir / f"{grant_hash}.json"
            grant_file.write_text(json.dumps(grant, indent=2, default=str), encoding="utf-8")

        return scored

    def _parse_date(self, date_str: str) -> Optional[datetime]:
        """Parse various date formats."""
        formats = [
            "%Y-%m-%d",
            "%d/%m/%Y",
            "%d %B %Y",
            "%d %b %Y",
            "%B %d, %Y",
            "%b %d, %Y",
        ]
        for fmt in formats:
            try:
                return datetime.strptime(date_str.strip(), fmt)
            except ValueError:
                continue
        return None

    def _draft_applications(self, grants: list) -> list:
        """Draft grant applications using AI. All require founder approval."""
        drafted = []

        for grant in grants:
            # Check if already drafted
            grant_hash = hashlib.md5(grant.get("url", grant.get("title", "")).encode()).hexdigest()
            existing_draft = self.drafts_dir / f"{grant_hash}_draft.md"
            if existing_draft.exists():
                continue

            # Constitutional audit — ALL grant submissions require approval
            audit = se
lf.audit_action(
                f"Draft grant application for: {grant['title']}",
                context={"requires_approval": True, "type": "grant_draft"}
            )

            if audit.overall_result == "FAIL":
                logger.warning(f"Constitutional Court blocked grant draft: {grant['title']}")
                continue

            # Generate draft
            draft = self._generate_draft(grant)
            if not draft:
                continue

            # Save draft
            draft_path = self.drafts_dir / f"{grant_hash}_draft.md"
            draft_path.write_text(draft, encoding="utf-8")

            # Create approval request — NEVER auto-submit
            approval_id = self.db.add_approval(
                item_type="grant_submission",
                description=f"Review and approve grant application: {grant['title']}",
                details=json.dumps({
                    "grant_title": grant["title"],
                    "grant_url": grant.get("url", ""),
                    "amount": grant.get("amount"),
                    "score": grant.get("score"),
                    "draft_path": str(draft_path),
                    "deadline": grant.get("deadline"),
                }, default=str),
                stream=self.STREAM,
                agent=self.AGENT_NAME,
                priority=1 if grant.get("score", 0) > 7 else 2,
            )

            drafted.append({
                "title": grant["title"],
                "draft_path": str(draft_path),
                "approval_id": approval_id,
            })

            logger.info(f"Drafted grant application: {grant['title']} (approval #{approval_id})")

        return drafted

    def _generate_draft(self, grant: dict) -> Optional[str]:
        """Generate a grant application draft using AI."""
        prompt = f"""Draft a grant application for the following opportunity.

GRANT TITLE: {grant.get('title', 'Unknown')}
DESCRIPTION: {grant.get('description', 'No description avai
lable')}
SOURCE: {grant.get('source', 'Unknown')}
TYPE: {grant.get('type', 'general')}
AMOUNT: ${grant.get('amount', 'Unknown')}
DEADLINE: {grant.get('deadline', 'Not specified')}

APPLICANT PROFILE:
- Individual with disability (spinal injury)
- Small technology business (sole trader)
- Based in Victoria, Australia
- Focus: AI automation, technical services, digital infrastructure
- Limited physical capacity but high technical expertise

REQUIREMENTS:
- Professional, concise writing
- Australian English
- Address selection criteria explicitly
- Demonstrate genuine need and capability
- Include measurable outcomes
- Keep under 1000 words
- Mark sections that need founder's personal details with [FOUNDER TO COMPLETE]
- Do NOT fabricate qualifications, history, or claims
- Be honest about limitations while emphasising strengths

OUTPUT FORMAT:
# Grant Application: [Title]

## Project Summary
[2-3 sentence overview]

## Objectives
[Numbered list of specific, measurable objectives]

## Methodology
[How the project will be delivered]

## Expected Outcomes
[Measurable outcomes with timelines]

## Budget Overview
[High-level budget breakdown]

## Applicant Capability
[Why this applicant can deliver — emphasise technical expertise]

## Supporting Statement
[Why this funding matters — genuine, not manipulative]

---
*DRAFT — Requires founder review and approval before submission*
*Generated by The Institution's Grant Pipeline Agent*
"""
        response = self.generate_text(
            prompt=prompt,
            quality_tier="critical",
            temperature=0.5,
            max_tokens=3000,
        )

        return response

    def _track_submitted(self):
        """Track status of submitted applications."""
        submitted_files = list(self.submitted_dir.glob("*.json"))

        for sf in submitted_files:
            try:
                data = json.loads(sf.read_text(encoding="utf-8"))
                status = data.get("status", "submitted")
                submitt
ed_date = data.get("submitted_at", "")

                # Check if follow-up is needed (30 days without update)
                if status == "submitted" and submitted_date:
                    try:
                        sub_dt = datetime.fromisoformat(submitted_date)
                        days_since = (datetime.now() - sub_dt).days
                        if days_since > 30 and not data.get("followup_sent"):
                            # Create follow-up reminder
                            self.db.add_approval(
                                item_type="grant_followup",
                                description=f"Follow up on grant: {data.get('title', 'Unknown')} (submitted {days_since} days ago)",
                                details=json.dumps(data, default=str),
                                stream=self.STREAM,
                                agent=self.AGENT_NAME,
                                priority=3,
                            )
                            data["followup_sent"] = True
                            sf.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
                    except (ValueError, TypeError):
                        pass

            except (json.JSONDecodeError, IOError):
                continue

    def _check_followups(self):
        """Check for upcoming deadlines and create reminders."""
        discovered_files = list(self.discovered_dir.glob("*.json"))

        for df in discovered_files[:20]:
            try:
                data = json.loads(df.read_text(encoding="utf-8"))
                deadline = data.get("deadline")
                if not deadline:
                    continue

                deadline_dt = self._parse_date(deadline)
                if not deadline_dt:
                    continue

                days_until = (deadline_dt - datetime.now()).days

                # Remind 7 days before deadline
                if 0 < days_until <= 7:
                    existing = self.d
b.fetchone(
                        "SELECT id FROM approvals WHERE item_type = 'grant_deadline' AND description LIKE ?",
                        (f"%{data.get('title', '')[:30]}%",)
                    )
                    if not existing:
                        self.db.add_approval(
                            item_type="grant_deadline",
                            description=f"DEADLINE in {days_until} days: {data.get('title', 'Unknown')}",
                            details=json.dumps(data, default=str),
                            stream=self.STREAM,
                            agent=self.AGENT_NAME,
                            priority=1,
                        )

            except (json.JSONDecodeError, IOError, ValueError):
                continue

    def record_outcome(self, grant_title: str, outcome: str, amount: float = 0):
        """Record grant outcome in Reflection Database."""
        self.db.add_learning(
            prediction=f"Grant '{grant_title}' would be approved",
            outcome=outcome,
            lesson=f"Grant outcome: {outcome}. {'Success pattern to replicate.' if 'approved' in outcome.lower() else 'Review application strategy.'}",
            confidence=60,
            stream=self.STREAM,
            tags=["grant", outcome.lower().split()[0] if outcome else "unknown"],
        )

        if amount > 0 and "approved" in outcome.lower():
            self.record_revenue("grant", amount, f"Grant awarded: {grant_title}")

            # Move to awarded directory
            grant_hash = hashlib.md5(grant_title.encode()).hexdigest()
            src = self.submitted_dir / f"{grant_hash}.json"
            if src.exists():
                dst = self.awarded_dir / f"{grant_hash}.json"
                data = json.loads(src.read_text(encoding="utf-8"))
                data["status"] = "awarded"
                data["awarded_amount"] = amount
                data["awarded_at"] = now_iso()
                dst.write_text(json.dumps(data, in
dent=2, default=str), encoding="utf-8")
                src.unlink()