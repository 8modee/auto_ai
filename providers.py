#!/usr/bin/env python3
"""
═══════════════════════════════════════════════════════════════
THE INSTITUTION — AI PROVIDER MANAGER
═══════════════════════════════════════════════════════════════
Rotates across free-tier APIs with rate limiting, caching,
exponential backoff, quality tiers, and graceful degradation.
Works with ZERO API keys (falls back to offline templates).
═══════════════════════════════════════════════════════════════
"""

import os
import json
import time
import hashlib
import threading
from datetime import datetime, timedelta
from typing import Optional
from pathlib import Path

import requests

from common import get_config, get_db, get_logger, generate_cache_key, safe_json_loads

logger = get_logger("providers")


class RateLimiter:
    """Per-provider rate limiter with RPM and RPD tracking."""

    def __init__(self, db):
        self.db = db
        self._lock = threading.Lock()

    def can_make_request(self, provider: str) -> bool:
        with self._lock:
            row = self.db.fetchone(
                "SELECT * FROM rate_limits WHERE provider = ?", (provider,)
            )
            if not row:
                return True

            now = datetime.now()

            # Reset minute counter if needed
            last_minute = row.get("last_reset_minute")
            if last_minute:
                last_min_dt = datetime.fromisoformat(last_minute)
                if (now - last_min_dt).total_seconds() >= 60:
                    self.db.execute(
                        "UPDATE rate_limits SET rpm_used = 0, last_reset_minute = ? WHERE provider = ?",
                        (now.isoformat(), provider)
                    )
                    row = dict(row)
                    row["rpm_used"] = 0

            # Reset day counter if needed
            last_day = row.get("last_reset_day")
            if last_day:
                last_day_dt = datetime.fromisoformat(last_day)
                if (now - last_day_dt).total_seconds() >= 86400:

                    self.db.execute(
                        "UPDATE rate_limits SET rpd_used = 0, last_reset_day = ? WHERE provider = ?",
                        (now.isoformat(), provider)
                    )
                    row = dict(row)
                    row["rpd_used"] = 0

            # Check backoff
            backoff_until = row.get("backoff_until")
            if backoff_until:
                if now < datetime.fromisoformat(backoff_until):
                    return False

            # Check limits
            rpm_limit = row.get("rpm_limit", 999)
            rpd_limit = row.get("rpd_limit", 999999)
            rpm_used = row.get("rpm_used", 0)
            rpd_used = row.get("rpd_used", 0)

            if rpm_used >= rpm_limit * 0.9:  # 90% threshold
                return False
            if rpd_used >= rpd_limit * 0.9:
                return False

            return True

    def record_request(self, provider: str):
        with self._lock:
            now = datetime.now()
            self.db.execute(
                """UPDATE rate_limits SET
                   rpm_used = COALESCE(rpm_used, 0) + 1,
                   rpd_used = COALESCE(rpd_used, 0) + 1,
                   last_reset_minute = COALESCE(last_reset_minute, ?),
                   last_reset_day = COALESCE(last_reset_day, ?)
                   WHERE provider = ?""",
                (now.isoformat(), now.isoformat(), provider)
            )

    def record_429(self, provider: str):
        with self._lock:
            row = self.db.fetchone(
                "SELECT consecutive_429s FROM rate_limits WHERE provider = ?", (provider,)
            )
            consecutive = (row["consecutive_429s"] or 0) + 1 if row else 1
            backoff_seconds = min(2 ** consecutive * 5, 300)  # Max 5 min
            backoff_until = (datetime.now() + timedelta(seconds=backoff_seconds)).isoformat()
            self.db.execute(
                "UPDATE rate_limits SET consecutive_429s = ?, backof
f_until = ? WHERE provider = ?",
                (consecutive, backoff_until, provider)
            )
            logger.warning(f"Provider {provider} hit 429. Backoff {backoff_seconds}s (attempt {consecutive})")

    def reset_429(self, provider: str):
        with self._lock:
            self.db.execute(
                "UPDATE rate_limits SET consecutive_429s = 0, backoff_until = NULL WHERE provider = ?",
                (provider,)
            )


