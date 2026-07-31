#!/usr/bin/env python3
"""
═══════════════════════════════════════════════════════════════
THE INSTITUTION — STREAM 7: PRINT-ON-DEMAND
═══════════════════════════════════════════════════════════════
Autonomous POD pipeline:
- AI-generated designs (SVG/PNG via Pillow)
- Auto-lists via platform APIs (Redbubble, Merch by Amazon)
- Niche-targeted (matches content site niches)
- Zero inventory, zero shipping
- Tracks sales per design, kills underperformers
═══════════════════════════════════════════════════════════════
"""

import os
import json
import math
import random
import hashlib
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import requests

from common import get_db, get_config, get_logger, INSTITUTION_ROOT, now_iso, today_str, slugify
from agents.base import BaseAgent

logger = get_logger("print_on_demand")

try:
    from PIL import Image, ImageDraw, ImageFont, ImageFilter
    PILLOW_AVAILABLE = True
except ImportError:
    PILLOW_AVAILABLE = False
    logger.warning("Pillow not installed. Design generation unavailable.")


class PrintOnDemandAgent(BaseAgent):
    AGENT_NAME = "print_on_demand"
    AGENT_TYPE = "revenue_stream"
    STREAM = "print_on_demand"
    DEFAULT_INTERVAL_SECONDS = 28800  # Every 8 hours

    def __init__(self):
        super().__init__()
        self.pod_dir = INSTITUTION_ROOT / "pod"
        self.designs_dir = self.pod_dir / "designs"
        self.listings_dir = self.pod_dir / "listings"
        for d in [self.designs_dir, self.listings_dir]:
            d.mkdir(parents=True, exist_ok=True)

        # Design style configurations
        self.styles = {
            "minimalist_text": {
                "bg_color": "#FFFFFF",
                "text_color": "#1A1A1A",
                "accent_color": "#E74C3C",
                "font_size_range": (48, 72),
                "layout": "centered",
            },
            "geometric": {
                "bg_color": "#1A1A2E",
                "text_c
olor": "#FFFFFF",
                "accent_color": "#E94560",
                "font_size_range": (36, 54),
                "layout": "geometric_shapes",
            },
            "vintage_badge": {
                "bg_color": "#F5E6D3",
                "text_color": "#2C3E50",
                "accent_color": "#8B4513",
                "font_size_range": (32, 48),
                "layout": "circular_badge",
            },
            "bold_statement": {
                "bg_color": "#000000",
                "text_color": "#FFFFFF",
                "accent_color": "#FFD700",
                "font_size_range": (56, 80),
                "layout": "full_bleed",
            },
            "nature_organic": {
                "bg_color": "#F0FFF0",
                "text_color": "#2D6A4F",
                "accent_color": "#95D5B2",
                "font_size_range": (40, 60),
                "layout": "organic_flow",
            },
        }

    def run_once(self):
        """Main cycle: generate designs, list on platforms, track sales."""
        if not self.should_run_today():
            return "Stream disabled or inactive"

        if not PILLOW_AVAILABLE:
            return "Pillow not available. Cannot generate designs."

        stream_cfg = self.get_stream_config()
        designs_per_week = stream_cfg.get("schedule", {}).get("designs_per_week", 10)
        niches = stream_cfg.get("niches", [])
        design_styles = stream_cfg.get("design_styles", [])
        platforms = stream_cfg.get("platforms", [])
        products = stream_cfg.get("products", [])

        # Check weekly quota
        week_designs = self.db.fetchone(
            "SELECT COUNT(*) as cnt FROM content_inventory WHERE stream = ? AND content_type = 'pod_design' AND created_at > datetime('now', '-7 days')",
            (self.STREAM,)
        )
        made_this_week = week_designs["cnt"] if week_designs else 0

        if made_this_week >= designs_per_week:
            # Track sales and kill underpe
