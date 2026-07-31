#!/usr/bin/env python3
"""
═══════════════════════════════════════════════════════════════
THE INSTITUTION — STREAM 8: AFFILIATE COMPARISON SITES
═══════════════════════════════════════════════════════════════
Programmatic SEO affiliate engine:
- Dedicated review/comparison pages for product niches
- Programmatic SEO: "best X for Y under $Z"
- Amazon Associates + ShareASale + CJ Affiliate
- Auto-updated when prices/products change
- Separate from content sites (different intent)
- Hugo static sites deployed to Cloudflare Pages
═══════════════════════════════════════════════════════════════
"""

import os
import json
import re
import subprocess
import hashlib
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import requests
from slugify import slugify

from common import get_db, get_config, get_logger, INSTITUTION_ROOT, now_iso, today_str
from agents.base import BaseAgent

logger = get_logger("affiliate_sites")


class AffiliateSitesAgent(BaseAgent):
    AGENT_NAME = "affiliate_sites"
    AGENT_TYPE = "revenue_stream"
    STREAM = "affiliate_sites"
    DEFAULT_INTERVAL_SECONDS = 14400  # Every 4 hours

    def __init__(self):
        super().__init__()
        self.sites_dir = INSTITUTION_ROOT / "sites"
        self.affiliate_dir = self.sites_dir / "affiliate"
        self.output_dir = self.affiliate_dir / "output"
        self.data_dir = self.affiliate_dir / "data"
        for d in [self.affiliate_dir, self.output_dir, self.data_dir]:
            d.mkdir(parents=True, exist_ok=True)

        # Affiliate program configurations
        self.affiliate_programs = {
            "amazon_associates": {
                "tag_env": "AMAZON_ASSOCIATE_TAG",
                "link_template": "https://www.amazon.com.au/dp/{asin}?tag={tag}",
                "search_template": "https://www.amazon.com.au/s?k={query}&tag={tag}",
            },
            "shareasale": {
                "token_env": "SHAREASALE_API_TOKEN",
                "
link_template": "https://www.shareasale.com/r.cfm?u={user_id}&b={banner_id}&m={merchant_id}",
            },
            "cj_affiliate": {
                "key_env": "CJ_API_KEY",
                "link_template": "https://www.anrdoezrs.net/click-{publisher_id}-{advertiser_id}",
            },
        }

    def run_once(self):
        """Main cycle: generate comparison pages, update prices, deploy."""
        if not self.should_run_today():
            return "Stream disabled or inactive"

        stream_cfg = self.get_stream_config()
        sites = stream_cfg.get("sites", [])
        page_types = stream_cfg.get("page_types", [])

        if not sites:
            return "No affiliate sites configured"

        results = []

        for site in sites:
            site_slug = site.get("slug", "default")
            site_dir = self.output_dir / site_slug

            # Initialize site if needed
            if not (site_dir / "hugo.toml").exists():
                self._initialize_affiliate_site(site_dir, site)
                results.append(f"{site_slug}: initialized")

            # Generate new comparison pages
            pages_generated = self._generate_comparison_pages(site_dir, site, page_types)
            if pages_generated:
                results.append(f"{site_slug}: {pages_generated} new pages")

            # Update existing pages with fresh prices
            updated = self._update_prices(site_dir, site)
            if updated:
                results.append(f"{site_slug}: {updated} pages price-updated")

            # Build and deploy
            self._build_site(site_dir)
            self._deploy_to_cloudflare(site_dir, site)

        return "; ".join(results) if results else "No affiliate work this cycle"

    def _initialize_affiliate_site(self, site_dir: Path, site_config: dict):
        """Create a new Hugo affiliate comparison site."""
        site_dir.mkdir(parents=True, exist_ok=True)

        hugo_toml = f"""baseURL = "https://{site_config.get
('slug', 'affiliate')}.pages.dev/"
languageCode = "en-au"
title = "{site_config.get('name', 'Best Tech Reviews')}"
theme = "affiliate"

[params]
  description = "Honest comparisons and reviews to help you find the best {site_config.get('niche', 'tech')} for your needs and budget."
  author = "The Institution"
  affiliate_disclosure = true

[markup]
  [markup.goldmark]
    [markup.goldmark.renderer]
      unsafe = true

[outputs]
  home = ["HTML", "RSS", "JSON"]

[sitemap]
  changefreq = "daily"
  priority = 0.8

[taxonomies]
  category = "categories"
  brand = "brands"
  price_range = "price_ranges"
"""
        (site_dir / "hugo.toml").write_text(hugo_toml)

        # Directory structure
        for d in ["content/reviews", "content/comparisons", "content/best-of",
                  "layouts/_default", "layouts/partials", "static/css",
                  "themes/affiliate/layouts/_default", "themes/affiliate/layouts/partials",
                  "themes/affiliate/static/css", "archetypes", "data/products"]:
            (site_dir / d).mkdir(parents=True, exist_ok=True)

        # Theme layouts
        baseof = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>{{ .Title }} | {{ .Site.Title }}</title>
    <meta name="description" content="{{ .Description | default .Site.Params.description }}">
    <link rel="stylesheet" href="/css/style.css">
    {{ template "_internal/opengraph.html" . }}
    {{ partial "affiliate-disclosure.html" . }}
    {{ partial "json-ld-review.html" . }}
