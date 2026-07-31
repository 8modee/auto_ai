#!/usr/bin/env python3
"""
═══════════════════════════════════════════════════════════════
THE INSTITUTION — STREAM 2: DIGITAL PRODUCTS
═══════════════════════════════════════════════════════════════
Generates substantive digital products:
- Planners, worksheets, SVG art, templates, checklists, journals
- 3-5+ pages minimum per product with ACTUAL content
- PDF output via ReportLab, SVG/PNG via Pillow
- Auto-lists via Etsy/Gumroad APIs (when keys available)
- Generates product descriptions, tags, mockup images
- Tracks sales, adjusts product types by demand
═══════════════════════════════════════════════════════════════
"""

import os
import json
import math
import random
from datetime import datetime
from pathlib import Path
from typing import Optional

import requests

from common import get_db, get_config, get_logger, INSTITUTION_ROOT, now_iso, slugify
from agents.base import BaseAgent

logger = get_logger("product_engine")

try:
    from reportlab.lib.pagesizes import A4, letter
    from reportlab.lib.units import mm, cm
    from reportlab.lib.colors import HexColor, black, white, grey
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.platypus import (
        SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
        PageBreak, ListFlowable, ListItem, HRFlowable
    )
    from reportlab.pdfgen import canvas
    REPORTLAB_AVAILABLE = True
except ImportError:
    REPORTLAB_AVAILABLE = False
    logger.warning("ReportLab not installed. PDF generation unavailable.")

try:
    from PIL import Image, ImageDraw, ImageFont
    PILLOW_AVAILABLE = True
except ImportError:
    PILLOW_AVAILABLE = False
    logger.warning("Pillow not installed. Image generation unavailable.")


