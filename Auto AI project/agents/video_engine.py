#!/usr/bin/env python3
"""
═══════════════════════════════════════════════════════════════
THE INSTITUTION — STREAM 3: FACELESS VIDEO CONTENT
═══════════════════════════════════════════════════════════════
Full faceless video pipeline:
- AI-generated scripts via LLM
- TTS voiceover via edge-tts (free, no API key)
- Stock footage from Pexels/Pixabay API (free)
- Video composition via ffmpeg (free)
- Thumbnail generation via Pillow
- YouTube upload via YouTube Data API v3
- Vertical format for TikTok/Shorts/Reels
- 3-5 videos/week, 2-3 channels
═══════════════════════════════════════════════════════════════
"""

import os
import json
import subprocess
import tempfile
import asyncio
import random
from datetime import datetime
from pathlib import Path
from typing import Optional

import requests

from common import get_db, get_config, get_logger, INSTITUTION_ROOT, now_iso, slugify
from agents.base import BaseAgent

logger = get_logger("video_engine")

try:
    from PIL import Image, ImageDraw, ImageFont, ImageFilter
    PILLOW_AVAILABLE = True
except ImportError:
    PILLOW_AVAILABLE = False

try:
    import edge_tts
    EDGE_TTS_AVAILABLE = True
except ImportError:
    EDGE_TTS_AVAILABLE = False
    logger.warning("edge-tts not installed. TTS unavailable.")


