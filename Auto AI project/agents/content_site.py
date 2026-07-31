#!/usr/bin/env python3
"""
═══════════════════════════════════════════════════════════════
THE INSTITUTION — STREAM 1: SEO CONTENT SITES
═══════════════════════════════════════════════════════════════
Full SEO content engine:
- Auto-generates 2-5 articles/day per site
- Hugo static sites deployed to Cloudflare Pages (free)
- Monetised: Amazon Associates, ShareASale, AdSense
- Self-expanding keyword bank
- Internal linking, sitemap.xml, robots.txt, RSS, JSON-LD
- Content bootstrap: 25 articles on first run
- Tracks traffic via Cloudflare Analytics API
═══════════════════════════════════════════════════════════════
"""

import os
import json
import subprocess
import shutil
from datetime import datetime
from pathlib import Path
from typing import Optional

import requests
from slugify import slugify

from common import get_db, get_config, get_logger, INSTITUTION_ROOT, now_iso, today_str
from agents.base import BaseAgent

logger = get_logger("content_site")


class ContentSiteAgent(BaseAgent):
    AGENT_NAME = "content_site"
    AGENT_TYPE = "revenue_stream"
    STREAM = "content_sites"
    DEFAULT_INTERVAL_SECONDS = 7200  # Every 2 hours

    def __init__(self):
        super().__init__()
        self.sites_dir = INSTITUTION_ROOT / "sites"
        self.output_dir = self.sites_dir / "output"
        self.templates_dir = self.sites_dir / "templates"
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.templates_dir.mkdir(parents=True, exist_ok=True)

    def run_once(self):
        """Main execution cycle: generate content, build sites, deploy."""
        if not self.should_run_today():
            return "Stream disabled or inactive"

        stream_cfg = self.get_stream_config()
        sites = stream_cfg.get("sites", [])
        if not sites:
            return "No sites configured"

        results = []
        articles_per_day = stream_cfg.get("schedule", {}).get("articles_per_day", 3)

        for site in sites:
            site_slug = site.get("slug", "default")
            site_dir = self.output_dir / site_slug

            # Initialize site if needed
            if not (site_dir / "hugo.toml").exists():
                self._initialize_hugo_site(site_dir, site)
                # Bootstrap content
                self._bootstrap_content(site_dir, site, count=25)
                results.append(f"{site_slug}: initialized with 25 articles")
            else:
                # Generate daily articles
                generated = 0
                for i in range(articles_per_day):
                    article = self._generate_article(site)
                    if article:
                        self._write_article(site_dir, article, site)
                        generated += 1

                # Build and deploy
                self._build_site(site_dir)
                self._deploy_to_cloudflare(site_dir, site)

                # Update sitemap and RSS
                self._update_sitemap(site_dir, site)
                self._update_rss(site_dir, site)

                results.append(f"{site_slug}: {generated} articles generated and deployed")

                # Expand keyword bank
                self._expand_keywords(site)

        # Track metrics
        self._track_traffic(sites)

        return "; ".join(results)

    def _initialize_hugo_site(self, site_dir: Path, site_config: dict):
        """Create a new Hugo site with proper structure."""
        site_dir.mkdir(parents=True, exist_ok=True)

        # Hugo configuration
        hugo_toml = f"""baseURL = "https://{site_config.get('cloudflare_project', site_config['slug'])}.pages.dev/"
languageCode = "en-au"
title = "{site_config['name']}"
theme = "institution"

[params]
  description = "Expert guides and reviews for {site_config.get('niche', 'technology')}"
  author = "The Institution"

[markup]
  [markup.goldmark]
    [markup.goldmark.renderer]
      unsafe = true

[outputs]
  home = ["HTML", "RSS", "JSON"]

[sitemap]
  changefreq = "weekly"
  priority = 0.7

[taxonomies]
  category = "categories"
  tag = "tags"
"""
        (site_dir / "hugo.toml").write_text(hugo_toml)

        # Create directory structure
        for d in ["content/posts", "content/categories", "layouts/_default",
                  "layouts/partials", "static/css", "static/js", "static/images",
                  "themes/institution/layouts/_default", "themes/institution/layouts/partials",
                  "themes/institution/static/css", "archetypes"]:
            (site_dir / d).mkdir(parents=True, exist_ok=True)

        # Minimal theme layouts
        baseof = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>{{ .Title }} | {{ .Site.Title }}</title>
    <meta name="description" content="{{ .Description | default .Site.Params.description }}">
    <link rel="stylesheet" href="/css/style.css">
    {{ template "_internal/opengraph.html" . }}
    {{ template "_internal/schema.html" . }}
    {{ partial "json-ld.html" . }}