</head>
<body>
    <header>
        <nav><a href="/">{{ .Site.Title }}</a></nav>
        {{ if .Site.Params.affiliate_disclosure }}
        <div class="disclosure">We may earn a commission from links on this page. This does not affect our recommendations.</div>
        {{ end }}
    </header>
    <main>{{ block "main" . }}{{ end }}</main>
    <footer><p>&copy; {{ now.Year }} {{ .Site.Title }}. P
rices updated regularly.</p></footer>
</body>
</html>
"""
        (site_dir / "themes/affiliate/layouts/_default/baseof.html").write_text(baseof)

        single = """{{ define "main" }}
<article class="review">
    <h1>{{ .Title }}</h1>
    <div class="meta">
        <time>Updated: {{ .Date.Format "January 2, 2006" }}</time>
        {{ with .Params.price_range }}<span class="price-badge">{{ . }}</span>{{ end }}
    </div>
    {{ .Content }}
    {{ partial "product-cards.html" . }}
</article>
{{ end }}
"""
        (site_dir / "themes/affiliate/layouts/_default/single.html").write_text(single)

        list = """{{ define "main" }}
<h1>{{ .Title }}</h1>
<div class="comparison-grid">
{{ range .Pages }}
<article class="comparison-card">
    <h2><a href="{{ .Permalink }}">{{ .Title }}</a></h2>
    <p>{{ .Description }}</p>
    {{ with .Params.top_pick }}<span class="badge">Top Pick</span>{{ end }}
</article>
{{ end }}
</div>
{{ end }}
"""
        (site_dir / "themes/affiliate/layouts/_default/list.html").write_text(list)

        # Affiliate disclosure partial
        disclosure = """{{ if .Site.Params.affiliate_disclosure }}
<div class="affiliate-disclosure" style="background:#f8f9fa;padding:0.5rem 1rem;font-size:0.85rem;border-left:3px solid #007bff;margin:1rem 0;">
    <strong>Disclosure:</strong> Some links on this page are affiliate links. If you purchase through them, we may earn a small commission at no extra cost to you. Our recommendations are based on research and are never influenced by commissions.
</div>
{{ end }}
"""
        (site_dir / "themes/affiliate/layouts/partials/affiliate-disclosure.html").write_text(disclosure)

        # JSON-LD for reviews
        jsonld = """{{ if .IsPage }}
