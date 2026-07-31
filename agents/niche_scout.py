#!/usr/bin/env python3
"""
═══════════════════════════════════════════════════════════════
THE INSTITUTION — NICHE SCOUT AGENT
═══════════════════════════════════════════════════════════════
Continuous opportunity discovery engine:
- Monitors markets, trends, forums, and platforms
- Discovers new revenue opportunities
- Scores opportunities by viability, effort, risk, fit
- Proposes new streams to the founder
- Spawns new agents via the meta-agent when validated
- Constitutional audit before any spawning
- Feeds validated opportunities into the Foundry
═══════════════════════════════════════════════════════════════
"""

import os
import json
import hashlib
import random
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import requests
from bs4 import BeautifulSoup

from common import get_db, get_config, get_logger, INSTITUTION_ROOT, now_iso, today_str, slugify
from agents.base import BaseAgent

logger = get_logger("niche_scout")


class NicheScoutAgent(BaseAgent):
    AGENT_NAME = "niche_scout"
    AGENT_TYPE = "governance"
    STREAM = None  # Cross-stream discovery agent
    DEFAULT_INTERVAL_SECONDS = 43200  # Every 12 hours

    def __init__(self):
        super().__init__()
        self.scout_dir = INSTITUTION_ROOT / "scout"
        self.opportunities_dir = self.scout_dir / "opportunities"
        self.research_dir = self.scout_dir / "research"
        for d in [self.opportunities_dir, self.research_dir]:
            d.mkdir(parents=True, exist_ok=True)

        self._session = requests.Session()
        self._session.headers.update({
            "User-Agent": "Mozilla/5.0 (compatible; InstitutionScout/1.0)"
        })

        # Scoring weights for opportunity evaluation
        self.score_weights = {
            "pain": 0.15,          # Is the problem painful enough to pay to solve?
            "buyer": 0.12,         # Is there a specific, reachable buyer?
            "reach": 0.10,         # Can we reach the buyer 
cheaply?
            "ability": 0.12,       # Can we deliver with AI assistance?
            "speed": 0.10,         # Can it produce cash within 30-90 days?
            "margin": 0.10,        # Is it high-margin?
            "recurrence": 0.08,    # Can it become recurring?
            "risk": 0.08,          # Is legal/reputational/financial risk low?
            "fit": 0.08,           # Does it fit founder's energy constraints?
            "compounding": 0.07,   # Does it create reusable assets?
        }

    def run_once(self):
        """Main cycle: discover, research, score, propose, spawn."""
        results = []

        # Phase 1: Discover new opportunities from multiple sources
        new_opportunities = self._discover_opportunities()
        if new_opportunities:
            results.append(f"Discovered {len(new_opportunities)} new opportunities")

        # Phase 2: Research and enrich each opportunity
        researched = self._research_opportunities(new_opportunities)
        if researched:
            results.append(f"Researched {len(researched)} opportunities")

        # Phase 3: Score all opportunities
        scored = self._score_opportunities(researched)
        if scored:
            results.append(f"Scored {len(scored)} opportunities")

        # Phase 4: Propose top opportunities to founder
        proposed = self._propose_opportunities(scored)
        if proposed:
            results.append(f"Proposed {proposed} opportunities for review")

        # Phase 5: Check for validated opportunities ready to spawn
        spawned = self._spawn_validated_agents()
        if spawned:
            results.append(f"Spawned {spawned} new agents")

        # Phase 6: Monitor existing streams for expansion opportunities
        self._monitor_expansion()

        return "; ".join(results) if results else "Scout cycle complete — no new opportunities"

    def _discover_opportunities(self) -> list:
        """Discover opportunities from multiple sources."""
    
    all_opportunities = []

        # Source 1: Analyze existing stream performance for patterns
        pattern_opps = self._discover_from_performance()
        all_opportunities.extend(pattern_opps)

        # Source 2: AI-generated opportunity hypotheses
        ai_opps = self._discover_via_ai()
        all_opportunities.extend(ai_opps)

        # Source 3: Market trend scanning
        trend_opps = self._discover_from_trends()
        all_opportunities.extend(trend_opps)

        # Source 4: Cross-pollination from existing niches
        cross_opps = self._discover_cross_pollination()
        all_opportunities.extend(cross_opps)

        # Deduplicate
        seen = set()
        unique = []
        for opp in all_opportunities:
            opp_hash = hashlib.md5(
                f"{opp.get('title', '')}{opp.get('category', '')}".encode()
            ).hexdigest()[:12]
            if opp_hash not in seen:
                seen.add(opp_hash)
                opp["hash"] = opp_hash
                # Check if already discovered
                existing = self.db.fetchone(
                    "SELECT id FROM content_inventory WHERE stream = 'scout' AND slug = ?",
                    (opp_hash,)
                )
                if not existing:
                    unique.append(opp)

        return unique

    def _discover_from_performance(self) -> list:
        """Analyze existing stream performance to find expansion patterns."""
        opportunities = []

        # Get top-performing content/products
        top_content = self.db.fetchall(
            """SELECT title, site, metrics, stream FROM content_inventory
               WHERE status = 'published' AND stream IN ('content_sites', 'digital_products', 'affiliate_sites')
               ORDER BY created_at DESC LIMIT 20"""
        )

        # Get revenue by stream
        revenue_by_stream = self.db.fetchall(
            """SELECT stream, SUM(amount) as total, COUNT(*) as count
               FROM revenue GROUP 
BY stream ORDER BY total DESC"""
        )

        # Identify patterns: what's working?
        if revenue_by_stream:
            top_stream = revenue_by_stream[0]
            if top_stream["total"] > 0:
                opportunities.append({
                    "title": f"Expand {top_stream['stream']} — proven revenue pattern",
                    "category": "expansion",
                    "description": f"Stream '{top_stream['stream']}' has generated ${top_stream['total']:.2f} "
                                   f"across {top_stream['count']} transactions. Consider doubling down.",
                    "source": "performance_analysis",
                    "evidence": f"${top_stream['total']:.2f} revenue, {top_stream['count']} transactions",
                    "discovered_at": now_iso(),
                })

        # Identify gaps: what niches have content but no revenue?
        content_streams = set(c.get("stream", "") for c in top_content)
        revenue_streams = set(r["stream"] for r in revenue_by_stream) if revenue_by_stream else set()
        gap_streams = content_streams - revenue_streams

        for gap in gap_streams:
            if gap:
                opportunities.append({
                    "title": f"Monetize {gap} — content exists but no revenue",
                    "category": "monetization_gap",
                    "description": f"Stream '{gap}' has published content but no recorded revenue. "
                                   f"Investigate monetization options.",
                    "source": "performance_analysis",
                    "evidence": f"Content published in {gap} but zero revenue recorded",
                    "discovered_at": now_iso(),
                })

        return opportunities

    def _discover_via_ai(self) -> list:
        """Use AI to generate opportunity hypotheses."""
        # Get context about what's working
        active_streams = self.db.get_active_streams()
        stream_names = [s["name"] for s in acti
ve_streams] if active_streams else []

        lessons = self.db.get_lessons(limit=5)
        lesson_texts = [l.get("lesson", "") for l in lessons]

        prompt = f"""You are an opportunity scout for a solo technical operator in Australia.

CURRENT ACTIVE STREAMS: {', '.join(stream_names) if stream_names else 'None yet'}
RECENT LESSONS: {json.dumps(lesson_texts[:3], default=str)}

OPERATOR PROFILE:
- Technical: Python, Linux, automation, AI, homelab infrastructure
- Constraints: Chronic pain, limited physical capacity, variable energy
- Location: Victoria, Australia
- Budget: Zero (must use free tiers)
- Strengths: Systems thinking, deep technical curiosity, automation expertise

Generate 5 NEW revenue opportunity hypotheses that:
1. Leverage the operator's existing skills
2. Can be delivered primarily by AI agents
3. Require minimal physical effort
4. Have low startup cost (free tiers only)
5. Can produce income within 30-90 days
6. Are NOT already in the active streams list

OUTPUT FORMAT (JSON array):
[
  {{
    "title": "Opportunity name (max 60 chars)",
    "category": "service|product|content|affiliate|saas|grant",
    "description": "2-3 sentence description of the opportunity",
    "target_buyer": "Who specifically would pay for this",
    "price_range": "$X-$Y AUD",
    "time_to_first_dollar": "estimated days",
    "ai_leverage": "How AI agents do 80%+ of the work",
    "evidence": "Why this might work (market signal, trend, gap)",
    "risks": ["risk 1", "risk 2"]
  }}
]
"""
        response = self.generate_text(
            prompt=prompt,
            quality_tier="routine",
            temperature=0.85,
            max_tokens=2000,
        )

        if not response:
            return []

        try:
            json_start = response.find("[")
            json_end = response.rfind("]") + 1
            if json_start >= 0 and json_end > json_start:
                opps = json.loads(response[json_start:json_end])
                if isinstance(opps, list
):
                    for opp in opps:
                        opp["source"] = "ai_hypothesis"
                        opp["discovered_at"] = now_iso()
                    return opps
        except json.JSONDecodeError:
            pass

        return []

    def _discover_from_trends(self) -> list:
        """Scan for market trends and emerging opportunities."""
        opportunities = []

        # Scan Hacker News for trending topics
        try:
            resp = self._session.get(
                "https://hacker-news.firebaseio.com/v0/topstories.json",
                timeout=15,
            )
            if resp.status_code == 200:
                story_ids = resp.json()[:10]
                trending_topics = []
                for sid in story_ids[:5]:
                    story_resp = self._session.get(
                        f"https://hacker-news.firebaseio.com/v0/item/{sid}.json",
                        timeout=10,
                    )
                    if story_resp.status_code == 200:
                        story = story_resp.json()
                        title = story.get("title", "")
                        if title:
                            trending_topics.append(title)

                if trending_topics:
                    # Ask AI to identify opportunities from trends
                    trend_text = "\n".join(f"- {t}" for t in trending_topics)
                    prompt = f"""Given these trending technology topics:
{trend_text}

Identify 1-2 revenue opportunities that a solo technical operator in Australia could pursue.
Focus on: automation services, technical content, micro-tools, or consulting.
Only suggest opportunities that can be started with zero budget.

OUTPUT FORMAT (JSON array):
[
  {{
    "title": "Opportunity name",
    "category": "service|product|content|saas",
    "description": "Brief description",
    "target_buyer": "Who pays",
    "evidence": "Which trend supports this"
  }}
]
"""
                    response = sel