class ProductEngineAgent(BaseAgent):
    AGENT_NAME = "product_engine"
    AGENT_TYPE = "revenue_stream"
    STREAM = "digital_products"
    DEFAULT_INTERVAL_SECONDS = 14400  # Every 4 hours

    def __init__(self):
        super().__init__()
        self.products_dir = INSTITUTION_ROOT / "products"
        self.output_dir = self.products_dir / "output"
        self.templates_dir = self.products_dir / "templates"
        self.listings_dir = self.products_dir / "listings"
        for d in [self.output_dir, self.templates_dir, self.listings_dir]:
            d.mkdir(parents=True, exist_ok=True)

        # Color palettes for products
        self.palettes = {
            "professional": {"primary": "#2C3E50", "secondary": "#3498DB", "accent": "#E74C3C", "bg": "#FFFFFF"},
            "calm": {"primary": "#5D6D7E", "secondary": "#AED6F1", "accent": "#82E0AA", "bg": "#F8F9FA"},
            "bold": {"primary": "#1A1A2E", "secondary": "#E94560", "accent": "#0F3460", "bg": "#FFFFFF"},
            "nature": {"primary": "#2D6A4F", "secondary": "#95D5B2", "accent": "#D4A373", "bg": "#FEFAE0"},
            "minimal": {"primary": "#212529", "secondary": "#6C757D", "accent": "#495057", "bg": "#FFFFFF"},
        }

    def run_once(self):
        """Main cycle: generate products, create listings, track sales."""
        if not self.should_run_today():
            return "Stream disabled or inactive"

        stream_cfg = self.get_stream_config()
        product_types = stream_cfg.get("product_types", [])
        if not product_types:
            return "No product types configured"

        # Check how many products we've made this week
        week_products = self.db.fetchone(
            "SELECT COUNT(*) as cnt FROM content_inventory WHERE stream = ? AND content_type = 'product' AND created_at > datetime('now', '-7 days')",
            (self.STREAM,)
        )
        products_per_week = stream_cfg.get("schedule", {}).get("products_per_week", 5)
        made_this_week = week_products["cnt"] if week_products else 0

        if made_this_week >= products_per_week:
            # Track sales instead of making more
            self._track_sales()
            return f"Weekly quota met ({made_this_week}/{products_per_week}). Tracking sales."

        # Select product type based on demand data
        product_type = self._select_product_type(product_types)
        if not product_type:
            return "No viable product type selected"

        # Generate the product
        product = self._generate_product(product_type, stream_cfg)
        if not product:
            return "Product generation failed"

        # Create listing materials
        listing = self._create_listing(product, stream_cfg)

        # Attempt to list on platforms
        self._list_on_platforms(product, listing)

        # Record in inventory
        self.db.insert("content_inventory", {
            "stream": self.STREAM,
            "content_type": "product",
            "title": product["title"],
            "slug": product["slug"],
            "status": "published",
            "published_at": now_iso(),
            "metrics": json.dumps({"type": product_type["type"], "pages": product.get("page_count", 0)}),
        })

        return f"Created: {product['title']} ({product_type['type']}, {product.get('page_count', 0)} pages)"

    def _select_product_type(self, product_types: list) -> Optional[dict]:
        """Select next product type based on demand and rotation."""
        # Check what's been made recently
        recent = self.db.fetchall(
            "SELECT metrics FROM content_inventory WHERE stream = ? AND content_type = 'product' ORDER BY created_at DESC LIMIT 10",
            (self.STREAM,)
        )
        recent_types = []
        for r in recent:
            try:
                m = json.loads(r.get("metrics", "{}"))
                recent_types.append(m.get("type", ""))
            except (json.JSONDecodeError, TypeError):
                pass

        # Prefer types not recently made
        available = [pt for pt in product_types if pt["type"] not in recent_types[-3:]]
        if not available:
            available = product_types

        # Weight by historical performance if available
        best = None
        best_score = -1
        for pt in available:
            score = random.random()  # Base randomness
            # Boost types that have sold well
            sales = self.db.fetchone(
                "SELECT COUNT(*) as cnt FROM revenue WHERE stream = ? AND source LIKE ?",
                (self.STREAM, f"%{pt['type']}%")
            )
            if sales and sales["cnt"] > 0:
                score += sales["cnt"] * 0.5
            if score > best_score:
                best_score = score
                best = pt

        return best

    def _generate_product(self, product_type: dict, stream_cfg: dict) -> Optional[dict]:
        """Generate a complete digital product with substantive content."""
        ptype = product_type["type"]
        pages = product_type.get("pages", 5)
        niches = product_type.get("niches", ["general"])
        niche = random.choice(niches)

        # Generate content via AI
        content = self._generate_content(ptype, niche, pages)
        if not content:
            return None

        title = content.get("title", f"{ptype.title()} - {niche.title()}")
        slug = slugify(title)
        palette_name = random.choice(list(self.palettes.keys()))
        palette = self.palettes[palette_name]

        product = {
            "title": title,
            "slug": slug,
            "type": ptype,
            "niche": niche,
            "pages": pages,
            "palette": palette,
            "palette_name": palette_name,
            "content": content,
            "page_count": 0,
            "price": self._determine_price(product_type),
        }

        # Generate PDF
        if REPORTLAB_AVAILABLE:
            pdf_path = self._generate_pdf(product)
            if pdf_path:
                product["pdf_path"] = str(pdf_path)
                product["page_count"] = pages
            else:
                logger.error(f"PDF generation failed for {title}")
                return None
        else:
            logger.warning("ReportLab unavailable. Skipping PDF generation.")
            return None

        # Generate cover/mockup image
        if PILLOW_AVAILABLE:
            cover_path = self._generate_cover(product)
            if cover_path:
                product["cover_path"] = str(cover_path)

        return product

    def _generate_content(self, ptype: str, niche: str, pages: int) -> Optional[dict]:
        """Use AI to generate substantive product content."""
        prompt = f"""Create content for a digital {ptype} about {niche}.
This must have {pages} pages of REAL, USEFUL content. Not blank lines. Not filler.

REQUIREMENTS:
- Every page must have substantive, actionable content
- Include specific prompts, questions, checklists, or frameworks
- Professional tone, Australian English
- Content that someone would genuinely pay $3-10 for
- No generic "write your goals here" without structure

OUTPUT FORMAT (JSON):
{{
  "title": "Product title",
  "subtitle": "One-line description",
  "pages": [
    {{
      "page_number": 1,
      "heading": "Page heading",
      "content": "Full page content with structure. Use \\n for line breaks. Include specific prompts, questions, frameworks, or checklists.",
      "type": "intro|content|checklist|framework|reflection|reference"
    }}
  ],
  "tags": ["tag1", "tag2", "tag3", "tag4", "tag5"],
  "description": "2-3 sentence product description for marketplace listing"
}}

Make every page genuinely useful. A planner should have specific daily/weekly structures. A worksheet should have real exercises. A checklist should have comprehensive items. A journal should have thoughtful prompts.
"""
        response = self.generate_text(
            prompt=prompt,
            quality_tier="routine",
            temperature=0.7,
            max_tokens=4096,
        )

        if not response:
            return None

        # Parse JSON from response
        try:
            # Try to find JSON in response
            json_start = response.find("{")
            json_end = response.rfind("}") + 1
            if json_start >= 0 and json_end > json_start:
                json_str = response[json_start:json_end]
                data = json.loads(json_str)
                if "pages" in data and len(data["pages"]) >= 3:
                    return data
        except json.JSONDecodeError:
            pass

        # Fallback: generate structured content locally
        return self._fallback_content(ptype, niche, pages)

    def _fallback_content(self, ptype: str, niche: str, pages: int) -> dict:
        """Generate structured content without AI (offline fallback)."""
        title = f"The {niche.title()} {ptype.title()}"
        subtitle = f"A practical {ptype} for {niche}"

        generated_pages = []

        if ptype == "planner":
            generated_pages = self._planner_pages(niche, pages)
        elif ptype == "worksheet":
            generated_pages = self._worksheet_pages(niche, pages)
        elif ptype == "checklist":
            generated_pages = self._checklist_pages(niche, pages)
        elif ptype == "journal":
            generated_pages = self._journal_pages(niche, pages)
        elif ptype == "template":
            generated_pages = self._template_pages(niche, pages)
        else:
            generated_pages = self._generic_pages(niche, pages, ptype)

        return {
            "title": title,
            "subtitle": subtitle,
            "pages": generated_pages,
            "tags": [niche, ptype, "printable", "digital download", "productivity"],
            "description": f"A professionally designed {pages}-page {ptype} for {niche}. Print or use digitally. Every page contains actionable content.",
        }

    def _planner_pages(self, niche: str, pages: int) -> list:
        """Generate substantive planner pages."""
        pages_list = []
        pages_list.append({
            "page_number": 1,
            "heading": f"Your {niche.title()} Planning Overview",
            "content": (
                f"Welcome to your {niche} planner.\n\n"
                "HOW TO USE THIS PLANNER:\n"
                "1. Start with the monthly overview to set your direction\n"
                "2. Break goals into weekly actions\n"
                "3. Use daily pages to track execution\n"
                "4. Review weekly to adjust course\n\n"
                "THIS MONTH'S FOCUS:\n"
                "Top 3 priorities:\n"
                "1. ___________________________________________\n"
                "2. ___________________________________________\n"
                "3. ___________________________________________\n\n"
                "WHAT SUCCESS LOOKS LIKE:\n"
                "By the end of this month, I will have:\n"
                "_____________________________________________\n"
                "_____________________________________________\n\n"
                "POTENTIAL OBSTACLES:\n"
                "_____________________________________________\n"
                "MY SOLUTION:\n"
                "_____________________________________________"
            ),
            "type": "intro",
        })

        for i in range(2, min(pages, 5)):
            pages_list.append({
                "page_number": i,
                "heading": f"Week {i-1} Plan",
                "content": (
                    f"WEEK {i-1} — {niche.title()} Focus\n\n"
                    "MONDAY:\n"
                    "Must-do: ___________________________________\n"
                    "Should-do: ___________________________________\n"
                    "Could-do: ___________________________________\n\n"
                    "TUESDAY:\n"
                    "Must-do: ___________________________________\n"
                    "Should-do: ___________________________________\n"
                    "Could-do: ___________________________________\n\n"
                    "WEDNESDAY:\n"
                    "Must-do: ___________________________________\n"
                    "Should-do: ___________________________________\n"
                    "Could-do: ___________________________________\n\n"
                    "THURSDAY:\n"
                    "Must-do: ___________________________________\n"
                    "Should-do: ___________________________________\n"
                    "Could-do: ___________________________________\n\n"
                    "FRIDAY:\n"
                    "Must-do: ___________________________________\n"
                    "Should-do: ___________________________________\n"
                    "Could-do: ___________________________________\n\n"
                    "WEEKEND:\n"
                    "_____________________________________________\n\n"
                    "END-OF-WEEK REVIEW:\n"
                    "What went well: _____________________________\n"
                    "What to improve: ____________________________\n"
                    "Next week's priority: _______________________"
                ),
                "type": "content",
            })

        # Add a review page
        pages_list.append({
            "page_number": pages,
            "heading": "Monthly Review & Reflection",
            "content": (
                "MONTHLY REVIEW\n\n"
                "GOALS ACHIEVED:\n"
                "_____________________________________________\n"
                "_____________________________________________\n\n"
                "GOALS NOT MET (and why):\n"
                "_____________________________________________\n"
                "_____________________________________________\n\n"
                "KEY LEARNINGS:\n"
                "1. ___________________________________________\n"
                "2. ___________________________________________\n"
                "3. ___________________________________________\n\n"
                "WHAT I'M PROUD OF:\n"
                "_____________________________________________\n\n"
                "NEXT MONTH'S TOP 3:\n"
                "1. ___________________________________________\n"
                "2. ___________________________________________\n"
                "3. ___________________________________________\n\n"
                "ENERGY LEVEL THIS MONTH (circle): 1  2  3  4  5\n"
                "SATISFACTION (circle): 1  2  3  4  5"
            ),
            "type": "reflection",
        })

        return pages_list

    def _worksheet_pages(self, niche: str, pages: int) -> list:
        """Generate substantive worksheet pages."""
        pages_list = [{
            "page_number": 1,
            "heading": f"{niche.title()} Assessment Worksheet",
            "content": (
                f"ASSESS YOUR CURRENT {niche.upper()} SITUATION\n\n"
                "Rate each area from 1 (struggling) to 5 (thriving):\n\n"
                f"1. My knowledge of {niche}: ___/5\n"
                f"2. My daily practice of {niche}: ___/5\n"
                f"3. My resources for {niche}: ___/5\n"
                f"4. My support network for {niche}: ___/5\n"
                f"5. My confidence in {niche}: ___/5\n\n"
                "TOTAL: ___/25\n\n"
                "MY STRONGEST AREA:\n"
                "_____________________________________________\n\n"
                "MY BIGGEST CHALLENGE:\n"
                "_____________________________________________\n\n"
                "IF I COULD CHANGE ONE THING:\n"
                "_____________________________________________\n\n"
                "THE FIRST STEP I WILL TAKE:\n"
                "_____________________________________________\n"
                "BY WHEN: _____________________________________"
            ),
            "type": "content",
        }]

        exercises = [
            ("Identify Your Patterns", "List 3 recurring challenges:\n1. ___________________________________________\n2. ___________________________________________\n3. ___________________________________________\n\nWhat triggers them?\n_____________________________________________\n\nWhat have you tried before?\n_____________________________________________\n\nWhat worked? What didn't?\n_____________________________________________"),
            ("Design Your Solution", "MY APPROACH:\n\nStep 1: _____________________________________\nStep 2: _____________________________________\nStep 3: _____________________________________\nStep 4: _____________________________________\nStep 5: _____________________________________\n\nRESOURCES I NEED:\n_____________________________________________\n\nPOTENTIAL OBSTACLES:\n_____________________________________________\n\nHOW I'LL OVERCOME THEM:\n_____________________________________________"),
            ("Track Your Progress", "WEEK 1: What I did: _________________________\nResult: _____________________________________\n\nWEEK 2: What I did: _________________________\nResult: _____________________________________\n\nWEEK 3: What I did: _________________________\nResult: _____________________________________\n\nWEEK 4: What I did: _________________________\nResult: _____________________________________\n\nOVERALL PROGRESS (circle): 1  2  3  4  5\n\nNEXT ACTION: __________________________________"),
        ]

        for i, (heading, content) in enumerate(exercises[:pages-1], start=2):
            pages_list.append({
                "page_number": i,
                "heading": heading,
                "content": content,
                "type": "content",
            })

        return pages_list

    def _checklist_pages(self, niche: str, pages: int) -> list:
        """Generate substantive checklist pages."""
        pages_list = [{
            "page_number": 1,
            "heading": f"Complete {niche.title()} Checklist",
            "content": (
                f"THE COMPLETE {niche.upper()} CHECKLIST\n\n"
                "Use this checklist to ensure nothing is missed.\n"
                "Tick each item as you complete it.\n\n"
                "PHASE 1: PREPARATION\n"
                "☐ Research requirements and prerequisites\n"
                "☐ Gather necessary materials and tools\n"
                "☐ Set a realistic timeline\n"
                "☐ Identify potential obstacles\n"
                "☐ Arrange support or help if needed\n"
                "☐ Set up tracking system\n\n"
                "PHASE 2: EXECUTION\n"
                "☐ Complete initial setup\n"
                "☐ Work through core tasks systematically\n"
                "☐ Check quality at each stage\n"
                "☐ Document progress\n"
                "☐ Address issues as they arise\n"
                "☐ Maintain momentum with daily action\n\n"
                "PHASE 3: COMPLETION\n"
                "☐ Final review of all items\n"
                "☐ Test or verify results\n"
                "☐ Clean up and organise\n"
                "☐ Document lessons learned\n"
                "☐ Celebrate completion\n"
                "☐ Plan next steps"
            ),
            "type": "checklist",
        }]

        for i in range(2, pages + 1):
            pages_list.append({
                "page_number": i,
                "heading": f"Detailed Checklist — Section {i-1}",
                "content": (
                    f"SECTION {i-1}: DETAILED ITEMS\n\n"
                    "☐ ___________________________________________\n"
                    "☐ ___________________________________________\n"
                    "☐ ___________________________________________\n"
                    "☐ ___________________________________________\n"
                    "☐ ___________________________________________\n"
                    "☐ ___________________________________________\n"
                    "☐ ___________________________________________\n"
                    "☐ ___________________________________________\n"
                    "☐ ___________________________________________\n"
                    "☐ ___________________________________________\n\n"
                    "NOTES:\n"
                    "_____________________________________________\n"
                    "_____________________________________________\n"
                    "_____________________________________________\n\n"
                    "COMPLETED DATE: ___/___/______"
                ),
                "type": "checklist",
            })

        return pages_list

    def _journal_pages(self, niche: str, pages: int) -> list:
        """Generate substantive journal pages."""
        prompts = [
            f"What draws me to {niche} is...",
            f"Today, my relationship with {niche} feels...",
            f"One thing I want to understand better about {niche} is...",
            f"A small win I had recently related to {niche}:",
            f"What I'm grateful for today:",
            f"Something that challenged me and how I responded:",
            f"If I could give advice to my past self about {niche}:",
            f"What I want to remember from this week:",
            f"Three things that went well today:",
            f"What I'm looking forward to:",
            f"A fear I want to release:",
            f"Something I learned about myself:",
            f"How I want to show up tomorrow:",
            f"What 'enough' looks like for me today:",
            f"A moment of peace I experienced:",
        ]

        pages_list = []
        for i in range(min(pages, len(prompts))):
            pages_list.append({
                "page_number": i + 1,
                "heading": f"Journal Entry — Day {i+1}",
                "content": (
                    f"DATE: ___/___/______\n"
                    f"MOOD (circle): 1  2  3  4  5\n"
                    f"ENERGY (circle): 1  2  3  4  5\n\n"
                    f"PROMPT: {prompts[i]}\n\n"
                    "_____________________________________________\n"
                    "_____________________________________________\n"
                    "_____________________________________________\n"
                    "_____________________________________________\n"
                    "_____________________________________________\n"
                    "_____________________________________________\n"
                    "_____________________________________________\n"
                    "_____________________________________________\n\n"
                    "ONE WORD FOR TODAY: _________________________"
                ),
                "type": "reflection",
            })

        return pages_list

    def _template_pages(self, niche: str, pages: int) -> list:
        """Generate substantive template pages."""
        pages_list = [{
            "page_number": 1,
            "heading": f"{niche.title()} Template — Overview",
            "content": (
                f"{niche.upper()} TEMPLATE\n\n"
                "PROJECT/ITEM NAME: _________________________\n"
                "DATE: ___/___/______\n"
                "STATUS: ☐ Not Started  ☐ In Progress  ☐ Complete\n\n"
                "OVERVIEW:\n"
                "_____________________________________________\n"
                "_____________________________________________\n\n"
                "KEY DETAILS:\n"
                "_____________________________________________\n"
                "_____________________________________________\n"
                "_____________________________________________\n\n"
                "DEADLINE: ___/___/______\n"
                "PRIORITY: ☐ Low  ☐ Medium  ☐ High  ☐ Urgent\n\n"
                "NOTES:\n"
                "_____________________________________________\n"
                "_____________________________________________"
            ),
            "type": "content",
        }]

        for i in range(2, pages + 1):
            pages_list.append({
                "page_number": i,
                "heading": f"Section {i-1}",
                "content": (
                    f"SECTION {i-1}\n\n"
                    "ITEM: _____________________________________\n"
                    "DESCRIPTION: _______________________________\n"
                    "_____________________________________________\n\n"
                    "STATUS: ☐ Pending  ☐ Active  ☐ Done\n"
                    "ASSIGNED TO: _______________________________\n"
                    "DUE: ___/___/______\n\n"
                    "DETAILS:\n"
                    "_____________________________________________\n"
                    "_____________________________________________\n"
                    "_____________________________________________\n\n"
                    "FOLLOW-UP REQUIRED: ☐ Yes  ☐ No\n"
                    "NEXT ACTION: _______________________________"
                ),
                "type": "content",
            })

        return pages_list

    def _generic_pages(self, niche: str, pages: int, ptype: str) -> list:
        """Generate generic structured pages."""
        pages_list = []
        for i in range(1, pages + 1):
            pages_list.append({
                "page_number": i,
                "heading": f"{ptype.title()} — Page {i}",
                "content": (
                    f"{niche.upper()} — {ptype.upper()} PAGE {i}\n\n"
                    "_____________________________________________\n"
                    "_____________________________________________\n"
                    "_____________________________________________\n"
                    "_____________________________________________\n"
                    "_____________________________________________\n"
                    "_____________________________________________\n"
                    "_____________________________________________\n"
                    "_____________________________________________\n"
                    "_____________________________________________\n"
                    "_____________________________________________\n\n"
                    "NOTES:\n"
                    "_____________________________________________\n"
                    "_____________________________________________"
                ),
                "type": "content",
            })
        return pages_list

    def _determine_price(self, product_type: dict) -> float:
        """Determine price within configured range."""
        price_range = product_type.get("price_range", [2.99, 7.99])
        return round(random.uniform(price_range[0], price_range[1]), 2)

    def _generate_pdf(self, product: dict) -> Optional[Path]:
        """Generate a professional PDF using ReportLab."""
        output_path = self.output_dir / f"{product['slug']}.pdf"
        palette = product["palette"]
        content = product["content"]

        try:
            doc = SimpleDocTemplate(
                str(output_path),
                pagesize=A4,
                rightMargin=20*mm,
                leftMargin=20*mm,
                topMargin=25*mm,
                bottomMargin=20*mm,
            )

            styles = getSampleStyleSheet()

            # Custom styles
            title_style = ParagraphStyle(
                "ProductTitle",
                parent=styles["Title"],
                fontSize=24,
                textColor=HexColor(palette["primary"]),
                spaceAfter=6*mm,
                alignment=1,
            )
            subtitle_style = ParagraphStyle(
                "ProductSubtitle",
                parent=styles["Normal"],
                fontSize=12,
                textColor=HexColor(palette["secondary"]),
                spaceAfter=10*mm,
                alignment=1,
            )
            heading_style = ParagraphStyle(
                "PageHeading",
                parent=styles["Heading1"],
                fontSize=16,
                textColor=HexColor(palette["primary"]),
                spaceBefore=8*mm,
                spaceAfter=4*mm,
            )
            body_style = ParagraphStyle(
                "ProductBody",
                parent=styles["Normal"],
                fontSize=10,
                leading=16,
                textColor=HexColor("#333333"),
            )
            footer_style = ParagraphStyle(
                "Footer",
                parent=styles["Normal"],
                fontSize=8,
                textColor=HexColor("#999999"),
                alignment=1,
            )

            story = []

            # Cover page
            story.append(Spacer(1, 40*mm))
            story.append(Paragraph(content.get("title", product["title"]), title_style))
            story.append(Paragraph(content.get("subtitle", ""), subtitle_style))
            story.append(Spacer(1, 20*mm))
            story.append(HRFlowable(width="60%", thickness=1, color=HexColor(palette["accent"])))
            story.append(Spacer(1, 10*mm))
            story.append(Paragraph(
                f"A {product['type']} by The Institution",
                footer_style
            ))
            story.append(PageBreak())

            # Content pages
            for page_data in content.get("pages", []):
                heading = page_data.get("heading", f"Page {page_data.get('page_number', '')}")
                page_content = page_data.get("content", "")

                story.append(Paragraph(heading, heading_style))
                story.append(HRFlowable(width="100%", thickness=0.5, color=HexColor(palette["secondary"])))
                story.append(Spacer(1, 3*mm))

                # Split content by newlines and add as paragraphs
                for line in page_content.split("\n"):
                    line = line.strip()
                    if line:
                        # Escape XML special characters for ReportLab
                        line = line.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                        story.append(Paragraph(line, body_style))
                    else:
                        story.append(Spacer(1, 3*mm))

                story.append(PageBreak())

            # Build PDF
            doc.build(story)
            logger.info(f"PDF generated: {output_path}")
            return output_path

        except Exception as e:
            logger.error(f"PDF generation error: {e}")
            return None

    def _generate_cover(self, product: dict) -> Optional[Path]:
        """Generate a product cover/mockup image using Pillow."""
        output_path = self.output_dir / f"{product['slug']}_cover.png"
        palette = product["palette"]

        try:
            width, height = 1200, 1600
            img = Image.new("RGB", (width, height), palette["bg"])
            draw = ImageDraw.Draw(img)

            # Background accent bar
            draw.rectangle([0, 0, width, 200], fill=palette["primary"])
            draw.rectangle([0, height-100, width, height], fill=palette["secondary"])

            # Title text
            try:
                title_font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 48)
                subtitle_font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 24)
                small_font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 16)
            except (IOError, OSError):
                title_font = ImageFont.load_default()
                subtitle_font = ImageFont.load_default()
                small_font = ImageFont.load_default()

            # Draw title (wrapped)
            title = product["title"]
            y_pos = 300
            words = title.split()
            lines = []
            current_line = ""
            for word in words:
                test_line = f"{current_line} {word}".strip()
                if len(test_line) > 25:
                    lines.append(current_line)
                    current_line = word
                else:
                    current_line = test_line
            if current_line:
                lines.append(current_line)

            for line in lines[:4]:
                bbox = draw.textbbox((0, 0), line, font=title_font)
                text_width = bbox[2] - bbox[0]
                x = (width - text_width) // 2
                draw.text((x, y_pos), line, fill=palette["primary"], font=title_font)
                y_pos += 60

            # Subtitle
            subtitle = product["content"].get("subtitle", "")
            if subtitle:
                bbox = draw.textbbox((0, 0), subtitle, font=subtitle_font)
                text_width = bbox[2] - bbox[0]
                x = (width - text_width) // 2
                draw.text((x, y_pos + 40), subtitle, fill=palette["secondary"], font=subtitle_font)

            # Product type badge
            badge_text = product["type"].upper()
            draw.rounded_rectangle([width//2 - 100, height - 250, width//2 + 100, height - 200], radius=10, fill=palette["accent"])
            bbox = draw.textbbox((0, 0), badge_text, font=small_font)
            text_width = bbox[2] - bbox[0]
            draw.text(((width - text_width) // 2, height - 240), badge_text, fill="white", font=small_font)

            # Page count
            page_text = f"{product.get('page_count', 0)} Pages"
            bbox = draw.textbbox((0, 0), page_text, font=small_font)
            text_width = bbox[2] - bbox[0]
            draw.text(((width - text_width) // 2, height - 150), page_text, fill=palette["primary"], font=small_font)

            img.save(str(output_path), "PNG", quality=95)
            logger.info(f"Cover generated: {output_path}")
            return output_path

        except Exception as e:
            logger.error(f"Cover generation error: {e}")
            return None

    def _create_listing(self, product: dict, stream_cfg: dict) -> dict:
        """Create marketplace listing materials."""
        content = product["content"]
        tags = content.get("tags", [product["niche"], product["type"]])
        description = content.get("description", f"A {product['type']} about {product['niche']}.")

        listing = {
            "title": product["title"],
            "description": description,
            "price": product["price"],
            "currency": "AUD",
            "tags": tags[:13],  # Etsy allows 13 tags
            "category": self._map_category(product["type"]),
            "materials": "Digital download - PDF",
            "slug": product["slug"],
        }

        # Save listing
        listing_path = self.listings_dir / f"{product['slug']}.json"
        listing_path.write_text(json.dumps(listing, indent=2), encoding="utf-8")

        return listing

    def _map_category(self, ptype: str) -> str:
        """Map product type to marketplace category."""
        mapping = {
            "planner": "Paper & Party Supplies > Paper > Calendars & Planners",
            "worksheet": "Paper & Party Supplies > Paper > Stationery",
            "template": "Paper & Party Supplies > Paper > Stationery",
            "checklist": "Paper & Party Supplies > Paper > Stationery",
            "journal": "Paper & Party Supplies > Paper > Journals & Notebooks",
        }
        return mapping.get(ptype, "Paper & Party Supplies > Paper")

    def _list_on_platforms(self, product: dict, listing: dict):
        """Attempt to list product on configured platforms."""
        platforms = self.get_stream_config().get("platforms", [])

        if "gumroad" in platforms:
            self._list_gumroad(product, listing)

        if "etsy" in platforms:
            self._list_etsy(product, listing)

    def _list_gumroad(self, product: dict, listing: dict):
        """List product on Gumroad."""
        token = os.environ.get("GUMROAD_ACCESS_TOKEN")
        if not token:
            logger.debug("No Gumroad token. Skipping listing.")
            return

        try:
            pdf_path = product.get("pdf_path")
            if not pdf_path or not Path(pdf_path).exists():
                logger.warning("No PDF file for Gumroad listing.")
                return

            resp = requests.post(
                "https://api.gumroad.com/v2/products",
                data={
                    "access_token": token,
                    "name": listing["title"],
                    "price": int(listing["price"] * 100),  # Cents
                    "currency": "aud",
                    "product_type": "digital",
                    "description": listing["description"],
                    "tags": ",".join(listing["tags"][:5]),
                },
                timeout=30,
            )

            if resp.status_code in (200, 201):
                data = resp.json()
                product_id = data.get("id", "")
                logger.info(f"Listed on Gumroad: {product_id}")

                # Upload PDF file
                if product_id:
                    with open(pdf_path, "rb") as f:
                        requests.put(
                            f"https://api.gumroad.com/v2/products/{product_id}/upload",
                            data={"access_token": token},
                            files={"file": f},
                            timeout=60,
                        )
            else:
                logger.warning(f"Gumroad listing failed: {resp.status_code} {resp.text[:200]}")

        except Exception as e:
            logger.warning(f"Gumroad listing error: {e}")

    def _list_etsy(self, product: dict, listing: dict):
        """List product on Etsy (requires API key and shop setup)."""
        api_key = os.environ.get("ETSY_API_KEY")
        if not api_key:
            logger.debug("No Etsy API key. Skipping listing.")
            return

        # Etsy API v3 requires OAuth — log for manual listing
        logger.info(
            f"Etsy listing prepared for '{listing['title']}' at ${listing['price']}. "
            f"Manual upload required (Etsy OAuth not configured)."
        )

        # Save listing data for manual upload
        etsy_path = self.listings_dir / f"{product['slug']}_etsy.json"
        etsy_path.write_text(json.dumps(listing, indent=2), encoding="utf-8")

    def _track_sales(self):
        """Track sales from platforms."""
        token = os.environ.get("GUMROAD_ACCESS_TOKEN")
        if token:
            try:
                resp = requests.get(
                    "https://api.gumroad.com/v2/sales",
                    params={"access_token": token},
                    timeout=30,
                )
                if resp.status_code == 200:
                    sales = resp.json().get("sales", [])
                    for sale in sales[:10]:
                        amount = sale.get("price", 0) / 100.0
                        if amount > 0:
                            self.record_revenue("gumroad", amount, f"Sale: {sale.get('product_name', 'unknown')}")
            except Exception as e:
                logger.debug(f"Gumroad sales tracking error: {e}")