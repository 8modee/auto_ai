#!/usr/bin/env python3
"""
═══════════════════════════════════════════════════════════════
THE INSTITUTION — STREAM 4: NEWSLETTER
═══════════════════════════════════════════════════════════════
Weekly curated + generated newsletter:
- Cross-promotes content sites and products
- Monetised: sponsorships at 1000+ subs, affiliate links
- Grows via content site CTAs and social media
- Buttondown free tier / Listmonk self-hosted
- Subscriber management and engagement tracking
═══════════════════════════════════════════════════════════════
"""

import os
import json
import random
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import requests

from common import get_db, get_config, get_logger, INSTITUTION_ROOT, now_iso, today_str
from agents.base import BaseAgent

logger = get_logger("newsletter")


class NewsletterAgent(BaseAgent):
    AGENT_NAME = "newsletter"
    AGENT_TYPE = "revenue_stream"
    STREAM = "newsletter"
    DEFAULT_INTERVAL_SECONDS = 86400  # Daily check, weekly send

    def __init__(self):
        super().__init__()
        self.newsletter_dir = INSTITUTION_ROOT / "newsletters"
        self.output_dir = self.newsletter_dir / "output"
        self.templates_dir = self.newsletter_dir / "templates"
        for d in [self.output_dir, self.templates_dir]:
            d.mkdir(parents=True, exist_ok=True)

    def run_once(self):
        """Main cycle: check if it's send day, generate and send newsletter."""
        if not self.should_run_today():
            return "Stream disabled or inactive"

        stream_cfg = self.get_stream_config()
        schedule = stream_cfg.get("schedule", {})
        send_day = schedule.get("send_day", "wednesday").lower()
        send_hour = schedule.get("send_hour", 9)

        now = datetime.now()
        current_day = now.strftime("%A").lower()
        current_hour = now.hour

        # Only generate/send on the configured day
        if current_day != send_day:
            # On
 other days, do subscriber growth tasks
            self._growth_tasks(stream_cfg)
            return f"Not send day ({send_day}). Growth tasks completed."

        # Check if already sent today
        already_sent = self.db.fetchone(
            "SELECT id FROM content_inventory WHERE stream = ? AND content_type = 'newsletter' AND DATE(created_at) = ?",
            (self.STREAM, today_str())
        )
        if already_sent:
            return "Newsletter already sent today."

        # Generate newsletter content
        newsletter = self._generate_newsletter(stream_cfg)
        if not newsletter:
            return "Newsletter generation failed"

        # Save newsletter
        issue_number = self._get_issue_number()
        newsletter_path = self.output_dir / f"issue_{issue_number:04d}_{today_str()}.md"
        newsletter_path.write_text(newsletter["markdown"], encoding="utf-8")

        # Send via configured platform
        sent = self._send_newsletter(newsletter, stream_cfg)

        # Record
        self.db.insert("content_inventory", {
            "stream": self.STREAM,
            "content_type": "newsletter",
            "title": newsletter["subject"],
            "slug": f"issue-{issue_number}",
            "status": "published" if sent else "draft",
            "published_at": now_iso() if sent else None,
            "metrics": json.dumps({"issue": issue_number, "sent": sent}),
        })

        if sent:
            return f"Newsletter #{issue_number} sent: '{newsletter['subject']}'"
        else:
            return f"Newsletter #{issue_number} generated (not sent — no platform configured)"

    def _get_issue_number(self) -> int:
        """Get the next issue number."""
        result = self.db.fetchone(
            "SELECT COUNT(*) as cnt FROM content_inventory WHERE stream = ? AND content_type = 'newsletter'",
            (self.STREAM,)
        )
        return (result["cnt"] if result else 0) + 1

    def _generate_newsletter(self, stream_cfg:
 dict) -> Optional[dict]:
        """Generate newsletter content using AI + institutional data."""
        sections = stream_cfg.get("sections", ["curated_links", "original_insight", "product_spotlight", "site_updates"])
        issue_number = self._get_issue_number()

        # Gather content from the institution
        recent_articles = self.db.fetchall(
            "SELECT title, slug, site FROM content_inventory WHERE stream = 'content_sites' AND status = 'published' ORDER BY created_at DESC LIMIT 5"
        )
        recent_products = self.db.fetchall(
            "SELECT title, slug FROM content_inventory WHERE stream = 'digital_products' AND status = 'published' ORDER BY created_at DESC LIMIT 3"
        )
        recent_lessons = self.db.get_lessons(limit=3)

        # Build context for AI
        articles_context = "\n".join([f"- {a['title']} (site: {a.get('site', 'unknown')})" for a in recent_articles]) or "No recent articles."
        products_context = "\n".join([f"- {p['title']}" for p in recent_products]) or "No recent products."
        lessons_context = "\n".join([f"- {l.get('lesson', '')}" for l in recent_lessons]) or "No recent lessons."

        prompt = f"""Write a newsletter issue #{issue_number} for a technology and productivity audience.

RECENT CONTENT FROM OUR SITES:
{articles_context}

RECENT PRODUCTS:
{products_context}

RECENT INSIGHTS/LESSONS:
{lessons_context}

SECTIONS TO INCLUDE: {', '.join(sections)}

REQUIREMENTS:
- Conversational, warm tone (not corporate)
- Australian English
- Each section should be 2-4 sentences
- Include one genuinely useful tip or insight
- Cross-reference our content naturally (don't be spammy)
- End with a brief, human sign-off
- Subject line should be compelling (max 60 chars)
- Total length: 300-500 words

OUTPUT FORMAT (JSON):
{{
  "subject": "Email subject line",
  "preview_text": "Preview text shown in inbox (max 90 chars)",
  "sections": [
    {{
      "heading": "Section heading",
      "content": "Se
ction content (2-4 sentences)",
      "type": "curated_links|original_insight|product_spotlight|site_updates|tip"
    }}
  ],
  "sign_off": "Brief closing line"
}}
"""
        response = self.generate_text(
            prompt=prompt,
            quality_tier="routine",
            temperature=0.7,
            max_tokens=2048,
        )

        if not response:
            return self._fallback_newsletter(issue_number, recent_articles, recent_products)

        # Parse response
        try:
            json_start = response.find("{")
            json_end = response.rfind("}") + 1
            if json_start >= 0 and json_end > json_start:
                data = json.loads(response[json_start:json_end])
                if "subject" in data and "sections" in data:
                    # Convert to markdown
                    data["markdown"] = self._render_markdown(data, issue_number)
                    return data
        except json.JSONDecodeError:
            pass

        return self._fallback_newsletter(issue_number, recent_articles, recent_products)

    def _fallback_newsletter(self, issue_number: int, articles: list, products: list) -> dict:
        """Generate newsletter without AI (offline fallback)."""
        sections = []

        # Intro
        sections.append({
            "heading": "This Week",
            "content": f"Welcome to issue #{issue_number}. Here's what's been happening and what's worth your attention this week.",
            "type": "original_insight",
        })

        # Site updates
        if articles:
            article_lines = "\n".join([f"- [{a['title']}](/posts/{a.get('slug', '')}/)" for a in articles[:3]])
            sections.append({
                "heading": "From Our Sites",
                "content": f"Recent articles you might have missed:\n{article_lines}",
                "type": "site_updates",
            })

        # Product spotlight
        if products:
            sections.append({
                "heading": "Pr
oduct Spotlight",
                "content": f"New in the shop: {products[0]['title']}. Designed to help you get organised and take action.",
                "type": "product_spotlight",
            })

        # Tip
        sections.append({
            "heading": "Quick Tip",
            "content": "The best productivity system is the one you'll actually use. Start with one habit, master it, then add another. Complexity is the enemy of consistency.",
            "type": "tip",
        })

        data = {
            "subject": f"Issue #{issue_number}: What's worth your time this week",
            "preview_text": "Quick insights, new content, and one useful tip.",
            "sections": sections,
            "sign_off": "Until next week — keep building.",
        }
        data["markdown"] = self._render_markdown(data, issue_number)
        return data

    def _render_markdown(self, data: dict, issue_number: int) -> str:
        """Render newsletter data to markdown."""
        md = f"# {data.get('subject', f'Issue #{issue_number}')}\n\n"

        for section in data.get("sections", []):
            md += f"## {section.get('heading', '')}\n\n"
            md += f"{section.get('content', '')}\n\n"

        md += f"---\n\n*{data.get('sign_off', 'Until next week.')}*\n\n"
        md += f"*You received this because you subscribed. Unsubscribe anytime.*\n"

        return md

    def _send_newsletter(self, newsletter: dict, stream_cfg: dict) -> bool:
        """Send newsletter via configured platform."""
        platform = stream_cfg.get("platform", "buttondown")

        if platform == "buttondown":
            return self._send_buttondown(newsletter)
        elif platform == "listmonk":
            return self._send_listmonk(newsletter)
        else:
            logger.info(f"Unknown newsletter platform: {platform}")
            return False

    def _send_buttondown(self, newsletter: dict) -> bool:
        """Send via Buttondown API (free tier: 100 subscribers)."
""
        api_key = os.environ.get("BUTTONDOWN_API_KEY")
        if not api_key:
            logger.debug("No Buttondown API key. Newsletter saved locally.")
            return False

        try:
            resp = requests.post(
                "https://api.buttondown.email/v1/emails",
                headers={
                    "Authorization": f"Token {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "subject": newsletter.get("subject", "Newsletter"),
                    "body": newsletter.get("markdown", ""),
                    "status": "public",
                },
                timeout=30,
            )

            if resp.status_code in (200, 201):
                logger.info("Newsletter sent via Buttondown")
                return True
            else:
                logger.warning(f"Buttondown send failed: {resp.status_code} {resp.text[:200]}")
                return False

        except Exception as e:
            logger.error(f"Buttondown send error: {e}")
            return False

    def _send_listmonk(self, newsletter: dict) -> bool:
        """Send via self-hosted Listmonk."""
        base_url = os.environ.get("LISTMONK_URL", "http://localhost:9000")
        username = os.environ.get("LISTMONK_USER", "admin")
        password = os.environ.get("LISTMONK_PASS", "")

        if not password:
            logger.debug("No Listmonk credentials. Newsletter saved locally.")
            return False

        try:
            # Create campaign
            resp = requests.post(
                f"{base_url}/api/campaigns",
                auth=(username, password),
                json={
                    "name": newsletter.get("subject", "Newsletter"),
                    "subject": newsletter.get("subject", "Newsletter"),
                    "body": newsletter.get("markdown", ""),
                    "content_type": "markdown",
                    "lists": [1],  # Defau
lt list
                    "type": "regular",
                },
                timeout=30,
            )

            if resp.status_code in (200, 201):
                campaign_data = resp.json().get("data", {})
                campaign_id = campaign_data.get("id")

                if campaign_id:
                    # Start the campaign
                    start_resp = requests.put(
                        f"{base_url}/api/campaigns/{campaign_id}/status",
                        auth=(username, password),
                        json={"status": "running"},
                        timeout=30,
                    )
                    if start_resp.status_code == 200:
                        logger.info(f"Newsletter sent via Listmonk (campaign {campaign_id})")
                        return True

            logger.warning(f"Listmonk send failed: {resp.status_code}")
            return False

        except Exception as e:
            logger.error(f"Listmonk send error: {e}")
            return False

    def _growth_tasks(self, stream_cfg: dict):
        """Perform subscriber growth tasks on non-send days."""
        # Generate CTA content for content sites
        cta_text = self._generate_cta()
        if cta_text:
            cta_path = self.newsletter_dir / "current_cta.txt"
            cta_path.write_text(cta_text, encoding="utf-8")

        # Check subscriber count for sponsorship eligibility
        self._check_sponsorship_eligibility(stream_cfg)

    def _generate_cta(self) -> str:
        """Generate a call-to-action for content sites to grow newsletter."""
        prompts = [
            "Get weekly insights on productivity and technology. Join the newsletter — no spam, unsubscribe anytime.",
            "Want one useful tip every Wednesday? Subscribe to our free newsletter.",
            "Join readers who get our best content first. Free weekly newsletter. No noise.",
        ]
        return random.choice(prompts)

    def _check_sponsorship_eligibil
ity(self, stream_cfg: dict):
        """Check if subscriber count meets sponsorship threshold."""
        threshold = stream_cfg.get("monetization", {}).get("sponsorship_threshold_subs", 1000)

        # Would query Buttondown/Listmonk API for subscriber count
        api_key = os.environ.get("BUTTONDOWN_API_KEY")
        if api_key:
            try:
                resp = requests.get(
                    "https://api.buttondown.email/v1/subscribers",
                    headers={"Authorization": f"Token {api_key}"},
                    params={"page_size": 1},
                    timeout=30,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    count = data.get("count", 0)
                    if count >= threshold:
                        # Log milestone
                        existing = self.db.fetchone(
                            "SELECT id FROM decisions WHERE description LIKE '%sponsorship eligible%'"
                        )
                        if not existing:
                            self.db.insert("decisions", {
                                "description": "Newsletter sponsorship eligible",
                                "decision": "READY",
                                "reasoning": f"Subscriber count ({count}) meets threshold ({threshold}). Can seek sponsors.",
                                "agent": self.AGENT_NAME,
                                "stream": self.STREAM,
                            })
                            logger.info(f"Newsletter sponsorship eligible! {count} subscribers.")
            except Exception as e:
                logger.debug(f"Subscriber count check failed: {e}")