class VideoEngineAgent(BaseAgent):
    AGENT_NAME = "video_engine"
    AGENT_TYPE = "revenue_stream"
    STREAM = "video_content"
    DEFAULT_INTERVAL_SECONDS = 21600  # Every 6 hours

    def __init__(self):
        super().__init__()
        self.videos_dir = INSTITUTION_ROOT / "videos"
        self.scripts_dir = self.videos_dir / "scripts"
        self.audio_dir = self.videos_dir / "audio"
        self.footage_dir = self.videos_dir / "footage"
        self.output_dir = self.videos_dir / "output"
        self.thumbs_dir = self.videos_dir / "thumbnails"
        for d in [self.scripts_dir, self.audio_dir, self.footage_dir, self.output_dir, self.thumbs_dir]:
            d.mkdir(parents=True, exist_ok=True)

        self._ffmpeg_available = self._check_ffmpeg()

    def _check_ffmpeg(self) -> bool:
        """Check if ffmpeg is installed."""
        try:
            result = subprocess.run(["ffmpeg", "-version"], capture_output=True, timeout=10)
            return result.returncode == 0
        except (FileNotFoundError, subprocess.TimeoutExpired):
            logger.warning("ffmpeg not found. Video composition unavailable.")
            return False

    def run_once(self):
        """Main cycle: generate script, TTS, footage, compose, thumbnail, upload."""
        if not self.should_run_today():
            return "Stream disabled or inactive"

        stream_cfg = self.get_stream_config()
        channels = stream_cfg.get("channels", [])
        if not channels:
            return "No channels configured"

        # Check weekly quota
        videos_per_week = stream_cfg.get("schedule", {}).get("videos_per_week", 4)
        week_count = self.db.fetchone(
            "SELECT COUNT(*) as cnt FROM content_inventory WHERE stream = ? AND content_type = 'video' AND created_at > datetime('now', '-7 days')",
            (self.STREAM,)
        )
        if week_count and week_count["cnt"] >= videos_per_week:
            return f"Weekly video quota met ({week_count['cnt']}/{videos_per_week})"

        # Select channel
        channel = random.choice(channels)
        is_vertical = channel.get("format") == "vertical"
        target_length = channel.get("target_length_seconds", 480 if not is_vertical else 45)

        # Step 1: Generate script
        script = self._generate_script(channel, target_length)
        if not script:
            return "Script generation failed"

        # Save script
        script_slug = slugify(script["title"])
        script_path = self.scripts_dir / f"{script_slug}.json"
        script_path.write_text(json.dumps(script, indent=2), encoding="utf-8")

        # Step 2: Generate TTS audio
        audio_path = self._generate_tts(script, script_slug, stream_cfg)
        if not audio_path:
            return "TTS generation failed"

        # Step 3: Download stock footage
        footage_paths = self._download_footage(script, script_slug, stream_cfg)

        # Step 4: Compose video
        if self._ffmpeg_available and footage_paths:
            video_path = self._compose_video(script_slug, audio_path, footage_paths, is_vertical, target_length)
        else:
            # Create audio-only video if no footage
            video_path = self._compose_audio_only(script_slug, audio_path, is_vertical)

        if not video_path:
            return "Video composition failed"

        # Step 5: Generate thumbnail
        thumb_path = self._generate_thumbnail(script, script_slug, is_vertical)

        # Step 6: Upload to YouTube
        upload_result = self._upload_youtube(script, video_path, thumb_path, channel)

        # Record in inventory
        self.db.insert("content_inventory", {
            "stream": self.STREAM,
            "content_type": "video",
            "title": script["title"],
            "slug": script_slug,
            "status": "published" if upload_result else "draft",
            "published_at": now_iso() if upload_result else None,
            "metrics": json.dumps({
                "channel": channel.get("name", ""),
                "format": "vertical" if is_vertical else "landscape",
                "duration_seconds": target_length,
                "uploaded": upload_result,
            }),
        })

        status = "uploaded" if upload_result else "composed (not uploaded)"
        return f"Video {status}: {script['title']}"

    def _generate_script(self, channel: dict, target_length: int) -> Optional[dict]:
        """Generate a video script using AI."""
        niche = channel.get("niche", "technology")
        is_short = target_length < 90

        if is_short:
            prompt = f"""Write a script for a {target_length}-second vertical video (YouTube Short / TikTok / Reel).
NICHE: {niche}

REQUIREMENTS:
- Hook in first 3 seconds (pattern interrupt, bold claim, or question)
- One clear point or tip
- Fast-paced, energetic language
- End with a call to action (subscribe, comment, etc.)
- Exactly {target_length} seconds when read aloud (~2.5 words per second)
- No visual directions needed

OUTPUT FORMAT (JSON):
{{
  "title": "Video title (max 70 chars)",
  "description": "Video description for YouTube (2-3 sentences + hashtags)",
  "tags": ["tag1", "tag2", "tag3", "tag4", "tag5"],
  "script": "The full spoken script text",
  "hook": "The opening hook line",
  "scenes": [
    {{"text": "spoken text for this scene", "visual_keyword": "search term for stock footage", "duration_seconds": 5}}
  ]
}}
"""
        else:
            prompt = f"""Write a script for a {target_length}-second YouTube video.
NICHE: {niche}
CHANNEL: {channel.get('name', 'Tech Channel')}

REQUIREMENTS:
- Strong hook in first 10 seconds
- 3-5 main points with clear transitions
- Conversational, engaging tone
- Include specific examples and data where possible
- End with summary and call to action
- Approximately {target_length} seconds when read aloud (~2.5 words per second = ~{int(target_length * 2.5)} words)
- Australian English

OUTPUT FORMAT (JSON):
{{
  "title": "Video title (max 70 chars, compelling)",
  "description": "Video description (3-5 sentences, include keywords naturally, add timestamps)",
  "tags": ["tag1", "tag2", "tag3", "tag4", "tag5", "tag6", "tag7", "tag8"],
  "script": "The full spoken script text",
  "hook": "The opening hook",
  "scenes": [
    {{"text": "spoken text for this scene", "visual_keyword": "search term for stock footage", "duration_seconds": 15}}
  ]
}}

Generate 6-10 scenes that together cover the full script duration.
"""

        response = self.generate_text(
            prompt=prompt,
            quality_tier="routine",
            temperature=0.8,
            max_tokens=4096,
        )

        if not response:
            return None

        # Parse JSON
        try:
            json_start = response.find("{")
            json_end = response.rfind("}") + 1
            if json_start >= 0 and json_end > json_start:
                data = json.loads(response[json_start:json_end])
                if "script" in data and "title" in data:
                    if "scenes" not in data:
                        data["scenes"] = [{"text": data["script"], "visual_keyword": niche, "duration_seconds": target_length}]
                    return data
        except json.JSONDecodeError:
            pass

        # Fallback script
        return {
            "title": f"Top {niche.title()} Tips You Need to Know",
            "description": f"Essential {niche} tips and insights. Subscribe for more content like this.",
            "tags": [niche, "tips", "guide", "2026", "explained"],
            "script": f"Welcome back to the channel. Today we're covering essential {niche} tips that everyone should know. "
                      f"Let's dive straight in. First, always start with the fundamentals. "
                      f"Understanding the basics gives you a foundation to build on. "
                      f"Second, consistency beats intensity. Small daily actions compound over time. "
                      f"Third, learn from others' mistakes. You don't have to make every error yourself. "
                      f"Finally, take action today. Don't wait for perfect conditions. "
                      f"If you found this helpful, subscribe and hit the notification bell. See you in the next one.",
            "hook": f"Here are the {niche} tips I wish someone told me years ago.",
            "scenes": [
                {"text": f"Here are the {niche} tips I wish someone told me years ago.", "visual_keyword": f"{niche} introduction", "duration_seconds": 10},
                {"text": "First, always start with the fundamentals.", "visual_keyword": "learning basics", "duration_seconds": 15},
                {"text": "Second, consistency beats intensity.", "visual_keyword": "daily routine productivity", "duration_seconds": 15},
                {"text": "Third, learn from others' mistakes.", "visual_keyword": "team collaboration", "duration_seconds": 15},
                {"text": "Finally, take action today.", "visual_keyword": "success achievement", "duration_seconds": 10},
                {"text": "Subscribe for more.", "visual_keyword": "subscribe youtube", "duration_seconds": 5},
            ],
        }

    def _generate_tts(self, script: dict, slug: str, stream_cfg: dict) -> Optional[Path]:
        """Generate text-to-speech audio using edge-tts."""
        output_path = self.audio_dir / f"{slug}.mp3"

        if not EDGE_TTS_AVAILABLE:
            logger.warning("edge-tts not available. Cannot generate audio.")
            return None

        tts_cfg = stream_cfg.get("tts", {})
        voice = tts_cfg.get("voice", "en-AU-WilliamNeural")
        fallback_voice = tts_cfg.get("fallback_voice", "en-US-GuyNeural")

        script_text = script.get("script", "")
        if not script_text:
            # Build from scenes
            script_text = " ".join(s.get("text", "") for s in script.get("scenes", []))

        if not script_text:
            logger.warning("No script text for TTS.")
            return None

        try:
            # Run edge-tts asynchronously
            asyncio.run(self._run_edge_tts(script_text, str(output_path), voice))
            if output_path.exists() and output_path.stat().st_size > 1000:
                logger.info(f"TTS generated: {output_path} ({output_path.stat().st_size} bytes)")
                return output_path
        except Exception as e:
            logger.warning(f"Primary voice failed ({voice}): {e}. Trying fallback.")
            try:
                asyncio.run(self._run_edge_tts(script_text, str(output_path), fallback_voice))
                if output_path.exists() and output_path.stat().st_size > 1000:
                    return output_path
            except Exception as e2:
                logger.error(f"Fallback voice also failed: {e2}")

        return None

    async def _run_edge_tts(self, text: str, output_path: str, voice: str):
        """Run edge-tts asynchronously."""
        communicate = edge_tts.Communicate(text, voice)
        await communicate.save(output_path)

    def _download_footage(self, script: dict, slug: str, stream_cfg: dict) -> list:
        """Download stock footage from Pexels/Pixabay."""
        scenes = script.get("scenes", [])
        footage_cfg = stream_cfg.get("footage", {})
        max_clips = footage_cfg.get("clips_per_video", 8)
        max_duration = footage_cfg.get("max_clip_duration_seconds", 15)

        footage_paths = []
        scene_footage_dir = self.footage_dir / slug
        scene_footage_dir.mkdir(parents=True, exist_ok=True)

        for i, scene in enumerate(scenes[:max_clips]):
            keyword = scene.get("visual_keyword", "technology")
            clip_path = scene_footage_dir / f"clip_{i:02d}.mp4"

            if clip_path.exists():
                footage_paths.append(clip_path)
                continue

            # Try Pexels first
            downloaded = self._download_pexels(keyword, str(clip_path), max_duration)
            if not downloaded:
                # Try Pixabay
                downloaded = self._download_pixabay(keyword, str(clip_path), max_duration)

            if downloaded:
                footage_paths.append(clip_path)
            else:
                logger.debug(f"No footage found for scene {i}: '{keyword}'")

        logger.info(f"Downloaded {len(footage_paths)}/{len(scenes)} footage clips")
        return footage_paths

    def _download_pexels(self, keyword: str, output_path: str, max_duration: int) -> bool:
        """Download a video clip from Pexels API."""
        api_key = os.environ.get("PEXELS_API_KEY")
        if not api_key:
            return False

        try:
            resp = requests.get(
                "https://api.pexels.com/videos/search",
                headers={"Authorization": api_key},
                params={
                    "query": keyword,
                    "per_page": 5,
                    "orientation": "landscape",
                },
                timeout=30,
            )

            if resp.status_code != 200:
                return False

            data = resp.json()
            videos = data.get("videos", [])

            for video in videos:
                duration = video.get("duration", 999)
                if duration > max_duration * 2:
                    continue

                # Get best quality file that's not too large
                video_files = video.get("video_files", [])
                best_file = None
                for vf in video_files:
                    if vf.get("quality") == "sd" and vf.get("width", 0) >= 640:
                        best_file = vf
                        break
                if not best_file and video_files:
                    best_file = video_files[0]

                if best_file:
                    link = best_file.get("link", "")
                    if link:
                        dl_resp = requests.get(link, stream=True, timeout=60)
                        if dl_resp.status_code == 200:
                            with open(output_path, "wb") as f:
                                for chunk in dl_resp.iter_content(chunk_size=8192):
                                    f.write(chunk)
                            if Path(output_path).stat().st_size > 10000:
                                return True
                            else:
                                Path(output_path).unlink(missing_ok=True)

            return False

        except Exception as e:
            logger.debug(f"Pexels download error: {e}")
            return False

    def _download_pixabay(self, keyword: str, output_path: str, max_duration: int) -> bool:
        """Download a video clip from Pixabay API."""
        api_key = os.environ.get("PIXABAY_API_KEY")
        if not api_key:
            return False

        try:
            resp = requests.get(
                "https://pixabay.com/api/videos/",
                params={
                    "key": api_key,
                    "q": keyword,
                    "per_page": 5,
                },
                timeout=30,
            )

            if resp.status_code != 200:
                return False

            data = resp.json()
            hits = data.get("hits", [])

            for hit in hits:
                duration = hit.get("duration", 999)
                if duration > max_duration * 2:
                    continue

                videos = hit.get("videos", {})
                # Prefer medium quality
                video_url = videos.get("medium", {}).get("url", "") or videos.get("small", {}).get("url", "")

                if video_url:
                    dl_resp = requests.get(video_url, stream=True, timeout=60)
                    if dl_resp.status_code == 200:
                        with open(output_path, "wb") as f:
                            for chunk in dl_resp.iter_content(chunk_size=8192):
                                f.write(chunk)
                        if Path(output_path).stat().st_size > 10000:
                            return True
                        else:
                            Path(output_path).unlink(missing_ok=True)

            return False

        except Exception as e:
            logger.debug(f"Pixabay download error: {e}")
            return False

    def _compose_video(self, slug: str, audio_path: Path, footage_paths: list,
                       is_vertical: bool, target_length: int) -> Optional[Path]:
        """Compose final video using ffmpeg."""
        output_path = self.output_dir / f"{slug}.mp4"

        if not footage_paths:
            return self._compose_audio_only(slug, audio_path, is_vertical)

        try:
            # Get audio duration
            audio_duration = self._get_media_duration(str(audio_path))
            if not audio_duration:
                audio_duration = target_length

            # Calculate duration per clip
            num_clips = len(footage_paths)
            clip_duration = audio_duration / max(num_clips, 1)

            # Build ffmpeg filter complex
            # First, create a concat file
            concat_file = self.output_dir / f"{slug}_concat.txt"
            with open(concat_file, "w") as f:
                for fp in footage_paths:
                    f.write(f"file '{fp}'\n")

            # Resolution
            if is_vertical:
                width, height = 1080, 1920
            else:
                width, height = 1920, 1080

            # Compose: concat footage, scale, add audio
            cmd = [
                "ffmpeg", "-y",
                "-f", "concat", "-safe", "0", "-i", str(concat_file),
                "-i", str(audio_path),
                "-vf", f"scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:black",
                "-c:v", "libx264",
                "-preset", "fast",
                "-crf", "23",
                "-c:a", "aac",
                "-b:a", "128k",
                "-shortest",
                "-movflags", "+faststart",
                str(output_path),
            ]

            result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)

            # Cleanup concat file
            concat_file.unlink(missing_ok=True)

            if result.returncode == 0 and output_path.exists():
                logger.info(f"Video composed: {output_path} ({output_path.stat().st_size} bytes)")
                return output_path
            else:
                logger.error(f"ffmpeg compose failed: {result.stderr[:500]}")
                return None

        except subprocess.TimeoutExpired:
            logger.error("ffmpeg compose timed out")
            return None
        except Exception as e:
            logger.error(f"Video composition error: {e}")
            return None

    def _compose_audio_only(self, slug: str, audio_path: Path, is_vertical: bool) -> Optional[Path]:
        """Create a video with static background and audio (no footage available)."""
        output_path = self.output_dir / f"{slug}.mp4"

        if is_vertical:
            width, height = 1080, 1920
        else:
            width, height = 1920, 1080

        try:
            # Generate a simple gradient background image
            bg_path = self.output_dir / f"{slug}_bg.png"
            self._generate_background(bg_path, width, height)

            cmd = [
                "ffmpeg", "-y",
                "-loop", "1", "-i", str(bg_path),
                "-i", str(audio_path),
                "-c:v", "libx264",
                "-tune", "stillimage",
                "-c:a", "aac",
                "-b:a", "128k",
                "-pix_fmt", "yuv420p",
                "-shortest",
                "-movflags", "+faststart",
                str(output_path),
            ]

            result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
            bg_path.unlink(missing_ok=True)

            if result.returncode == 0 and output_path.exists():
                logger.info(f"Audio-only video composed: {output_path}")
                return output_path
            else:
                logger.error(f"Audio-only compose failed: {result.stderr[:300]}")
                return None

        except Exception as e:
            logger.error(f"Audio-only composition error: {e}")
            return None

    def _generate_background(self, path: Path, width: int, height: int):
        """Generate a gradient background image."""
        if not PILLOW_AVAILABLE:
            # Create minimal valid PNG
            img = Image.new("RGB", (width, height), (30, 30, 50))
            img.save(str(path))
            return

        img = Image.new("RGB", (width, height))
        draw = ImageDraw.Draw(img)

        # Gradient from dark blue to dark purple
        for y in range(height):
            ratio = y / height
            r = int(20 + ratio * 30)
            g = int(20 + ratio * 10)
            b = int(50 + ratio * 40)
            draw.line([(0, y), (width, y)], fill=(r, g, b))

        img.save(str(path))

    def _get_media_duration(self, path: str) -> Optional[float]:
        """Get duration of a media file using ffprobe."""
        try:
            result = subprocess.run(
                ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
                 "-of", "csv=p=0", path],
                capture_output=True, text=True, timeout=30,
            )
            if result.returncode == 0:
                return float(result.stdout.strip())
        except (subprocess.TimeoutExpired, ValueError, FileNotFoundError):
            pass
        return None

    def _generate_thumbnail(self, script: dict, slug: str, is_vertical: bool) -> Optional[Path]:
        """Generate a video thumbnail using Pillow."""
        if not PILLOW_AVAILABLE:
            return None

        if is_vertical:
            width, height = 1080, 1920
        else:
            width, height = 1280, 720

        output_path = self.thumbs_dir / f"{slug}_thumb.png"

        try:
            img = Image.new("RGB", (width, height), (20, 20, 40))
            draw = ImageDraw.Draw(img)

            # Gradient background
            for y in range(height):
                ratio = y / height
                r = int(20 + ratio * 40)
                g = int(30 + ratio * 20)
                b = int(80 + ratio * 60)
                draw.line([(0, y), (width, y)], fill=(r, g, b))

            # Accent bar
            accent_color = (233, 69, 96)  # Red-pink
            draw.rectangle([0, height - 8, width, height], fill=accent_color)

            # Title text
            try:
                if is_vertical:
                    font_size = 64
                else:
                    font_size = 56
                title_font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", font_size)
            except (IOError, OSError):
                title_font = ImageFont.load_default()

            title = script.get("title", "Video Title")
            # Wrap title
            words = title.split()
            lines = []
            current = ""
            max_chars = 20 if is_vertical else 30
            for word in words:
                if len(f"{current} {word}".strip()) > max_chars:
                    lines.append(current.strip())
                    current = word
                else:
                    current = f"{current} {word}".strip()
            if current:
                lines.append(current.strip())

            y_start = height // 3
            for line in lines[:4]:
                bbox = draw.textbbox((0, 0), line, font=title_font)
                text_w = bbox[2] - bbox[0]
                x = (width - text_w) // 2
                # Shadow
                draw.text((x + 2, y_start + 2), line, fill=(0, 0, 0), font=title_font)
                # Main text
                draw.text((x, y_start), line, fill=(255, 255, 255), font=title_font)
                y_start += font_size + 10

            img.save(str(output_path), "PNG", quality=95)
            logger.info(f"Thumbnail generated: {output_path}")
            return output_path

        except Exception as e:
            logger.error(f"Thumbnail generation error: {e}")
            return None

    def _upload_youtube(self, script: dict, video_path: Path,
                        thumb_path: Optional[Path], channel: dict) -> bool:
        """Upload video to YouTube via Data API v3."""
        api_key = os.environ.get("YOUTUBE_API_KEY")
        client_id = os.environ.get("YOUTUBE_CLIENT_ID")
        client_secret = os.environ.get("YOUTUBE_CLIENT_SECRET")
        refresh_token = os.environ.get("YOUTUBE_REFRESH_TOKEN")

        if not all([api_key, client_id, client_secret, refresh_token]):
            logger.info("YouTube credentials not configured. Video saved locally.")
            return False

        if not video_path.exists():
            logger.warning(f"Video file not found: {video_path}")
            return False

        try:
            # Get access token from refresh token
            token_resp = requests.post(
                "https://oauth2.googleapis.com/token",
                data={
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "refresh_token": refresh_token,
                    "grant_type": "refresh_token",
                },
                timeout=30,
            )

            if token_resp.status_code != 200:
                logger.warning(f"YouTube token refresh failed: {token_resp.status_code}")
                return False

            access_token = token_resp.json().get("access_token")
            if not access_token:
                return False

            # Upload video
            headers = {
                "Authorization": f"Bearer {access_token}",
            }

            metadata = {
                "snippet": {
                    "title": script.get("title", "Untitled Video")[:100],
                    "description": script.get("description", "")[:5000],
                    "tags": script.get("tags", [])[:30],
                    "categoryId": "28",  # Science & Technology
                },
                "status": {
                    "privacyStatus": "private",  # Start private for safety
                    "selfDeclaredMadeForKids": False,
                },
            }

            # Resumable upload
            upload_resp = requests.post(
                "https://www.googleapis.com/upload/youtube/v3/videos?uploadType=resumable&part=snippet,status",
                headers={
                    **headers,
                    "Content-Type": "application/json; charset=UTF-8",
                    "X-Upload-Content-Type": "video/mp4",
                },
                json=metadata,
                timeout=30,
            )

            if upload_resp.status_code != 200:
                logger.warning(f"YouTube upload init failed: {upload_resp.status_code}")
                return False

            upload_url = upload_resp.headers.get("Location")
            if not upload_url:
                return False

            # Upload file content
            file_size = video_path.stat().st_size
            with open(video_path, "rb") as f:
                content_resp = requests.put(
                    upload_url,
                    headers={
                        **headers,
                        "Content-Type": "video/mp4",
                        "Content-Length": str(file_size),
                    },
                    data=f,
                    timeout=600,
                )

            if content_resp.status_code in (200, 201):
                video_data = content_resp.json()
                video_id = video_data.get("id", "")
                logger.info(f"YouTube upload successful: {video_id}")

                # Upload thumbnail if available
                if thumb_path and thumb_path.exists() and video_id:
                    self._upload_thumbnail(video_id, thumb_path, access_token)

                return True
            else:
                logger.warning(f"YouTube content upload failed: {content_resp.status_code}")
                return False

        except Exception as e:
            logger.error(f"YouTube upload error: {e}")
            return False

    def _upload_thumbnail(self, video_id: str, thumb_path: Path, access_token: str):
        """Upload custom thumbnail to YouTube."""
        try:
            with open(thumb_path, "rb") as f:
                resp = requests.post(
                    f"https://www.googleapis.com/upload/youtube/v3/thumbnails/set?videoId={video_id}",
                    headers={"Authorization": f"Bearer {access_token}"},
                    files={"media": ("thumbnail.png", f, "image/png")},
                    timeout=60,
                )
            if resp.status_code == 200:
                logger.info(f"Thumbnail uploaded for video {video_id}")
            else:
                logger.debug(f"Thumbnail upload failed: {resp.status_code}")
        except Exception as e:
            logger.debug(f"Thumbnail upload error: {e}")