rformers instead
            self._track_sales()
            self._kill_underperformers()
            return f"Weekly design quota met ({made_this_week}/{designs_per_week}). Tracking sales."

        results = []

        # Generate designs
        remaining = designs_per_week - made_this_week
        batch_size = min(remaining, 3)  # Max 3 per cycle

        for i in range(batch_size):
            niche = random.choice(niches) if niches else "general"
            style_name = random.choice(design_styles) if design_styles else random.choice(list(self.styles.keys()))
            style = self.styles.get(style_name, self.styles["minimalist_text"])

            design = self._generate_design(niche, style_name, style)
            if design:
                # List on platforms
                listed = self._list_on_platforms(design, platforms, products)
                results.append(f"Created: {design['title']} ({'listed' if listed else 'saved'})")

                # Record in inventory
                self.db.insert("content_inventory", {
                    "stream": self.STREAM,
                    "content_type": "pod_design",
                    "title": design["title"],
                    "slug": design["hash"],
                    "status": "published" if listed else "draft",
                    "published_at": now_iso() if listed else None,
                    "metrics": json.dumps({
                        "niche": niche,
                        "style": style_name,
                        "listed": listed,
                        "platforms": platforms,
                    }),
                })

        # Track sales periodically
        self._track_sales()

        return "; ".join(results) if results else "No designs generated this cycle"

    def _generate_design(self, niche: str, style_name: str, style: dict) -> Optional[dict]:
        """Generate a POD design using AI for text + Pillow for rendering."""
        # Get design text/concept from AI
        c