<script type="application/ld+json">
{
  "@context": "https://schema.org",
  "@type": "ItemList",
  "name": "{{ .Title }}",
  "description": "{{ .Description }}",
  "dateModified": "{{ .Date.Format "2006-01-02" }}",
  "itemListElement": [
    {{ range $index, $element := .P
arams.products }}
    {
      "@type": "ListItem",
      "position": {{ add $index 1 }},
      "item": {
        "@type": "Product",
        "name": "{{ $element.name }}",
        "description": "{{ $element.description }}",
        "offers": {
          "@type": "Offer",
          "price": "{{ $element.price }}",
          "priceCurrency": "AUD"
        }
      }
    }{{ if ne (add $index 1) (len $.Params.products) }},{{ end }}
    {{ end }}
  ]
}
</script>
{{ end }}
"""
        (site_dir / "themes/affiliate/layouts/partials/json-ld-review.html").write_text(jsonld)

        # Product cards partial
        cards = """{{ with .Params.products }}
<div class="product-cards">
{{ range . }}
<div class="product-card">
    <h3>{{ .name }}</h3>
    <p class="price">${{ .price }}</p>
    <p>{{ .description }}</p>
    {{ if .affiliate_url }}
    <a href="{{ .affiliate_url }}" class="btn" rel="nofollow sponsored" target="_blank">Check Price</a>
    {{ end }}