class AIProviderManager:
    """
    Manages AI inference across multiple free-tier providers.
    Priority chain: Groq → Gemini → Mistral → Cloudflare → HuggingFace →
                    OpenRouter → Cohere → Ollama → Offline
    """

    def __init__(self):
        self.config = get_config()
        self.db = get_db()
        self.rate_limiter = RateLimiter(self.db)
        self._session = requests.Session()
        self._session.headers.update({"User-Agent": "TheInstitution/1.0"})

    def generate(self, prompt: str, system_prompt: str = None,
                 quality_tier: str = "routine", stream: str = None,
                 task_type: str = None, max_tokens: int = None,
                 temperature: float = 0.7, use_cache: bool = True) -> str:
        """
        Generate text using the best available provider.
        Falls back through the priority chain automatically.
        Returns generated text or offline template response.
        """
        if max_tokens is None:
            max_tokens = self.config.get("ai", "providers", "groq", "max_tokens", default=4096)

        # Check cache first
        cache_key = generate_cache_key(prompt, quality_tier, str(temperature))
        if use_cache:
            cached = self.db.get_cached_response(cache_key)
            if cached:
                self.db.log_ai_usage(
                    provider="cache", model="cached", task_type=task_type,
                    stream=stream, quality_tier=quality_tier, cached=True
                )
                logger.debug(f"Cache
 hit for task: {task_type}")
                return cached

        # Build full prompt
        full_prompt = ""
        if system_prompt:
            full_prompt = f"{system_prompt}\n\n---\n\n{prompt}"
        else:
            full_prompt = prompt

        # Try providers in priority order
        providers = self._get_provider_chain()
        for provider_name in providers:
            if not self.rate_limiter.can_make_request(provider_name):
                continue

            try:
                start_time = time.time()
                response = self._call_provider(
                    provider_name, full_prompt, quality_tier, max_tokens, temperature
                )
                latency_ms = int((time.time() - start_time) * 1000)

                if response:
                    self.rate_limiter.reset_429(provider_name)
                    self.rate_limiter.record_request(provider_name)

                    # Log usage
                    prompt_tokens = len(full_prompt) // 4  # Rough estimate
                    completion_tokens = len(response) // 4
                    self.db.log_ai_usage(
                        provider=provider_name,
                        model=self._get_model(provider_name, quality_tier),
                        task_type=task_type,
                        stream=stream,
                        prompt_tokens=prompt_tokens,
                        completion_tokens=completion_tokens,
                        latency_ms=latency_ms,
                        quality_tier=quality_tier,
                    )

                    # Cache the response
                    if use_cache:
                        self.db.set_cached_response(
                            cache_key, response,
                            provider=provider_name,
                            model=self._get_model(provider_name, quality_tier),
                            prompt_hash=hashlib.md5(full_prompt.encode()).hexdigest(),
                            quality_
tier=quality_tier,
                        )

                    logger.info(
                        f"Generated via {provider_name} ({latency_ms}ms, "
                        f"{completion_tokens}~ tokens) for {task_type or 'general'}"
                    )
                    return response

            except RateLimitError:
                self.rate_limiter.record_429(provider_name)
                continue
            except Exception as e:
                logger.warning(f"Provider {provider_name} failed: {e}")
                continue

        # All providers failed — use offline template
        logger.warning("All providers exhausted. Using offline template generator.")
        return self._offline_generate(prompt, task_type)

    def _get_provider_chain(self) -> list:
        """Get providers sorted by priority, filtered by availability."""
        providers_config = self.config.get("ai", "providers", default={})
        chain = []
        for name, cfg in sorted(providers_config.items(), key=lambda x: x[1].get("priority", 99)):
            if not cfg.get("enabled", False):
                continue
            # Check if API key exists (except ollama and offline)
            if name in ("ollama", "offline"):
                chain.append(name)
                continue
            env_key = self._get_env_key_name(name)
            if env_key and os.environ.get(env_key):
                chain.append(name)
            elif name == "cloudflare":
                if os.environ.get("CLOUDFLARE_ACCOUNT_ID") and os.environ.get("CLOUDFLARE_API_TOKEN"):
                    chain.append(name)
        # Always add offline as last resort
        if "offline" not in chain:
            chain.append("offline")
        return chain

    def _get_env_key_name(self, provider: str) -> str:
        mapping = {
            "groq": "GROQ_API_KEY",
            "gemini": "GEMINI_API_KEY",
            "mistral": "MISTRAL_API_KEY",
            "huggingface": "HUGGINGFACE_TOKEN",
   
         "openrouter": "OPENROUTER_API_KEY",
            "cohere": "COHERE_API_KEY",
        }
        return mapping.get(provider, "")

    def _get_model(self, provider: str, quality_tier: str) -> str:
        models = self.config.get("ai", "providers", provider, "models", default={})
        return models.get(quality_tier, models.get("routine", "unknown"))

    def _call_provider(self, provider: str, prompt: str, quality_tier: str,
                       max_tokens: int, temperature: float) -> Optional[str]:
        """Dispatch to specific provider implementation."""
        method = getattr(self, f"_call_{provider}", None)
        if method is None:
            return None
        model = self._get_model(provider, quality_tier)
        return method(prompt, model, max_tokens, temperature)

    # ─── GROQ ─────────────────────────────────────────────────
    def _call_groq(self, prompt: str, model: str, max_tokens: int, temperature: float) -> Optional[str]:
        api_key = os.environ.get("GROQ_API_KEY")
        if not api_key:
            return None
        resp = self._session.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": max_tokens,
                "temperature": temperature,
            },
            timeout=60,
        )
        if resp.status_code == 429:
            raise RateLimitError("Groq rate limited")
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"]

    # ─── GEMINI ───────────────────────────────────────────────
    def _call_gemini(self, prompt: str, model: str, max_tokens: int, temperature: float) -> Optional[str]:
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            return None
    
    resp = self._session.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}",
            json={
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {
                    "maxOutputTokens": max_tokens,
                    "temperature": temperature,
                },
            },
            timeout=60,
        )
        if resp.status_code == 429:
            raise RateLimitError("Gemini rate limited")
        resp.raise_for_status()
        data = resp.json()
        candidates = data.get("candidates", [])
        if candidates:
            parts = candidates[0].get("content", {}).get("parts", [])
            return "".join(p.get("text", "") for p in parts)
        return None

    # ─── MISTRAL ──────────────────────────────────────────────
    def _call_mistral(self, prompt: str, model: str, max_tokens: int, temperature: float) -> Optional[str]:
        api_key = os.environ.get("MISTRAL_API_KEY")
        if not api_key:
            return None
        resp = self._session.post(
            "https://api.mistral.ai/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": max_tokens,
                "temperature": temperature,
            },
            timeout=60,
        )
        if resp.status_code == 429:
            raise RateLimitError("Mistral rate limited")
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"]

    # ─── CLOUDFLARE WORKERS AI ────────────────────────────────
    def _call_cloudflare(self, prompt: str, model: str, max_tokens: int, temperature: float) -> Optional[str]:
        account_id = os.environ.get("CLOUDFLARE_ACCOUNT_ID")
        api_token = os.environ.get("CLO
UDFLARE_API_TOKEN")
        if not account_id or not api_token:
            return None
        resp = self._session.post(
            f"https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/run/{model}",
            headers={"Authorization": f"Bearer {api_token}", "Content-Type": "application/json"},
            json={
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": max_tokens,
                "temperature": temperature,
            },
            timeout=60,
        )
        if resp.status_code == 429:
            raise RateLimitError("Cloudflare rate limited")
        resp.raise_for_status()
        data = resp.json()
        result = data.get("result", {})
        return result.get("response", "")

    # ─── HUGGINGFACE ──────────────────────────────────────────
    def _call_huggingface(self, prompt: str, model: str, max_tokens: int, temperature: float) -> Optional[str]:
        token = os.environ.get("HUGGINGFACE_TOKEN")
        if not token:
            return None
        resp = self._session.post(
            f"https://api-inference.huggingface.co/models/{model}",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json={
                "inputs": prompt,
                "parameters": {
                    "max_new_tokens": max_tokens,
                    "temperature": temperature,
                    "return_full_text": False,
                },
            },
            timeout=120,
        )
        if resp.status_code == 429:
            raise RateLimitError("HuggingFace rate limited")
        if resp.status_code == 503:
            return None  # Model loading
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, list) and data:
            return data[0].get("generated_text", "")
        return None

    # ─── OPENROUTER ───────────────────────────────────────────
    def _call_openrouter(self, prompt: str,
 model: str, max_tokens: int, temperature: float) -> Optional[str]:
        api_key = os.environ.get("OPENROUTER_API_KEY")
        if not api_key:
            return None
        resp = self._session.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://institution.local",
                "X-Title": "The Institution",
            },
            json={
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": max_tokens,
                "temperature": temperature,
            },
            timeout=60,
        )
        if resp.status_code == 429:
            raise RateLimitError("OpenRouter rate limited")
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"]

    # ─── COHERE ───────────────────────────────────────────────
    def _call_cohere(self, prompt: str, model: str, max_tokens: int, temperature: float) -> Optional[str]:
        api_key = os.environ.get("COHERE_API_KEY")
        if not api_key:
            return None
        resp = self._session.post(
            "https://api.cohere.ai/v1/chat",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "model": model,
                "message": prompt,
                "max_tokens": max_tokens,
                "temperature": temperature,
            },
            timeout=60,
        )
        if resp.status_code == 429:
            raise RateLimitError("Cohere rate limited")
        resp.raise_for_status()
        data = resp.json()
        return data.get("text", "")

    # ─── OLLAMA (LOCAL) ───────────────────────────────────────
    def _call_ollama(self, prompt: str, model: str, max_tokens: int, temperature: float) -> Optio