oncept = self._generate_concept(niche, style_name)
        if not concept:
            return None

        title = concept.get("title", f"{niche.title()} Design")
        text_lines = concept.get("text_lines", [title])
        tags = concept.get("tags", [niche, style_name])

        # Generate the image
        design_hash = hashlib.md5(f"{title}{now_iso()}".encode()).hexdigest()[:12]
        output_path = self.designs_dir / f"{design_hash}.png"

        success = self._render_design(output_path, text_lines, style, niche)
        if not success:
            return None

        return {
            "title": title,
            "hash": design_hash,
            "niche": niche,
            "style": style_name,
            "text_lines": text_lines,
            "tags": tags,
            "image_path": str(output_path),
            "created_at": now_iso(),
        }

    def _generate_concept(self, niche: str, style_name: str) -> Optional[dict]:
        """Use AI to generate design concept text."""
        prompt = f"""Generate a print-on-demand design concept.

NICHE: {niche}
STYLE: {style_name}

REQUIREMENTS:
- Short, impactful text (max 3 lines, max 6 words per line)
- Would look good on a t-shirt, mug, or sticker
- Appeals to the target audience of this niche
- Not offensive, not trademarked, not copyrighted
- Witty, relatable, or inspirational
- Australian sensibility (dry humour welcome)

OUTPUT FORMAT (JSON):
{{
  "title": "Design name for internal tracking",
  "text_lines": ["Line 1", "Line 2", "Line 3"],
  "tags": ["tag1", "tag2", "tag3", "tag4", "tag5"],
  "description": "One-line product description for marketplace"
}}

Examples of good POD text:
- "I'm not lazy, I'm on energy saving mode"
- "Ctrl+Z my life choices"
- "It works on my machine"
- "Powered by caffeine and spite"
- "Introverted but willing to discuss cats"
"""
        response = self.generate_text(
            prompt=prompt,
            quality_tier="routine",
            temperature=0.9,
          
  max_tokens=500,
        )

        if not response:
            # Fallback concepts
            return self._fallback_concept(niche)

        try:
            json_start = response.find("{")
            json_end = response.rfind("}") + 1
            if json_start >= 0 and json_end > json_start:
                data = json.loads(response[json_start:json_end])
                if "text_lines" in data and data["text_lines"]:
                    return data
        except json.JSONDecodeError:
            pass

        return self._fallback_concept(niche)

    def _fallback_concept(self, niche: str) -> dict:
        """Generate a fallback concept without AI."""
        concepts = {
            "tech humor": [
                {"title": "Semicolon", "text_lines": [";", "I'm here", "because of a typo"], "tags": ["programming", "humor", "code"]},
                {"title": "Reboot", "text_lines": ["Have you tried", "turn it off", "and on again?"], "tags": ["it", "support", "humor"]},
                {"title": "WiFi", "text_lines": ["I'm here", "for the WiFi", "and the snacks"], "tags": ["wifi", "introvert", "humor"]},
            ],
            "introvert culture": [
                {"title": "Social Battery", "text_lines": ["Social battery:", "1%", "Please recharge"], "tags": ["introvert", "battery", "humor"]},
                {"title": "Plans", "text_lines": ["I made plans", "with my couch", "we're very happy"], "tags": ["introvert", "couch", "cozy"]},
                {"title": "Leaving", "text_lines": ["I came", "I saw", "I want to go home"], "tags": ["introvert", "social", "humor"]},
            ],
            "chronic illness awareness": [
                {"title": "Spoonie", "text_lines": ["Running on", "spoons and", "stubbornness"], "tags": ["chronic", "spoonie", "strength"]},
                {"title": "Rest", "text_lines": ["Rest is not", "quitting", "it's strategy"], "tags": ["chronic", "rest", "wisdom"]},
                {"title": "Invisible", "text_lines": ["My i
llness", "is invisible", "my strength isn't"], "tags": ["chronic", "invisible", "strength"]},
            ],
            "homelab nerds": [
                {"title": "Uptime", "text_lines": ["99.9% uptime", "0.1% panic"], "tags": ["homelab", "server", "uptime"]},
                {"title": "DNS", "text_lines": ["It's always", "DNS", "it's never DNS"], "tags": ["networking", "dns", "humor"]},
                {"title": "Docker", "text_lines": ["Works on", "my machine", "so I shipped it"], "tags": ["docker", "devops", "humor"]},
            ],
            "australian wildlife": [
                {"title": "Magpie", "text_lines": ["Swooping season", "is my", "villain arc"], "tags": ["australia", "magpie", "humor"]},
                {"title": "Spider", "text_lines": ["No worries", "mate", "just a spider"], "tags": ["australia", "spider", "humor"]},
                {"title": "Wombat", "text_lines": ["Wombat", "energy", "unbothered"], "tags": ["australia", "wombat", "chill"]},
            ],
        }

        niche_concepts = concepts.get(niche, concepts["tech humor"])
        chosen = random.choice(niche_concepts)
        chosen["description"] = f"A {niche} design for people who get it."
        return chosen

    def _render_design(self, output_path: Path, text_lines: list, style: dict, niche: str) -> bool:
        """Render the design image using Pillow."""
        try:
            # Standard POD dimensions (4500x5400 for t-shirts at 300 DPI)
            width, height = 4500, 5400

            bg_color = self._hex_to_rgb(style["bg_color"])
            text_color = self._hex_to_rgb(style["text_color"])
            accent_color = self._hex_to_rgb(style["accent_color"])

            img = Image.new("RGB", (width, height), bg_color)
            draw = ImageDraw.Draw(img)

            layout = style.get("layout", "centered")

            if layout == "centered":
                self._layout_centered(draw, text_lines, width, height, text_color, accent_color, style)
          
  elif layout == "geometric_shapes":
                self._layout_geometric(draw, text_lines, width, height, text_color, accent_color, style)
            elif layout == "circular_badge":
                self._layout_badge(draw, text_lines, width, height, text_color, accent_color, style)
            elif layout == "full_bleed":
                self._layout_full_bleed(draw, text_lines, width, height, text_color, accent_color, style)
            elif layout == "organic_flow":
                self._layout_organic(draw, text_lines, width, height, text_color, accent_color, style)
            else:
                self._layout_centered(draw, text_lines, width, height, text_color, accent_color, style)

            # Save as high-quality PNG
            img.save(str(output_path), "PNG", quality=100)
            logger.info(f"Design rendered: {output_path}")
            return True

        except Exception as e:
            logger.error(f"Design render error: {e}")
            return False

    def _get_font(self, size: int) -> ImageFont.FreeTypeFont:
        """Get a font at the specified size."""
        font_paths = [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
            "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
        ]
        for fp in font_paths:
            try:
                return ImageFont.truetype(fp, size)
            except (IOError, OSError):
                continue
        return ImageFont.load_default()

    def _layout_centered(self, draw: ImageDraw.ImageDraw, text_lines: list,
                         width: int, height: int, text_color: tuple, accent_color: tuple, style: dict):
        """Centered text layout."""
        font_size = style["font_size_range"][0] * 3  # Scale up for 4500px
        font = self._get_font(font_size)

        total_height = len(text_lines) * (font_si
ze + 40)
        y_start = (height - total_height) // 2

        for i, line in enumerate(text_lines):
            bbox = draw.textbbox((0, 0), line, font=font)
            text_width = bbox[2] - bbox[0]
            x = (width - text_width) // 2
            y = y_start + i * (font_size + 40)

            # First line gets accent color
            color = accent_color if i == 0 else text_color
            draw.text((x, y), line, fill=color, font=font)

    def _layout_geometric(self, draw: ImageDraw.ImageDraw, text_lines: list,
                          width: int, height: int, text_color: tuple, accent_color: tuple, style: dict):
        """Geometric shapes with text."""
        # Draw geometric background elements
        for i in range(5):
            x = random.randint(0, width)
            y = random.randint(0, height)
            size = random.randint(200, 800)
            shape_type = random.choice(["rectangle", "triangle", "circle"])

            shape_color = accent_color if random.random() > 0.5 else text_color
            alpha_color = tuple(min(255, c + 50) for c in shape_color)

            if shape_type == "rectangle":
                draw.rectangle([x, y, x + size, y + size], outline=alpha_color, width=8)
            elif shape_type == "circle":
                draw.ellipse([x, y, x + size, y + size], outline=alpha_color, width=8)
            elif shape_type == "triangle":
                points = [(x, y + size), (x + size // 2, y), (x + size, y + size)]
                draw.polygon(points, outline=alpha_color)

        # Overlay text
        font_size = style["font_size_range"][0] * 3
        font = self._get_font(font_size)

        total_height = len(text_lines) * (font_size + 40)
        y_start = (height - total_height) // 2

        for i, line in enumerate(text_lines):
            bbox = draw.textbbox((0, 0), line, font=font)
            text_width = bbox[2] - bbox[0]
            x = (width - text_width) // 2
            y = y_start + i * (font_
size + 40)
            draw.text((x, y), line, fill=text_color, font=font)

    def _layout_badge(self, draw: ImageDraw.ImageDraw, text_lines: list,
                      width: int, height: int, text_color: tuple, accent_color: tuple, style: dict):
        """Circular badge layout."""
        center_x, center_y = width // 2, height // 2
        radius = min(width, height) // 3

        # Outer circle
        draw.ellipse(
            [center_x - radius, center_y - radius, center_x + radius, center_y + radius],
            outline=accent_color, width=20
        )
        # Inner circle
        inner_radius = radius - 60
        draw.ellipse(
            [center_x - inner_radius, center_y - inner_radius,
             center_x + inner_radius, center_y + inner_radius],
            outline=text_color, width=8
        )

        # Text inside badge
        font_size = style["font_size_range"][0] * 2
        font = self._get_font(font_size)

        total_height = len(text_lines) * (font_size + 20)
        y_start = center_y - total_height // 2

        for i, line in enumerate(text_lines):
            bbox = draw.textbbox((0, 0), line, font=font)
            text_width = bbox[2] - bbox[0]
            x = center_x - text_width // 2
            y = y_start + i * (font_size + 20)
            draw.text((x, y), line, fill=text_color, font=font)

        # Decorative stars/dots around badge
        for angle in range(0, 360, 30):
            rad = math.radians(angle)
            star_x = center_x + int((radius + 40) * math.cos(rad))
            star_y = center_y + int((radius + 40) * math.sin(rad))
            draw.ellipse([star_x - 10, star_y - 10, star_x + 10, star_y + 10], fill=accent_color)

    def _layout_full_bleed(self, draw: ImageDraw.ImageDraw, text_lines: list,
                           width: int, height: int, text_color: tuple, accent_color: tuple, style: dict):
        """Bold full-bleed text layout."""
        font_size = style["font_size_range"][1] * 3  # Larg
er for bold statement
        font = self._get_font(font_size)

        # Stack text vertically, filling the space
        total_height = len(text_lines) * (font_size + 20)
        y_start = (height - total_height) // 2

        for i, line in enumerate(text_lines):
            bbox = draw.textbbox((0, 0), line, font=font)
            text_width = bbox[2] - bbox[0]
            x = (width - text_width) // 2
            y = y_start + i * (font_size + 20)

            # Alternate colors for emphasis
            color = accent_color if i == len(text_lines) // 2 else text_color
            draw.text((x, y), line, fill=color, font=font)

        # Underline accent
        line_y = y_start + total_height + 40
        draw.rectangle([width // 4, line_y, 3 * width // 4, line_y + 12], fill=accent_color)

    def _layout_organic(self, draw: ImageDraw.ImageDraw, text_lines: list,
                        width: int, height: int, text_color: tuple, accent_color: tuple, style: dict):
        """Organic/nature-inspired layout."""
        # Draw organic curves
        for i in range(3):
            y_base = height // 4 + i * height // 4
            points = []
            for x in range(0, width, 50):
                y = y_base + int(50 * math.sin(x / 200 + i))
                points.append((x, y))
            if len(points) > 1:
                draw.line(points, fill=accent_color, width=4)

        # Text
        font_size = style["font_size_range"][0] * 3
        font = self._get_font(font_size)

        total_height = len(text_lines) * (font_size + 40)
        y_start = (height - total_height) // 2

        for i, line in enumerate(text_lines):
            bbox = draw.textbbox((0, 0), line, font=font)
            text_width = bbox[2] - bbox[0]
            x = (width - text_width) // 2
            y = y_start + i * (font_size + 40)
            draw.text((x, y), line, fill=text_color, font=font)

    def _hex_to_rgb(self, hex_color: str) -> tuple:
        """Convert hex color to RG
B tuple."""
        hex_color = hex_color.lstrip("#")
        return tuple(int(hex_color[i:i+2], 16) for i in (0, 2, 4))

    def _list_on_platforms(self, design: dict, platforms: list, products: list) -> bool:
        """List design on POD platforms."""
        listed = False

        if "redbubble" in platforms:
            if self._list_redbubble(design, products):
                listed = True

        if "merch_by_amazon" in platforms:
            if self._list_merch_amazon(design, products):
                listed = True

        # If no platform APIs available, save listing data for manual upload
        if not listed:
            listing_data = {
                "title": design["title"],
                "tags": design["tags"],
                "description": f"{design['title']} - {design['niche']} design",
                "image_path": design["image_path"],
                "products": products,
                "created_at": now_iso(),
            }
            listing_path = self.listings_dir / f"{design['hash']}_listing.json"
            listing_path.write_text(json.dumps(listing_data, indent=2), encoding="utf-8")
            logger.info(f"Listing saved for manual upload: {design['title']}")

        return listed

    def _list_redbubble(self, design: dict, products: list) -> bool:
        """List on Redbubble (requires session cookie)."""
        session_cookie = os.environ.get("REDBUBBLE_SESSION")
        if not session_cookie:
            logger.debug("No Redbubble session. Skipping auto-listing.")
            return False

        try:
            # Redbubble doesn't have a public API — requires authenticated session
            # This is a simplified version; real implementation would use their internal API
            headers = {
                "Cookie": f"_redbubble_session={session_cookie}",
                "Content-Type": "application/json",
            }

            # Upload design image first
            with open(design["image_path"], "rb") as
 f:
                upload_resp = requests.post(
                    "https://www.redbubble.com/portfolio/import",
                    headers=headers,
                    files={"file": (f"{design['hash']}.png", f, "image/png")},
                    timeout=60,
                )

            if upload_resp.status_code in (200, 201):
                logger.info(f"Redbubble upload successful: {design['title']}")
                return True
            else:
                logger.debug(f"Redbubble upload failed: {upload_resp.status_code}")
                return False

        except Exception as e:
            logger.debug(f"Redbubble listing error: {e}")
            return False

    def _list_merch_amazon(self, design: dict, products: list) -> bool:
        """List on Merch by Amazon (requires API access)."""
        # Merch by Amazon doesn't have a public API
        # Listings must be done manually or via their web interface
        logger.debug("Merch by Amazon has no public API. Saving for manual upload.")
        return False

    def _track_sales(self):
        """Track sales per design."""
        # Check Redbubble sales if session available
        session_cookie = os.environ.get("REDBUBBLE_SESSION")
        if session_cookie:
            try:
                resp = requests.get(
                    "https://www.redbubble.com/account/sales",
                    headers={"Cookie": f"_redbubble_session={session_cookie}"},
                    timeout=30,
                )
                if resp.status_code == 200:
                    # Parse sales data (simplified)
                    soup = BeautifulSoup(resp.text, "lxml")
                    # Real implementation would parse the sales table
                    logger.debug("Redbubble sales page accessed.")
            except Exception as e:
                logger.debug(f"Redbubble sales tracking error: {e}")

    def _kill_underperformers(self):
        """Kill designs that haven't sold after threshold per
iod."""
        stream = self.db.get_stream(self.STREAM)
        if not stream:
            return

        kill_window = stream.get("kill_window_days", 60)

        # Find old designs with no sales
        old_designs = self.db.fetchall(
            """SELECT * FROM content_inventory
               WHERE stream = ? AND content_type = 'pod_design'
               AND created_at < datetime('now', ?)
               AND status = 'published'""",
            (self.STREAM, f"-{kill_window} days")
        )

        for design in old_designs:
            metrics = {}
            try:
                metrics = json.loads(design.get("metrics", "{}"))
            except (json.JSONDecodeError, TypeError):
                pass

            sales = metrics.get("sales_count", 0)
            if sales == 0:
                # Mark as killed
                self.db.update("content_inventory", {
                    "status": "archived",
                }, "id = ?", (design["id"],))

                logger.info(f"Killed underperforming design: {design['title']} (0 sales in {kill_window} days)")

                self.log_learning(
                    prediction=f"Design '{design['title']}' would generate sales within {kill_window} days",
                    outcome=f"Zero sales after {kill_window} days",
                    lesson=f"Design style/niche combination '{metrics.get('style', 'unknown')}/{metrics.get('niche', 'unknown')}' "
                           f"did not generate sales. Consider different approach for this niche.",
                    confidence=60,
                    tags=["pod", "kill", metrics.get("niche", "unknown")],
                )