#!/usr/bin/env python3
"""
═══════════════════════════════════════════════════════════════
THE INSTITUTION — STREAM 9: SOCIAL MEDIA CONTENT ENGINE
═══════════════════════════════════════════════════════════════
Multi-platform social media automation:
- Auto-generates posts for Twitter/X, LinkedIn, Reddit, Pinterest
- Repurposes content site articles into platform-native formats
- Builds audience for newsletter and product funnels
- Schedules via platform APIs or Buffer free tier
- Tracks engagement, adjusts strategy
═══════════════════════════════════════════════════════════════
"""

import os
import json
import random
import hashlib
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import requests

from common import get_db, get_config, get_logger, INSTITUTION_ROOT, now_iso, today_str, slugify
from agents.base import BaseAgent

logger = get_logger("social_media")


class SocialMediaAgent(BaseAgent):
    AGENT_NAME = "social_media"
    AGENT_TYPE = "revenue_stream"
    STREAM = "social_media"
    DEFAULT_INTERVAL_SECONDS = 10800  # Every 3 hours

    def __init__(self):
        super().__init__()
        self.social_dir = INSTITUTION_ROOT / "social"
        self.posts_dir = self.social_dir / "posts"
        self.scheduled_dir = self.social_dir / "scheduled"
        self.analytics_dir = self.social_dir / "analytics"
        for d in [self.posts_dir, self.scheduled_dir, self.analytics_dir]:
            d.mkdir(parents=True, exist_ok=True)

        # Platform character limits and configs
        self.platform_configs = {
            "twitter": {
                "max_chars": 280,
                "hashtag_limit": 3,
                "best_times": [9, 12, 17, 20],
                "content_style": "punchy, conversational, thread-friendly",
            },
            "linkedin": {
                "max_chars": 3000,
                "hashtag_limit": 5,
                "best_times": [8, 10, 12],
                "content_style": "prof
essional, insight-driven, story-based",
            },
            "reddit": {
                "max_chars": 40000,
                "hashtag_limit": 0,
                "best_times": [7, 12, 19],
                "content_style": "helpful, community-focused, no self-promotion unless allowed",
            },
            "pinterest": {
                "max_chars": 500,
                "hashtag_limit": 0,
                "best_times": [14, 20, 21],
                "content_style": "inspirational, visual-first, keyword-rich descriptions",
            },
        }

    def run_once(self):
        """Main cycle: generate posts, schedule, track engagement."""
        if not self.should_run_today():
            return "Stream disabled or inactive"

        stream_cfg = self.get_stream_config()
        platforms = stream_cfg.get("platforms", [])
        posts_per_day = stream_cfg.get("schedule", {}).get("posts_per_day", 3)
        repurpose_from = stream_cfg.get("repurpose_from", [])

        results = []

        # Check daily quota
        posts_today = self.db.fetchone(
            "SELECT COUNT(*) as cnt FROM content_inventory WHERE stream = ? AND content_type = 'social_post' AND DATE(created_at) = ?",
            (self.STREAM, today_str())
        )
        current_posts = posts_today["cnt"] if posts_today else 0

        if current_posts < posts_per_day:
            # Generate new posts
            remaining = posts_per_day - current_posts
            generated = self._generate_posts(platforms, remaining, repurpose_from)
            if generated:
                results.append(f"Generated {len(generated)} posts")

            # Schedule posts
            scheduled = self._schedule_posts(generated or [])
            if scheduled:
                results.append(f"Scheduled {scheduled} posts")

        # Track engagement on past posts
        self._track_engagement()

        # Adjust strategy based on performance
        self._adjust_strategy()

        return "; ".join(res
ults) if results else "Social media cycle complete"

    def _generate_posts(self, platforms: list, count: int,
                        repurpose_from: list) -> list:
        """Generate platform-native social media posts."""
        generated = []

        # Get content to repurpose
        source_content = self._get_repurpose_content(repurpose_from)

        for i in range(count):
            # Select platform (rotate)
            if platforms:
                platform_cfg = platforms[i % len(platforms)]
                platform_name = platform_cfg.get("platform", "twitter")
                content_types = platform_cfg.get("content_types", ["tips"])
                content_type = random.choice(content_types)
            else:
                platform_name = "twitter"
                content_type = "tips"

            # Select source content if available
            source = None
            if source_content:
                source = source_content[i % len(source_content)]

            # Generate post
            post = self._generate_single_post(platform_name, content_type, source)
            if post:
                # Save post
                post_hash = hashlib.md5(f"{post['text']}{now_iso()}".encode()).hexdigest()[:12]
                post["hash"] = post_hash
                post["platform"] = platform_name
                post["content_type"] = content_type
                post["created_at"] = now_iso()

                post_path = self.posts_dir / f"{post_hash}.json"
                post_path.write_text(json.dumps(post, indent=2, default=str), encoding="utf-8")

                # Record in inventory
                self.db.insert("content_inventory", {
                    "stream": self.STREAM,
                    "content_type": "social_post",
                    "title": post["text"][:80],
                    "slug": post_hash,
                    "status": "draft",
                    "metrics": json.dumps({
                        "platform": platform_
name,
                        "content_type": content_type,
                        "source": source.get("title", "") if source else "",
                    }),
                })

                generated.append(post)

        return generated

    def _get_repurpose_content(self, repurpose_from: list) -> list:
        """Get recent content from other streams to repurpose."""
        content = []

        for stream_slug in repurpose_from:
            recent = self.db.fetchall(
                "SELECT title, slug, site, metrics FROM content_inventory WHERE stream = ? AND status = 'published' ORDER BY created_at DESC LIMIT 5",
                (stream_slug,)
            )
            content.extend(recent)

        return content[:10]

    def _generate_single_post(self, platform: str, content_type: str,
                               source: Optional[dict]) -> Optional[dict]:
        """Generate a single platform-native post."""
        platform_cfg = self.platform_configs.get(platform, self.platform_configs["twitter"])
        max_chars = platform_cfg["max_chars"]
        style = platform_cfg["content_style"]
        hashtag_limit = platform_cfg["hashtag_limit"]

        source_context = ""
        if source:
            source_context = f"\nSOURCE CONTENT TO REPURPOSE:\nTitle: {source.get('title', '')}\nSlug: {source.get('slug', '')}\n"

        prompt = f"""Generate a {platform} post.

PLATFORM: {platform}
CONTENT TYPE: {content_type}
STYLE: {style}
MAX CHARACTERS: {max_chars}
HASHTAG LIMIT: {hashtag_limit}
{source_context}

REQUIREMENTS:
- Write in Australian English
- Sound human, not AI-generated
- Provide genuine value (tip, insight, resource)
- No spam, no engagement bait
- If repurposing source content, add unique commentary (don't just share a link)
- For Reddit: be genuinely helpful, no self-promotion, contribute to community
- For LinkedIn: professional but not corporate-speak
- For Twitter/X: punchy, opinionated, conversational
- For Pinterest: keyword
-rich description for an infographic or visual

OUTPUT FORMAT (JSON):
{{
  "text": "The post text (within character limit)",
  "hashtags": ["tag1", "tag2"],
  "cta": "optional call to action",
  "image_prompt": "description of image to accompany post (if applicable)",
  "best_time_hour": 12,
  "reply_hook": "optional question to encourage engagement"
}}
"""
        response = self.generate_text(
            prompt=prompt,
            quality_tier="routine",
            temperature=0.85,
            max_tokens=800,
        )

        if not response:
            return self._fallback_post(platform, content_type)

        try:
            json_start = response.find("{")
            json_end = response.rfind("}") + 1
            if json_start >= 0 and json_end > json_start:
                data = json.loads(response[json_start:json_end])
                if "text" in data:
                    # Enforce character limit
                    if len(data["text"]) > max_chars:
                        data["text"] = data["text"][:max_chars - 3] + "..."
                    # Enforce hashtag limit
                    if hashtag_limit > 0:
                        data["hashtags"] = data.get("hashtags", [])[:hashtag_limit]
                    else:
                        data["hashtags"] = []
                    return data
        except json.JSONDecodeError:
            pass

        return self._fallback_post(platform, content_type)

    def _fallback_post(self, platform: str, content_type: str) -> dict:
        """Generate a fallback post without AI."""
        posts = {
            "twitter": {
                "tips": {
                    "text": "Productivity tip: The best system is the one you'll actually use. Stop optimising. Start doing. One habit, mastered, beats ten half-started.",
                    "hashtags": ["productivity", "tips"],
                },
                "threads": {
                    "text": "Things I wish I knew about automation earlier:\n\n1. S
tart with the boring stuff\n2. Measure before optimising\n3. Document everything\n4. Build for your worst day, not your best",
                    "hashtags": ["automation", "tech"],
                },
                "hot_takes": {
                    "text": "Hot take: Most 'AI productivity tools' are just expensive to-do lists. The real productivity hack is doing fewer things with more focus.",
                    "hashtags": ["AI", "productivity"],
                },
            },
            "linkedin": {
                "insights": {
                    "text": "After months of building automated systems, here's what I've learned:\n\nThe technology is rarely the bottleneck. The bottleneck is knowing what to automate first.\n\nStart with the task you dread most. That's where the ROI lives.\n\nWhat's the one task you'd automate first?",
                    "hashtags": ["automation", "productivity", "technology", "insights"],
                },
                "case_studies": {
                    "text": "Small win this week: Automated a process that used to take 45 minutes daily.\n\nTime saved: 3.75 hours/week.\nCost: $0 (open-source tools).\nEffort: One afternoon of setup.\n\nThe best automation isn't impressive. It's invisible.",
                    "hashtags": ["automation", "efficiency", "smallbusiness"],
                },
            },
            "reddit": {
                "helpful_answers": {
                    "text": "If you're just getting started with home automation, my advice: don't buy a hub first. Start with one smart plug and one smart bulb. Learn what you actually want to automate before spending $200+ on a hub you might not need.\n\nI made the mistake of buying everything upfront and half of it sits unused. Start small, expand based on actual use.",
                    "hashtags": [],
                },
                "resource_shares": {
                    "text": "Compiled a list of free tools I use daily for productivity:\n\n- Obsidi
an (notes/knowledge base)\n- Todoist free tier (task management)\n- Cold Turkey (focus/blocking)\n- LibreOffice (documents)\n- GIMP (image editing)\n\nAll free, all excellent. No subscriptions needed.",
                    "hashtags": [],
                },
            },
            "pinterest": {
                "infographics": {
                    "text": "10 Free Productivity Tools That Replace Expensive Subscriptions | Save money while staying organised with these completely free alternatives to popular paid apps. Perfect for budget-conscious professionals and students.",
                    "hashtags": [],
                    "image_prompt": "Clean infographic showing 10 free tool logos with brief descriptions, modern flat design, blue and white color scheme",
                },
                "product_pins": {
                    "text": "Printable Weekly Planner Template | Free downloadable PDF planner with daily schedules, habit trackers, and weekly review sections. Perfect for getting organised without buying an expensive planner.",
                    "hashtags": [],
                    "image_prompt": "Mockup of a printed weekly planner on a clean desk, top-down view, natural lighting, minimalist style",
                },
            },
        }

        platform_posts = posts.get(platform, posts["twitter"])
        type_posts = platform_posts.get(content_type, list(platform_posts.values())[0])

        result = {
            "text": type_posts["text"],
            "hashtags": type_posts.get("hashtags", []),
            "cta": "",
            "image_prompt": type_posts.get("image_prompt", ""),
            "best_time_hour": 12,
            "reply_hook": "",
        }
        return result

    def _schedule_posts(self, posts: list) -> int:
        """Schedule posts for publishing."""
        scheduled = 0

        for post in posts:
            platform = post.get("platform", "twitter")
            best_hour = post.get("best_time_hour", 12)

         
   # Determine next posting time
            now = datetime.now()
            target_time = now.replace(hour=best_hour, minute=0, second=0, microsecond=0)
            if target_time <= now:
                target_time += timedelta(days=1)

            # Save to scheduled directory
            schedule_data = {
                "post": post,
                "scheduled_for": target_time.isoformat(),
                "status": "scheduled",
            }
            schedule_path = self.scheduled_dir / f"{post['hash']}_{platform}.json"
            schedule_path.write_text(json.dumps(schedule_data, indent=2, default=str), encoding="utf-8")

            # Attempt to publish via API
            published = self._publish_post(post, platform)
            if published:
                # Update status
                schedule_data["status"] = "published"
                schedule_data["published_at"] = now_iso()
                schedule_path.write_text(json.dumps(schedule_data, indent=2, default=str), encoding="utf-8")

                self.db.update("content_inventory", {
                    "status": "published",
                    "published_at": now_iso(),
                }, "slug = ?", (post["hash"],))

            scheduled += 1

        return scheduled

    def _publish_post(self, post: dict, platform: str) -> bool:
        """Attempt to publish post via platform API."""
        if platform == "twitter":
            return self._publish_twitter(post)
        elif platform == "linkedin":
            return self._publish_linkedin(post)
        elif platform == "reddit":
            return self._publish_reddit(post)
        elif platform == "pinterest":
            return self._publish_pinterest(post)
        return False

    def _publish_twitter(self, post: dict) -> bool:
        """Publish to Twitter/X via API."""
        api_key = os.environ.get("TWITTER_API_KEY")
        api_secret = os.environ.get("TWITTER_API_SECRET")
        access_token = os.environ.get("TWITTER_AC
CESS_TOKEN")
        access_secret = os.environ.get("TWITTER_ACCESS_SECRET")

        if not all([api_key, api_secret, access_token, access_secret]):
            logger.debug("Twitter credentials not configured. Post saved locally.")
            return False

        try:
            # Twitter API v2 requires OAuth 1.0a
            import hmac
            import base64
            import urllib.parse
            import time as time_module

            text = post["text"]
            if post.get("hashtags"):
                text += " " + " ".join(f"#{h}" for h in post["hashtags"])

            # Simplified OAuth 1.0a signing
            oauth_params = {
                "oauth_consumer_key": api_key,
                "oauth_nonce": hashlib.md5(now_iso().encode()).hexdigest(),
                "oauth_signature_method": "HMAC-SHA1",
                "oauth_timestamp": str(int(time_module.time())),
                "oauth_token": access_token,
                "oauth_version": "1.0",
            }

            # Build signature base string
            params = {**oauth_params}
            sorted_params = sorted(params.items())
            param_string = urllib.parse.urlencode(sorted_params, quote_via=urllib.parse.quote)
            base_string = f"POST&{urllib.parse.quote('https://api.twitter.com/2/tweets', safe='')}&{urllib.parse.quote(param_string, safe='')}"

            # Sign
            signing_key = f"{urllib.parse.quote(api_secret, safe='')}&{urllib.parse.quote(access_secret, safe='')}"
            signature = base64.b64encode(
                hmac.new(signing_key.encode(), base_string.encode(), hashlib.sha1).digest()
            ).decode()
            oauth_params["oauth_signature"] = signature

            # Build Authorization header
            auth_header = "OAuth " + ", ".join(
                f'{urllib.parse.quote(k, safe="")}="{urllib.parse.quote(v, safe="")}"'
                for k, v in sorted(oauth_params.items())
            )

            resp = requests.
post(
                "https://api.twitter.com/2/tweets",
                headers={
                    "Authorization": auth_header,
                    "Content-Type": "application/json",
                },
                json={"text": text},
                timeout=30,
            )

            if resp.status_code in (200, 201):
                tweet_id = resp.json().get("data", {}).get("id", "")
                logger.info(f"Tweet published: {tweet_id}")
                return True
            else:
                logger.warning(f"Twitter publish failed: {resp.status_code}")
                return False

        except Exception as e:
            logger.warning(f"Twitter publish error: {e}")
            return False

    def _publish_linkedin(self, post: dict) -> bool:
        """Publish to LinkedIn via API."""
        access_token = os.environ.get("LINKEDIN_ACCESS_TOKEN")
        if not access_token:
            logger.debug("LinkedIn token not configured. Post saved locally.")
            return False

        try:
            # Get user profile first
            profile_resp = requests.get(
                "https://api.linkedin.com/v2/userinfo",
                headers={"Authorization": f"Bearer {access_token}"},
                timeout=30,
            )
            if profile_resp.status_code != 200:
                return False

            user_sub = profile_resp.json().get("sub", "")
            if not user_sub:
                return False

            text = post["text"]
            if post.get("hashtags"):
                text += "\n\n" + " ".join(f"#{h}" for h in post["hashtags"])

            resp = requests.post(
                "https://api.linkedin.com/v2/ugcPosts",
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/json",
                    "X-Restli-Protocol-Version": "2.0.0",
                },
                json={
                    "author": f"urn:li
:person:{user_sub}",
                    "lifecycleState": "PUBLISHED",
                    "specificContent": {
                        "com.linkedin.ugc.ShareContent": {
                            "shareCommentary": {"text": text},
                            "shareMediaCategory": "NONE",
                        }
                    },
                    "visibility": {
                        "com.linkedin.ugc.MemberNetworkVisibility": "PUBLIC"
                    },
                },
                timeout=30,
            )

            if resp.status_code in (200, 201):
                logger.info("LinkedIn post published")
                return True
            else:
                logger.warning(f"LinkedIn publish failed: {resp.status_code}")
                return False

        except Exception as e:
            logger.warning(f"LinkedIn publish error: {e}")
            return False

    def _publish_reddit(self, post: dict) -> bool:
        """Publish to Reddit via API."""
        client_id = os.environ.get("REDDIT_CLIENT_ID")
        client_secret = os.environ.get("REDDIT_CLIENT_SECRET")

        if not all([client_id, client_secret]):
            logger.debug("Reddit credentials not configured. Post saved locally.")
            return False

        # Reddit requires user authentication for posting
        # Log for manual posting
        logger.info(
            f"Reddit post prepared for manual submission: {post['text'][:80]}..."
        )
        return False

    def _publish_pinterest(self, post: dict) -> bool:
        """Publish to Pinterest via API."""
        access_token = os.environ.get("PINTEREST_ACCESS_TOKEN")
        if not access_token:
            logger.debug("Pinterest token not configured. Post saved locally.")
            return False

        try:
            resp = requests.post(
                "https://api.pinterest.com/v5/pins",
                headers={
                    "Authorization": f"Bearer {access_token}",
       
             "Content-Type": "application/json",
                },
                json={
                    "title": post["text"][:100],
                    "description": post["text"],
                    "board_id": "",  # Would need board ID from config
                    "media_source": {
                        "source_type": "image_url",
                        "url": post.get("image_url", ""),
                    },
                },
                timeout=30,
            )

            if resp.status_code in (200, 201):
                logger.info("Pinterest pin published")
                return True
            else:
                logger.warning(f"Pinterest publish failed: {resp.status_code}")
                return False

        except Exception as e:
            logger.warning(f"Pinterest publish error: {e}")
            return False

    def _track_engagement(self):
        """Track engagement metrics on published posts."""
        # Check Twitter analytics if available
        access_token = os.environ.get("TWITTER_ACCESS_TOKEN")
        if access_token:
            try:
                # Get recent tweets metrics
                resp = requests.get(
                    "https://api.twitter.com/2/users/me/tweets",
                    headers={"Authorization": f"Bearer {access_token}"},
                    params={
                        "max_results": 10,
                        "tweet.fields": "public_metrics,created_at",
                    },
                    timeout=30,
                )
                if resp.status_code == 200:
                    tweets = resp.json().get("data", [])
                    for tweet in tweets:
                        metrics = tweet.get("public_metrics", {})
                        engagement = (
                            metrics.get("like_count", 0) +
                            metrics.get("retweet_count", 0) +
                            metrics.get("reply_count", 0)
                        )
    
                    # Log metrics
                        self.db.execute(
                            """UPDATE content_inventory SET metrics = json_set(
                                COALESCE(metrics, '{}'),
                                '$.likes', ?, '$.retweets', ?, '$.replies', ?, '$.engagement', ?
                            ) WHERE stream = ? AND content_type = 'social_post' AND metrics LIKE ?""",
                            (
                                metrics.get("like_count", 0),
                                metrics.get("retweet_count", 0),
                                metrics.get("reply_count", 0),
                                engagement,
                                self.STREAM,
                                f"%{tweet.get('id', '')}%",
                            )
                        )
            except Exception as e:
                logger.debug(f"Twitter analytics error: {e}")

    def _adjust_strategy(self):
        """Adjust content strategy based on engagement data."""
        # Get performance by content type
        posts = self.db.fetchall(
            "SELECT metrics FROM content_inventory WHERE stream = ? AND content_type = 'social_post' AND status = 'published' ORDER BY created_at DESC LIMIT 30",
            (self.STREAM,)
        )

        if not posts:
            return

        # Calculate average engagement by platform
        platform_engagement = {}
        for post in posts:
            try:
                metrics = json.loads(post.get("metrics", "{}"))
                platform = metrics.get("platform", "unknown")
                engagement = metrics.get("engagement", 0)
                if platform not in platform_engagement:
                    platform_engagement[platform] = []
                platform_engagement[platform].append(engagement)
            except (json.JSONDecodeError, TypeError):
                continue

        # Log insights
        for platform, engagements in platform_engagement.ite
ms():
            avg = sum(engagements) / max(len(engagements), 1)
            if avg > 5:  # Good engagement
                self.log_learning(
                    prediction=f"{platform} posts would generate engagement",
                    outcome=f"Average engagement: {avg:.1f} per post",
                    lesson=f"{platform} performing well (avg {avg:.1f} engagement). Continue current strategy.",
                    confidence=60,
                    tags=["social", platform, "engagement"],
                )