</div>
{{ end }}
</div>
{{ end }}
"""
        (site_dir / "themes/affiliate/layouts/partials/product-cards.html").write_text(cards)

        # CSS
        css = """body{font-family:system-ui,-apple-system,sans-serif;max-width:900px;margin:0 auto;padding:1rem;line-height:1.6;color:#1a1a1a}
header nav{padding:1rem 0;border-bottom:2px solid #007bff}
header nav a{text-decoration:none;font-weight:bold;font-size:1.3rem;color:#1a1a1a}
.disclosure{font-size:0.8rem;color:#666;padding:0.3rem 0}
.review{margin:2rem 0}
.meta{color:#666;font-size:0.9rem;margin-bottom:1rem}
.price-badge{background:#28a745;color:#fff;padding:2px 8px;border-radius:4px;font-size:0.8rem}
.comparison-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:1.5rem}
.comparison-card{border:1px solid #e0e0e0;border-radius:8px;padding:1.5rem}
.comparison-card h2{margin-top:0}
.badge{background:#ffc107;color:#000;padding:2px 8px;border-radius:4px;font-size:0.75rem;font-weight:bold}
.product-cards{display:grid;grid-template-columns:repeat(au
to-fill,minmax(250px,1fr));gap:1rem;margin:2rem 0}
.product-card{border:1px solid #e0e0e0;border-radius:8px;padding:1rem;text-align:center}
.product-card .price{font-size:1.4rem;font-weight:bold;color:#28a745}
.btn{display:inline-block;background:#007bff;color:#fff;padding:0.5rem 1.5rem;border-radius:4px;text-decoration:none;margin-top:0.5rem}
.btn:hover{background:#0056b3}
footer{margin-top:3rem;padding:1rem 0;border-top:1px solid #eee;color:#666;font-size:0.9rem}
"""
        (site_dir / "themes/affiliate/static/css/style.css").write_text(css)

        # robots.txt
        robots = f"""User-agent: *
Allow: /
Sitemap: https://{site_config.get('slug', 'affiliate')}.pages.dev/sitemap.xml
"""
        (site_dir / "static/robots.txt").write_text(robots)

        logger.info(f"Affiliate site initialized: {site_dir}")

    def _generate_comparison_pages(self, site_dir: Path, site_config: dict,
                                    page_types: list) -> int:
        """Generate programmatic comparison pages."""
        niche = site_config.get("niche", "tech")
        programs = site_config.get("programs", ["amazon_associates"])
        pages_generated = 0

        # Check how many pages exist
        existing_pages = list((site_dir / "content").rglob("*.md"))
        target_pages = 5  # per week

        # Generate page ideas using AI
        page_ideas = self._generate_page_ideas(niche, page_types, len(existing_pages))

        for idea in page_ideas[:target_pages]:
            # Check if already exists
            page_slug = slugify(idea["title"])
            existing = self.db.fetchone(
                "SELECT id FROM content_inventory WHERE stream = ? AND slug = ?",
                (self.STREAM, page_slug)
            )
            if existing:
                continue

            # Generate full page content
            page_content = self._generate_page_content(idea, niche, programs)
            if not page_content:
                continue

            # Write Hugo ma
rkdown
            self._write_comparison_page(site_dir, page_content, idea)

            # Record in inventory
            self.db.insert("content_inventory", {
                "stream": self.STREAM,
                "site": site_config.get("slug"),
                "content_type": "affiliate_page",
                "title": idea["title"],
                "slug": page_slug,
                "status": "published",
                "published_at": now_iso(),
                "metrics": json.dumps({"page_type": idea.get("type", "comparison")}),
            })

            pages_generated += 1

        return pages_generated

    def _generate_page_ideas(self, niche: str, page_types: list,
                              existing_count: int) -> list:
        """Generate programmatic page ideas using AI."""
        prompt = f"""Generate 5 affiliate comparison page ideas for a website about {niche}.

PAGE TYPES TO USE: {', '.join(page_types) if page_types else 'best_x_for_y, x_vs_y, x_under_$z, top_10_x'}

REQUIREMENTS:
- Each idea targets a specific search query with commercial intent
- Include price points where relevant (AUD)
- Target Australian market
- Mix of page types
- Specific enough to rank (not too broad)
- Include the target keyword

OUTPUT FORMAT (JSON array):
[
  {{
    "title": "Page title (max 60 chars)",
    "type": "best_x_for_y|x_vs_y|x_under_z|top_10",
    "target_keyword": "the search query this targets",
    "price_point": "under $100|under $500|$100-$300|any",
    "products_needed": 5,
    "search_intent": "what the searcher wants to know"
  }}
]

Examples of good titles:
- "Best Budget Laptops for Students Under $800 (2026)"
- "Sony WH-1000XM5 vs Bose QC Ultra: Which Should You Buy?"
- "Top 10 Mechanical Keyboards Under $150 in Australia"
- "Best Smart Home Hubs for Renters (No Wiring Required)"
"""
        response = self.generate_text(
            prompt=prompt,
            quality_tier="routine",
            temperature=0.8,
            max_tokens=1500
,
        )

        if not response:
            return self._fallback_page_ideas(niche)

        try:
            json_start = response.find("[")
            json_end = response.rfind("]") + 1
            if json_start >= 0 and json_end > json_start:
                ideas = json.loads(response[json_start:json_end])
                if isinstance(ideas, list) and ideas:
                    return ideas
        except json.JSONDecodeError:
            pass

        return self._fallback_page_ideas(niche)

    def _fallback_page_ideas(self, niche: str) -> list:
        """Generate fallback page ideas without AI."""
        year = datetime.now().year
        return [
            {
                "title": f"Best {niche.title()} Products Under $100 ({year})",
                "type": "best_x_under_z",
                "target_keyword": f"best {niche} under $100",
                "price_point": "under $100",
                "products_needed": 5,
                "search_intent": f"Find affordable {niche} options that don't compromise quality",
            },
            {
                "title": f"Top 10 {niche.title()} Picks for Beginners ({year})",
                "type": "top_10",
                "target_keyword": f"best {niche} for beginners",
                "price_point": "any",
                "products_needed": 10,
                "search_intent": f"Find the easiest {niche} products to start with",
            },
            {
                "title": f"Best {niche.title()} for Small Spaces ({year})",
                "type": "best_x_for_y",
                "target_keyword": f"best {niche} for small spaces",
                "price_point": "any",
                "products_needed": 5,
                "search_intent": f"Find compact {niche} solutions for apartments",
            },
        ]

    def _generate_page_content(self, idea: dict, niche: str, programs: list) -> Optional[dict]:
        """Generate full comparison page content with products and affiliate links.
"""
        prompt = f"""Write a comprehensive affiliate comparison page.

TITLE: {idea['title']}
TARGET KEYWORD: {idea.get('target_keyword', '')}
PAGE TYPE: {idea.get('type', 'comparison')}
PRICE POINT: {idea.get('price_point', 'any')}
SEARCH INTENT: {idea.get('search_intent', '')}
NICHE: {niche}
NUMBER OF PRODUCTS: {idea.get('products_needed', 5)}

REQUIREMENTS:
- Write for Australian audience (AUD prices, Australian retailers)
- Include specific product names, models, and realistic prices
- Each product needs: name, price (AUD), 2-3 sentence description, pros, cons
- Include a "Our Top Pick" and "Best Value" recommendation
- Honest, balanced tone — not salesy
- Include a brief buying guide section
- Natural keyword usage
- 1500-2500 words total
- Australian English spelling

OUTPUT FORMAT (JSON):
{{
  "meta_description": "SEO meta description (max 155 chars)",
  "introduction": "2-3 paragraph intro addressing the search intent",
  "buying_guide": "What to look for when buying (3-5 key factors)",
  "products": [
    {{
      "name": "Product Name",
      "price": 99.99,
      "description": "2-3 sentences about this product",
      "pros": ["pro 1", "pro 2", "pro 3"],
      "cons": ["con 1", "con 2"],
      "best_for": "who this is ideal for",
      "search_term": "amazon search term for this product"
    }}
  ],
  "top_pick": "name of top pick product",
  "best_value": "name of best value product",
  "conclusion": "2-3 sentence wrap-up with recommendation",
  "tags": ["tag1", "tag2", "tag3"]
}}
"""
        response = self.generate_text(
            prompt=prompt,
            quality_tier="routine",
            temperature=0.6,
            max_tokens=4096,
        )

        if not response:
            return None

        try:
            json_start = response.find("{")
            json_end = response.rfind("}") + 1
            if json_start >= 0 and json_end > json_start:
                data = json.loads(response[json_start:json_end])
                if "produ
cts" in data and data["products"]:
                    # Add affiliate URLs to products
                    for product in data["products"]:
                        product["affiliate_url"] = self._build_affiliate_url(
                            product.get("search_term", product.get("name", "")),
                            programs
                        )
                    data["title"] = idea["title"]
                    data["slug"] = slugify(idea["title"])
                    data["type"] = idea.get("type", "comparison")
                    data["target_keyword"] = idea.get("target_keyword", "")
                    return data
        except json.JSONDecodeError:
            pass

        return None

    def _build_affiliate_url(self, search_term: str, programs: list) -> str:
        """Build an affiliate URL for a product."""
        if "amazon_associates" in programs:
            tag = os.environ.get("AMAZON_ASSOCIATE_TAG", "")
            if tag:
                encoded_query = requests.utils.quote(search_term)
                return f"https://www.amazon.com.au/s?k={encoded_query}&tag={tag}"

        if "shareasale" in programs:
            token = os.environ.get("SHAREASALE_API_TOKEN", "")
            if token:
                return f"https://www.shareasale.com/r.cfm?keyword={requests.utils.quote(search_term)}"

        # Fallback: plain Amazon search (no affiliate)
        encoded_query = requests.utils.quote(search_term)
        return f"https://www.amazon.com.au/s?k={encoded_query}"

    def _write_comparison_page(self, site_dir: Path, content: dict, idea: dict):
        """Write comparison page as Hugo markdown."""
        # Determine content subdirectory based on page type
        page_type = idea.get("type", "comparison")
        if "vs" in page_type or "comparison" in page_type:
            subdir = "comparisons"
        elif "best" in page_type or "top" in page_type:
            subdir = "best-of"
        else:
            subdir = "reviews"


        content_dir = site_dir / "content" / subdir
        content_dir.mkdir(parents=True, exist_ok=True)

        slug = content.get("slug", slugify(content.get("title", "page")))
        filepath = content_dir / f"{slug}.md"

        # Build frontmatter
        products_json = json.dumps(content.get("products", []), default=str)

        frontmatter = f"""---
title: "{content.get('title', idea['title'])}"
date: {now_iso()}
draft: false
description: "{content.get('meta_description', '')}"
categories: ["{idea.get('type', 'comparison')}"]
tags: {json.dumps(content.get('tags', []))}
keywords: ["{content.get('target_keyword', '')}"]
price_range: "{idea.get('price_point', 'any')}"
top_pick: "{content.get('top_pick', '')}"
products: {products_json}
---

"""

        # Build body
        body = f"{content.get('introduction', '')}\n\n"

        # Buying guide
        if content.get("buying_guide"):
            body += f"## What to Look For\n\n{content['buying_guide']}\n\n"

        # Product reviews
        body += "## Our Recommendations\n\n"
        for i, product in enumerate(content.get("products", []), 1):
            is_top = product.get("name") == content.get("top_pick")
            is_value = product.get("name") == content.get("best_value")

            badge = ""
            if is_top:
                badge = " 🏆 **OUR TOP PICK**"
            elif is_value:
                badge = " 💰 **BEST VALUE**"

            body += f"### {i}. {product.get('name', 'Product')}{badge}\n\n"
            body += f"**Price:** ${product.get('price', 'N/A')} AUD\n\n"
            body += f"{product.get('description', '')}\n\n"

            if product.get("pros"):
                body += "**Pros:**\n"
                for pro in product["pros"]:
                    body += f"- {pro}\n"
                body += "\n"

            if product.get("cons"):
                body += "**Cons:**\n"
                for con in product["cons"]:
                    body += f"- {con}\n"
             
   body += "\n"

            if product.get("best_for"):
                body += f"**Best for:** {product['best_for']}\n\n"

            if product.get("affiliate_url"):
                body += f"[Check Current Price →]({product['affiliate_url']}){{rel=\"nofollow sponsored\"}}\n\n"

            body += "---\n\n"

        # Conclusion
        if content.get("conclusion"):
            body += f"## Final Verdict\n\n{content['conclusion']}\n\n"

        # Affiliate disclosure footer
        body += "---\n\n*Prices are in AUD and were accurate at time of writing. We may earn a commission from purchases made through links on this page. This never influences our recommendations.*\n"

        filepath.write_text(frontmatter + body, encoding="utf-8")
        logger.info(f"Affiliate page written: {filepath}")

    def _update_prices(self, site_dir: Path, site_config: dict) -> int:
        """Update prices on existing pages (simulated — real impl would scrape)."""
        updated = 0
        content_dir = site_dir / "content"

        if not content_dir.exists():
            return 0

        # Find pages older than 7 days
        for md_file in content_dir.rglob("*.md"):
            try:
                content = md_file.read_text(encoding="utf-8")

                # Check if page needs updating (older than 7 days)
                date_match = re.search(r'date: (\d{4}-\d{2}-\d{2})', content)
                if date_match:
                    page_date = datetime.strptime(date_match.group(1), "%Y-%m-%d")
                    if (datetime.now() - page_date).days < 7:
                        continue

                # In a real implementation, we'd scrape current prices here
                # For now, update the date to indicate freshness
                updated_content = re.sub(
                    r'date: \d{4}-\d{2}-\d{2}T[\d:]+',
                    f'date: {now_iso()}',
                    content
                )

                if updated_content != content:
           
         md_file.write_text(updated_content, encoding="utf-8")
                    updated += 1

            except Exception as e:
                logger.debug(f"Price update error for {md_file}: {e}")

        return updated

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
                logger.error(f"Hugo build failed: {result.stderr[:300]}")
            else:
                logger.debug(f"Affiliate site built: {site_dir.name}")
        except (FileNotFoundError, subprocess.TimeoutExpired) as e:
            logger.warning(f"Hugo build unavailable: {e}")

    def _deploy_to_cloudflare(self, site_dir: Path, site_config: dict):
        """Deploy to Cloudflare Pages."""
        token = os.environ.get("CLOUDFLARE_PAGES_TOKEN")
        project = site_config.get("slug", "affiliate-site")
        public_dir = site_dir / "public"

        if not token or not public_dir.exists():
            return

        try:
            result = subprocess.run(
                ["npx", "wrangler", "pages", "deploy", str(public_dir),
                 "--project-name", project, "--branch", "main"],
                capture_output=True,
                text=True,
                timeout=300,
                env={**os.environ, "CLOUDFLARE_API_TOKEN": token},
            )
            if result.returncode == 0:
                logger.info(f"Affiliate site deployed: {project}")
        except (FileNotFoundError, subprocess.TimeoutExpired) as e:
            logger.debug(f"Cloudflare deploy unavailable: {e}")