</head>
<body>
    <header><nav><a href="/">{{ .Site.Title }}</a></nav></header>
    <main>{{ block "main" . }}{{ end }}</main>
    <footer><p>&copy; {{ now.Year }} {{ .Site.Title }}. All rights reserved.</p></footer>
</body>
</html>
"""
        (site_dir / "themes/institution/layouts/_default/baseof.html").write_text(baseof)

        single = """{{ define "main" }}
<article>
    <h1>{{ .Title }}</h1>
    <time>{{ .Date.Format "January 2, 2006" }}</time>
    {{ .Content }}
</article>
{{ end }}
"""
        (site_dir / "themes/institution/layouts/_default/single.html").write_text(single)

        list = """{{ define "main" }}
<h1>{{ .Title }}</h1>
{{ range .Pages }}
<article>
    <h2><a href="{{ .Permalink }}">{{ .Title }}</a></h2>
    <p>{{ .Summary }}</p>
</article>
{{ end }}
{{ end }}
"""
        (site_dir / "themes/institution/layouts/_default/list.html").write_text(list)

        # JSON-LD partial
        jsonld = """{{ if .IsPage }}
<script type="application/ld+json">
{
  "@context": "https://schema.org",
  "@type": "Article",
  "headline": "{{ .Title }}",
  "description": "{{ .Description }}",
  "datePublished": "{{ .Date.Format "2006-01-02" }}",
  "author": {"@type": "Organization", "name": "{{ .Site.Title }}"}
}
</script>
{{ end }}
"""
        (site_dir / "themes/institution/layouts/partials/json-ld.html").write_text(jsonld)

        # CSS
        css = """body{font-family:system-ui,-apple-system,sans-serif;max-width:800px;margin:0 auto;padding:1rem;line-height:1.6;color:#1a1a1a}
header nav{padding:1rem 0;border-bottom:1px solid #eee}
header nav a{text-decoration:none;font-weight:bold;color:#1a1a1a}
article{margin:2rem 0}
footer{margin-top:3rem;padding:1rem 0;border-top:1px solid #eee;color:#666;font-size:0.9rem}
"""
        (site_dir / "themes/institution/static/css/style.css").write_text(css)

        # robots.txt
        robots = f"""User-agent: *
Allow: /
Sitemap: https://{site_config.get('cloudflare_project', site_config['slug'])}.pages.dev/sitemap.xml
"""
        (site_dir / "static/robots.txt").write_text(robots)

        # Archetype
        archetype = """---
title: "{{ replace .Name "-" " " | title }}"
date: {{ .Date }}
draft: false
categories: []
tags: []
description: ""
---
"""
        (site_dir / "archetypes/default.md").write_text(archetype)

        logger.info(f"Hugo site initialized: {site_dir}")

    def _bootstrap_content(self, site_dir: Path, site_config: dict, count: int = 25):
        """Generate initial batch of articles for a new site."""
        categories = site_config.get("categories", ["general"])
        keywords = site_config.get("seed_keywords", [])

        for i in range(count):
            category = categories[i % len(categories)]
            keyword = keywords[i] if i < len(keywords) else f"best {category} guide {i+1}"

            article = self._generate_article(site_config, keyword=keyword, category=category)
            if article:
                self._write_article(site_dir, article, site_config)

        logger.info(f"Bootstrapped {count} articles for {site_config['slug']}")

    def _generate_article(self, site_config: dict, keyword: str = None,
                          category: str = None) -> Optional[dict]:
        """Generate a single SEO article using AI."""
        niche = site_config.get("niche", "technology")
        categories = site_config.get("categories", ["general"])

        if not keyword:
            # Get next keyword from bank
            kw_row = self.db.fetchone(
                "SELECT keyword FROM keywords WHERE site = ? AND status = 'discovered' ORDER BY RANDOM() LIMIT 1",
                (site_config["slug"],)
            )
            if kw_row:
                keyword = kw_row["keyword"]
                self.db.execute(
                    "UPDATE keywords SET status = 'targeted' WHERE keyword = ? AND site = ?",
                    (keyword, site_config["slug"])
                )
            else:
                keyword = f"complete guide to {niche} {datetime.now().strftime('%Y')}"

        if not category:
            category = categories[0] if categories else "general"

        min_words = site_config.get("content", {}).get("min_word_count", 1500)

        prompt = f"""Write a comprehensive, helpful SEO article.

TOPIC/KEYWORD: {keyword}
NICHE: {niche}
CATEGORY: {category}
MINIMUM WORD COUNT: {min_words}

REQUIREMENTS:
- Write for a human reader who needs practical help
- Include specific, actionable advice (not generic filler)
- Use H2 and H3 subheadings for structure
- Include a brief introduction (2-3 sentences)
- Include a conclusion with key takeaways
- Natural keyword usage (not stuffed)
- Australian English spelling
- No AI-sounding phrases like "in today's digital landscape"
- Include specific product names, prices, or examples where relevant
- Write as if you're an expert helping a friend

OUTPUT FORMAT (exact):
TITLE: [article title]
DESCRIPTION: [meta description, max 155 chars]
TAGS: [comma-separated tags]
---
[article body in markdown]
"""

        response = self.generate_text(
            prompt=prompt,
            quality_tier="routine",
            temperature=0.7,
            max_tokens=4096,
        )

        if not response or len(response) < 200:
            return None

        # Parse response
        title = keyword.title()
        description = f"Expert guide on {keyword}"
        tags = [category, niche]
        body = response

        if "TITLE:" in response:
            lines = response.split("\n")
            for i, line in enumerate(lines):
                if line.startswith("TITLE:"):
                    title = line[6:].strip()
                elif line.startswith("DESCRIPTION:"):
                    description = line[12:].strip()
                elif line.startswith("TAGS:"):
                    tags = [t.strip() for t in line[5:].split(",")]
                elif line.strip() == "---":
                    body = "\n".join(lines[i+1:])
                    break

        return {
            "title": title,
            "slug": slugify(title),
            "description": description,
            "tags": tags,
            "category": category,
            "body": body.strip(),
            "keyword": keyword,
            "word_count": len(body.split()),
        }

    def _write_article(self, site_dir: Path, article: dict, site_config: dict):
        """Write article as Hugo markdown file."""
        posts_dir = site_dir / "content" / "posts"
        posts_dir.mkdir(parents=True, exist_ok=True)

        # Avoid overwriting
        slug = article["slug"]
        filepath = posts_dir / f"{slug}.md"
        counter = 1
        while filepath.exists():
            filepath = posts_dir / f"{slug}-{counter}.md"
            counter += 1

        # Build internal links
        internal_links = self._get_internal_links(site_dir, article, count=5)

        frontmatter = f"""---
title: "{article['title']}"
date: {now_iso()}
draft: false
description: "{article['description']}"
categories: ["{article['category']}"]
tags: {json.dumps(article['tags'])}
keywords: ["{article['keyword']}"]
---

"""
        content = frontmatter + article["body"]

        # Append internal links section
        if internal_links:
            content += "\n\n## Related Articles\n\n"
            for link in internal_links:
                content += f"- [{link['title']}]({link['url']})\n"

        # Append affiliate section if configured
        monetization = site_config.get("monetization", [])
        if "amazon_associates" in monetization:
            associate_tag = os.environ.get("AMAZON_ASSOCIATE_TAG", "")
            if associate_tag:
                content += f"\n\n---\n*Some links in this article are affiliate links. We may earn a commission at no extra cost to you.*\n"

        filepath.write_text(content, encoding="utf-8")

        # Record in content inventory
        self.db.insert("content_inventory", {
            "stream": self.STREAM,
            "site": site_config["slug"],
            "content_type": "article",
            "title": article["title"],
            "slug": filepath.stem,
            "word_count": article["word_count"],
            "status": "published",
            "published_at": now_iso(),
        })

        # Update keyword status
        self.db.execute(
            "UPDATE keywords SET status = 'published' WHERE keyword = ? AND site = ?",
            (article["keyword"], site_config["slug"])
        )

    def _get_internal_links(self, site_dir: Path, current_article: dict, count: int = 5) -> list:
        """Find existing articles to link to."""
        posts_dir = site_dir / "content" / "posts"
        if not posts_dir.exists():
            return []

        links = []
        for md_file in posts_dir.glob("*.md"):
            if md_file.stem == current_article["slug"]:
                continue
            # Read title from frontmatter
            try:
                content = md_file.read_text(encoding="utf-8")
                for line in content.split("\n")[:10]:
                    if line.startswith("title:"):
                        title = line.split(":", 1)[1].strip().strip('"')
                        links.append({"title": title, "url": f"/posts/{md_file.stem}/"})
                        break
            except Exception:
                continue
            if len(links) >= count:
                break

        return links

    def _build_site(self, site_dir: Path):
        """Build the Hugo site."""
        try:
            result = subprocess.run(
                ["hugo", "--minify", "--destination", "public"],
                cwd=str(site_dir),
                capture_output=True,
                text=True,
                timeout=120,
            )
            if result.returncode != 0:
                logger.error(f"Hugo build failed: {result.stderr[:500]}")
            else:
                logger.debug(f"Hugo build successful: {site_dir.name}")
        except FileNotFoundError:
            logger.error("Hugo not installed. Cannot build site.")
        except subprocess.TimeoutExpired:
            logger.error("Hugo build timed out.")

    def _deploy_to_cloudflare(self, site_dir: Path, site_config: dict):
        """Deploy built site to Cloudflare Pages."""
        token = os.environ.get("CLOUDFLARE_PAGES_TOKEN")
        project = site_config.get("cloudflare_project", site_config["slug"])
        public_dir = site_dir / "public"

        if not token:
            logger.debug("No Cloudflare Pages token. Skipping deploy.")
            return

        if not public_dir.exists():
            logger.warning(f"No public directory to deploy for {project}")
            return

        try:
            # Use wrangler CLI if available, otherwise API
            result = subprocess.run(
                ["npx", "wrangler", "pages", "deploy", str(public_dir),
                 "--project-name", project, "--branch", "main"],
                capture_output=True,
                text=True,
                timeout=300,
                env={**os.environ, "CLOUDFLARE_API_TOKEN": token},
            )
            if result.returncode == 0:
                logger.info(f"Deployed {project} to Cloudflare Pages")
            else:
                logger.warning(f"Cloudflare deploy issue: {result.stderr[:200]}")
        except (FileNotFoundError, subprocess.TimeoutExpired) as e:
            logger.warning(f"Cloudflare deploy unavailable: {e}")

    def _update_sitemap(self, site_dir: Path, site_config: dict):
        """Hugo generates sitemap automatically, but ensure it exists."""
        # Hugo handles this via [sitemap] config. Nothing extra needed.
        pass

    def _update_rss(self, site_dir: Path, site_config: dict):
        """Hugo generates RSS automatically via outputs config."""
        pass

    def _expand_keywords(self, site_config: dict):
        """Use AI to discover new keywords for the site."""
        niche = site_config.get("niche", "technology")
        categories = site_config.get("categories", [])

        # Only expand if running low
        existing = self.db.fetchone(
            "SELECT COUNT(*) as cnt FROM keywords WHERE site = ? AND status = 'discovered'",
            (site_config["slug"],)
        )
        if existing and existing["cnt"] > 20:
            return

        prompt = f"""Generate 15 long-tail SEO keywords for a website about {niche}.
Categories: {', '.join(categories)}

Requirements:
- Each keyword should be a specific search query (4-8 words)
- Mix of informational and commercial intent
- Include "best", "how to", "vs", "under $", "for beginners" patterns
- Australian market relevance where applicable
- One keyword per line, no numbering

Example format:
best budget smart home hub australia
how to set up home automation cheaply
"""
        response = self.generate_text(prompt, quality_tier="routine", temperature=0.8)
        if response:
            for line in response.strip().split("\n"):
                keyword = line.strip().lstrip("0123456789.-) ")
                if keyword and len(keyword) > 10:
                    self.db.execute(
                        "INSERT OR IGNORE INTO keywords (stream, site, keyword, status) VALUES (?, ?, ?, 'discovered')",
                        (self.STREAM, site_config["slug"], keyword)
                    )

    def _track_traffic(self, sites: list):
        """Track traffic via Cloudflare Analytics API if available."""
        account_id = os.environ.get("CLOUDFLARE_ACCOUNT_ID")
        api_token = os.environ.get("CLOUDFLARE_API_TOKEN")

        if not account_id or not api_token:
            return

        for site in sites:
            project = site.get("cloudflare_project", site["slug"])
            try:
                resp = requests.get(
                    f"https://api.cloudflare.com/client/v4/accounts/{account_id}/pages/projects/{project}/deployments",
                    headers={"Authorization": f"Bearer {api_token}"},
                    timeout=30,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    deployments = data.get("result", [])
                    if deployments:
                        latest = deployments[0]
                        self.db.insert("content_inventory", {
                            "stream": self.STREAM,
                            "site": site["slug"],
                            "content_type": "deployment",
                            "title": f"Deploy {latest.get('id', 'unknown')[:8]}",
                            "status": "published",
                            "metrics": json.dumps({"deployments": len(deployments)}),
                        })
            except Exception as e:
                logger.debug(f"Traffic tracking unavailable for {project}: {e}")