f.generate_text(prompt, quality_tier="routine", temperature=0.8, max_tokens=800)
                    if response:
                        try:
                            json_start = response.find("[")
                            json_end = response.rfind("]") + 1
                            if json_start >= 0 and json_end > json_start:
                                trend_opps = json.loads(response[json_start:json_end])
                                for opp in trend_opps:
                                    opp["source"] = "trend_scan"
                                    opp["discovered_at"] = now_iso()
                                    opp["risks"] = ["Trend may be short-lived"]
                                opportunities.extend(trend_opps)
                        except json.JSONDecodeError:
                            pass

        except Exception as e:
            logger.debug(f"Trend scan error: {e}")

        return opportunities

    def _discover_cross_pollination(self) -> list:
        """Find opportunities by combining existing niches."""
        opportunities = []

        # Get all configured niches
        config = get_config()
        content_sites = config.get("streams", "content_sites", "sites", default=[])
        niches = [s.get("niche", "") for s in content_sites if s.get("niche")]

        if len(niches) >= 2:
            # Generate cross-niche opportunities
            for i in range(min(3, len(niches))):
                for j in range(i + 1, min(4, len(niches))):
                    niche_a = niches[i]
                    niche_b = niches[j]
                    opportunities.append({
                        "title": f"Cross-niche: {niche_a} × {niche_b}",
                        "category": "content",
                        "description": f"Create content/products at the intersection of {niche_a} and {niche_b}. "
                                       f"Less competition, highly targeted audience.",
                        "target_buye
r": f"People interested in both {niche_a} and {niche_b}",
                        "source": "cross_pollination",
                        "evidence": f"Existing expertise in both {niche_a} and {niche_b}",
                        "discovered_at": now_iso(),
                        "risks": ["Audience may be too narrow"],
                    })

        return opportunities

    def _research_opportunities(self, opportunities: list) -> list:
        """Research and enrich each opportunity with market data."""
        researched = []

        for opp in opportunities[:10]:  # Limit research per cycle
            # Use AI to research viability
            prompt = f"""Research this revenue opportunity for a solo technical operator in Australia.

OPPORTUNITY: {opp.get('title', 'Unknown')}
DESCRIPTION: {opp.get('description', 'No description')}
CATEGORY: {opp.get('category', 'general')}
TARGET BUYER: {opp.get('target_buyer', 'Unknown')}

ANALYSE:
1. Market size: How many potential buyers exist? (rough estimate)
2. Competition: Who else serves this market? Name 2-3 competitors.
3. Pricing: What do competitors charge?
4. Differentiation: What unique angle could we take?
5. Delivery: Can AI agents handle 80%+ of delivery?
6. Validation: What's the cheapest way to test demand?
7. Risks: Top 3 risks and mitigations.

OUTPUT FORMAT (JSON):
{{
  "market_size_estimate": "rough number of potential buyers",
  "competitors": ["competitor 1", "competitor 2"],
  "competitor_pricing": "what they charge",
  "differentiation": "our unique angle",
  "ai_delivery_percent": 80,
  "validation_method": "cheapest way to test",
  "validation_cost": "$0-$X",
  "risks": [
    {{"risk": "description", "severity": "high|medium|low", "mitigation": "how to handle"}}
  ],
  "viability_score": 7,
  "recommended_next_step": "specific action to validate"
}}
"""
            response = self.generate_text(
                prompt=prompt,
                quality_tier="routine",
                temperature=0.5,

                max_tokens=1500,
            )

            if response:
                try:
                    json_start = response.find("{")
                    json_end = response.rfind("}") + 1
                    if json_start >= 0 and json_end > json_start:
                        research = json.loads(response[json_start:json_end])
                        opp["research"] = research
                        opp["viability_score"] = research.get("viability_score", 5)
                except json.JSONDecodeError:
                    opp["viability_score"] = 5
            else:
                opp["viability_score"] = 5

            researched.append(opp)

            # Save research
            research_file = self.research_dir / f"{opp['hash']}_research.json"
            research_file.write_text(json.dumps(opp, indent=2, default=str), encoding="utf-8")

        return researched

    def _score_opportunities(self, opportunities: list) -> list:
        """Score opportunities using the weighted scorecard."""
        scored = []

        for opp in opportunities:
            research = opp.get("research", {})
            scores = {}

            # Pain: Is the problem painful enough?
            pain_indicators = ["urgent", "critical", "expensive", "time-consuming", "frustrating"]
            desc_lower = f"{opp.get('description', '')} {opp.get('title', '')}".lower()
            scores["pain"] = sum(1 for p in pain_indicators if p in desc_lower) * 2 + 3
            scores["pain"] = min(10, scores["pain"])

            # Buyer: Is there a specific buyer?
            buyer = opp.get("target_buyer", "")
            scores["buyer"] = 8 if buyer and len(buyer) > 10 else 4

            # Reach: Can we reach them cheaply?
            reach_keywords = ["online", "digital", "remote", "content", "seo", "social"]
            scores["reach"] = sum(1 for k in reach_keywords if k in desc_lower) * 2 + 3
            scores["reach"] = min(10, scores["reach"])

            # Abil