nal[str]:
        base_url = self.config.get("ai", "providers", "ollama", "base_url", default="http://localhost:11434")
        try:
            resp = self._session.post(
                f"{base_url}/api/generate",
                json={
                    "model": model,
                    "prompt": prompt,
                    "stream": False,
                    "options": {
                        "num_predict": max_tokens,
                        "temperature": temperature,
                    },
                },
                timeout=180,
            )
            resp.raise_for_status()
            data = resp.json()
            return data.get("response", "")
        except requests.ConnectionError:
            return None

    # ─── OFFLINE TEMPLATE GENERATOR ───────────────────────────
    def _offline_generate(self, prompt: str, task_type: str = None) -> str:
        """
        Last-resort template-based generation.
        Always works. No API needed. Produces structured placeholder content.
        """
        prompt_lower = prompt.lower()

        if task_type == "article" or "article" in prompt_lower or "write" in prompt_lower:
            return self._template_article(prompt)
        elif task_type == "product_description" or "product" in prompt_lower:
            return self._template_product(prompt)
        elif task_type == "email" or "newsletter" in prompt_lower:
            return self._template_newsletter(prompt)
        elif task_type == "grant" or "grant" in prompt_lower:
            return self._template_grant(prompt)
        elif task_type == "proposal" or "proposal" in prompt_lower:
            return self._template_proposal(prompt)
        elif task_type == "social" or "post" in prompt_lower:
            return self._template_social(prompt)
        else:
            return self._template_generic(prompt)

    def _template_article(self, prompt: str) -> str:
        # Extract topic from prompt
        topic = "Technology"
        for
 line in prompt.split("\n"):
            if "topic:" in line.lower() or "about:" in line.lower():
                topic = line.split(":", 1)[1].strip()
                break

        return f"""# {topic}: A Comprehensive Guide

## Introduction

{topic} has become increasingly important in modern life. This guide covers everything you need to know, from basics to advanced considerations.

## What Is {topic}?

Understanding the fundamentals is essential before diving deeper. At its core, {topic} involves systematic approaches to achieving specific outcomes through careful planning and execution.

## Key Benefits

1. **Efficiency**: Properly implemented {topic} saves significant time and resources.
2. **Reliability**: Consistent approaches yield predictable results.
3. **Scalability**: Good foundations allow growth without complete rework.
4. **Cost-effectiveness**: Long-term savings outweigh initial investment.

## Getting Started

### Step 1: Assessment
Evaluate your current situation and identify specific needs related to {topic}.

### Step 2: Planning
Create a structured plan with clear milestones and measurable outcomes.

### Step 3: Implementation
Begin with small, manageable steps. Build momentum through early wins.

### Step 4: Optimization
Monitor results, gather feedback, and refine your approach continuously.

## Common Mistakes to Avoid

- Rushing without proper planning
- Ignoring foundational requirements
- Failing to measure outcomes
- Overcomplicating simple processes

## Advanced Considerations

Once basics are mastered, explore automation, integration with existing systems, and advanced optimization techniques specific to {topic}.

## Conclusion

{topic} rewards patience and systematic thinking. Start small, measure everything, and iterate based on evidence rather than assumptions.

---
*This article was generated as part of The Institution's content pipeline. Last updated: {datetime.now().strftime('%B %Y')}*
"""

    def _template_product(self, promp
t: str) -> str:
        return f"""# Product Description

## Overview
A professionally designed digital product created to solve a specific problem efficiently.

## What's Included
- 5+ pages of substantive, actionable content
- Clean, professional layout
- Printable format (A4 and US Letter)
- Editable fields where applicable

## Who This Is For
Anyone looking for a structured, no-nonsense approach to getting organized and taking action.

## How to Use
1. Download the file
2. Print or use digitally
3. Fill in your specific details
4. Follow the structured prompts
5. Review weekly for best results

## Quality Guarantee
Every page contains real, usable content. No filler. No blank lines pretending to be content.

---
*Created by The Institution's Product Engine*
"""

    def _template_newsletter(self, prompt: str) -> str:
        return f"""# This Week's Edition

## Top Story
The most important development this week in our coverage area, with analysis of what it means for you.

## Quick Hits
- Item 1: Brief summary with actionable takeaway
- Item 2: Brief summary with actionable takeaway
- Item 3: Brief summary with actionable takeaway

## Resource Spotlight
A tool, guide, or resource that provides genuine value. Not sponsored. Actually useful.

## From Our Sites
Recent articles you may have missed, with one-line summaries and links.

## Until Next Week
A brief sign-off with one thought to carry forward.

---
*You received this because you subscribed. Unsubscribe anytime.*
"""

    def _template_grant(self, prompt: str) -> str:
        return f"""# Grant Application Draft

## Applicant Summary
[To be completed with founder's details upon approval]

## Project Description
This project addresses a demonstrated need through practical, measurable interventions. The approach is evidence-based and designed for sustainability beyond the funding period.

## Objectives
1. Primary objective with measurable outcome
2. Secondary objective with timeline
3. Tertiary objective with
 evaluation method

## Budget Justification
All requested funds are directly tied to project deliverables. No administrative overhead exceeds 10%.

## Expected Outcomes
- Quantifiable outcome 1 (metric, target, timeframe)
- Quantifiable outcome 2 (metric, target, timeframe)
- Community/systemic impact beyond direct beneficiaries

## Sustainability Plan
Post-funding continuity is addressed through [specific mechanism].

## Supporting Evidence
[References and data to be attached upon review]

---
*DRAFT — Requires founder review and approval before submission*
"""

    def _template_proposal(self, prompt: str) -> str:
        return f"""# Project Proposal

## Understanding
Based on your requirements, I understand you need [specific deliverable] to achieve [specific outcome].

## Approach
I will deliver this through a structured process:
1. Discovery and requirements confirmation (Day 1)
2. Initial delivery (Day 2-3)
3. Revision based on feedback (Day 4)
4. Final delivery with documentation (Day 5)

## Deliverables
- Primary deliverable in specified format
- Documentation for ongoing use
- One round of revisions included

## Timeline
5 business days from acceptance.

## Investment
$[amount] — fixed price, no surprises.

## Why Me
Technical expertise combined with clear communication and reliable delivery. References available.

---
*DRAFT — Requires founder approval before sending*
"""

    def _template_social(self, prompt: str) -> str:
        return f"""Here's a practical tip that saved me hours this week:

[Specific, actionable tip related to the topic]

The key insight: [one sentence that reframes the problem]

If you're dealing with [related challenge], try this approach first. It's counterintuitive but works.

#productivity #tech #tips
"""

    def _template_generic(self, prompt: str) -> str:
        return f"""[Generated response for: {prompt[:100]}...]

This is an offline-generated response. The AI provider chain was exhausted.
The system will retry with live 
providers on the next cycle.

Content quality: TEMPLATE (lowest tier)
Action: This output should be reviewed before any external use.
"""

    def get_provider_status(self) -> dict:
        """Get current status of all providers for dashboard."""
        providers = self.config.get("ai", "providers", default={})
        status = {}
        for name, cfg in providers.items():
            available = self.rate_limiter.can_make_request(name)
            env_key = self._get_env_key_name(name)
            has_key = bool(os.environ.get(env_key)) if env_key else (name in ("ollama", "offline"))
            status[name] = {
                "enabled": cfg.get("enabled", False),
                "available": available,
                "has_credentials": has_key,
                "priority": cfg.get("priority", 99),
                "model": self._get_model(name, "routine"),
            }
        return status


class RateLimitError(Exception):
    """Raised when a provider returns 429."""
    pass


# ─── MODULE-LEVEL SINGLETON ───────────────────────────────────
_provider_manager = None
_provider_lock = threading.Lock()

def get_ai_provider() -> AIProviderManager:
    global _provider_manager
    if _provider_manager is None:
        with _provider_lock:
            if _provider_manager is None:
                _provider_manager = AIProviderManager()
    return _provider_manager