ity: Can we deliver with AI?
            ai_percent = research.get("ai_delivery_percent", 50)
            scores["ability"] = min(10, ai_percent // 10)

            # Speed: Time to first dollar
            time_str = str(opp.get("time_to_first_dollar", "60")).lower()
            if "7" in time_str or "14" in time_str or "week" in time_str:
                scores["speed"] = 9
            elif "30" in time_str or "month" in time_str:
                scores["speed"] = 7
            elif "60" in time_str or "90" in time_str:
                scores["speed"] = 5
            else:
                scores["speed"] = 3

            # Margin: High margin?
            category = opp.get("category", "")
            if category in ("saas", "product", "digital"):
                scores["margin"] = 9
            elif category in ("service", "content"):
                scores["margin"] = 7
            elif category == "affiliate":
                scores["margin"] = 6
            else:
                scores["margin"] = 5

            # Recurrence: Can it recur?
            if category in ("saas", "service"):
                scores["recurrence"] = 8
            elif category == "content":
                scores["recurrence"] = 6
            else:
                scores["recurrence"] = 4

            # Risk: Low risk?
            risks = opp.get("risks", [])
            if isinstance(risks, list):
                high_risks = sum(1 for r in risks if isinstance(r, dict) and r.get("severity") == "high")
                scores["risk"] = max(2, 10 - high_risks * 3)
            else:
                scores["risk"] = 6

            # Fit: Does it fit energy constraints?
            physical_keywords = ["physical", "manual", "onsite", "travel", "heavy"]
            has_physical = any(k in desc_lower for k in physical_keywords)
            scores["fit"] = 3 if has_physical else 8

            # Compounding: Creates reusable assets?
            compounding_keywords = ["template", "system", "t
ool", "platform", "library", "framework"]
            scores["compounding"] = sum(1 for k in compounding_keywords if k in desc_lower) * 2 + 4
            scores["compounding"] = min(10, scores["compounding"])

            # Calculate weighted total
            total = sum(
                scores.get(key, 5) * weight
                for key, weight in self.score_weights.items()
            )

            opp["score"] = round(total, 2)
            opp["score_breakdown"] = scores
            scored.append(opp)

        # Sort by score descending
        scored.sort(key=lambda o: o.get("score", 0), reverse=True)

        # Store in database
        for opp in scored:
            self.db.insert("content_inventory", {
                "stream": "scout",
                "content_type": "opportunity",
                "title": opp.get("title", "Unknown"),
                "slug": opp["hash"],
                "status": "draft",
                "metrics": json.dumps({
                    "score": opp["score"],
                    "category": opp.get("category"),
                    "viability": opp.get("viability_score", 5),
                }),
            })

            # Save to opportunities directory
            opp_file = self.opportunities_dir / f"{opp['hash']}.json"
            opp_file.write_text(json.dumps(opp, indent=2, default=str), encoding="utf-8")

        return scored

    def _propose_opportunities(self, scored: list) -> int:
        """Propose top opportunities to founder for review."""
        proposed = 0
        min_score = self.config.get("meta_agent", "spawn_threshold", "min_opportunity_score", default=7)

        for opp in scored[:5]:
            if opp.get("score", 0) < min_score:
                continue

            # Check if already proposed
            existing = self.db.fetchone(
                "SELECT id FROM approvals WHERE item_type = 'new_opportunity' AND description LIKE ? AND status = 'pending'",
                (f"%{opp['hash']}%",)
     
       )
            if existing:
                continue

            # Constitutional audit before proposing
            audit = self.audit_action(
                f"Propose new revenue opportunity: {opp.get('title', 'Unknown')}",
                context={"type": "opportunity_proposal", "score": opp.get("score", 0)}
            )

            if audit.overall_result == "FAIL":
                logger.warning(f"Constitutional Court blocked opportunity: {opp.get('title')}")
                continue

            # Create approval request
            self.db.add_approval(
                item_type="new_opportunity",
                description=f"New opportunity (score {opp['score']:.1f}/10): {opp.get('title', 'Unknown')}",
                details=json.dumps({
                    "hash": opp["hash"],
                    "title": opp.get("title"),
                    "category": opp.get("category"),
                    "description": opp.get("description"),
                    "target_buyer": opp.get("target_buyer"),
                    "score": opp["score"],
                    "score_breakdown": opp.get("score_breakdown"),
                    "research": opp.get("research"),
                    "risks": opp.get("risks"),
                    "validation_method": opp.get("research", {}).get("validation_method"),
                }, default=str),
                stream="scout",
                agent=self.AGENT_NAME,
                priority=1 if opp["score"] >= 8 else 2,
            )

            proposed += 1
            logger.info(f"Proposed opportunity: {opp.get('title')} (score: {opp['score']:.1f})")

        return proposed

    def _spawn_validated_agents(self) -> int:
        """Spawn new agents for opportunities that have been validated and approved."""
        spawned = 0

        # Find approved opportunities
        approved = self.db.fetchall(
            """SELECT * FROM approvals
               WHERE item_type = 'new_opportunity' AND status = 'approved'
  
             AND resolved_at > datetime('now', '-7 days')"""
        )

        for approval in approved:
            details = {}
            try:
                details = json.loads(approval.get("details", "{}"))
            except (json.JSONDecodeError, TypeError):
                continue

            opp_hash = details.get("hash", "")
            if not opp_hash:
                continue

            # Check if already spawned
            spawn_marker = self.opportunities_dir / f"{opp_hash}_spawned.json"
            if spawn_marker.exists():
                continue

            # Load full opportunity data
            opp_file = self.opportunities_dir / f"{opp_hash}.json"
            if not opp_file.exists():
                continue

            opp = json.loads(opp_file.read_text(encoding="utf-8"))

            # Constitutional audit before spawning
            audit = self.audit_action(
                f"Spawn new agent for validated opportunity: {opp.get('title', 'Unknown')}",
                context={
                    "type": "agent_spawn",
                    "requires_approval": False,  # Already approved by founder
                    "score": opp.get("score", 0),
                }
            )

            if audit.overall_result == "FAIL":
                logger.warning(f"Constitutional Court blocked agent spawn: {opp.get('title')}")
                continue

            # Call meta-agent to spawn
            try:
                from meta_agent import MetaAgent
                meta = MetaAgent()
                success = meta.spawn_agent_for_opportunity({
                    "name": opp.get("title", "New Stream"),
                    "slug": slugify(opp.get("title", f"stream_{opp_hash}")),
                    "description": opp.get("description", ""),
                    "score": opp.get("score", 0),
                    "category": opp.get("category", ""),
                    "kill_criterion": f"No revenue after 60 days",
                    "s
tream": slugify(opp.get("title", f"stream_{opp_hash}")),
                })

                if success:
                    # Mark as spawned
                    spawn_marker.write_text(json.dumps({
                        "spawned_at": now_iso(),
                        "opportunity": opp.get("title"),
                        "approval_id": approval["id"],
                    }, indent=2), encoding="utf-8")
                    spawned += 1

                    # Record learning
                    self.log_learning(
                        prediction=f"Opportunity '{opp.get('title')}' would be validated and spawned",
                        outcome=f"Validated and spawned as new stream",
                        lesson=f"Opportunity scoring system works. Score {opp.get('score', 0)}/10 led to successful spawn.",
                        confidence=70,
                        tags=["scout", "spawn", opp.get("category", "")],
                    )

                    logger.info(f"Spawned new agent for: {opp.get('title')}")

            except Exception as e:
                logger.error(f"Failed to spawn agent for {opp.get('title')}: {e}")

        return spawned

    def _monitor_expansion(self):
        """Monitor existing streams for expansion opportunities."""
        streams = self.db.get_active_streams()

        for stream in streams:
            # Check if stream is performing well enough to expand
            revenue = self.db.fetchone(
                "SELECT COALESCE(SUM(amount), 0) as total FROM revenue WHERE stream = ? AND recorded_at > datetime('now', '-30 days')",
                (stream["slug"],)
            )

            if revenue and revenue["total"] > 100:  # Threshold for expansion consideration
                # Check if expansion has already been proposed
                existing = self.db.fetchone(
                    "SELECT id FROM approvals WHERE item_type = 'expansion' AND description LIKE ? AND status = 'pending'",
                    (f
"%{stream['slug']}%",)
                )
                if not existing:
                    self.db.add_approval(
                        item_type="expansion",
                        description=f"Consider expanding '{stream['name']}' — ${revenue['total']:.2f}/month revenue",
                        details=json.dumps({
                            "stream": stream["slug"],
                            "revenue_30d": revenue["total"],
                            "current_autonomy": stream.get("autonomy_level", 1),
                        }),
                        stream=stream["slug"],
                        agent=self.AGENT_NAME,
                        priority=3,
                    )