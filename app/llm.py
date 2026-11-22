"""Optional server-side LLM for the "Ask the AI" button.

Defaults to LOCAL/OFFLINE models: anything that speaks the OpenAI-compatible
chat API (Ollama, LM Studio, llama.cpp server, vLLM, LocalAI…). Hosted models
(OpenAI, OpenRouter, Groq…) work through the same path; Anthropic Claude uses
the official SDK."""
from __future__ import annotations

import httpx

from .config import settings

DEFAULT_MODELS = {"ollama": "llama3.1:8b", "openai": "gpt-4o-mini", "anthropic": "claude-opus-5-5"}

SYSTEM = (
    "You are the house analyst for 'ETF Me in the ETN', a tracker for 2x/3x bull leveraged ETFs/ETNs. "
    "The user buys leveraged bulls after big crashes. Use ONLY the JSON context provided (prices, drawdown, "
    "past crash episodes, Monte Carlo). Be concise, concrete and honest about volatility decay, path "
    "dependence and the risk of a fund going to ~0 or being closed. Give: 1) where it is in the crash ladder, "
    "2) what history says happened after similar drawdowns, 3) key risks, 4) a probability-weighted view for "
    "the next 3-12 months. This is not financial advice; say so in one short line at the end."
)


def status() -> dict:
    p = settings.llm_provider
    return {"provider": p, "enabled": p not in ("", "none"),
            "model": settings.llm_model or DEFAULT_MODELS.get(p, ""),
            "base_url": settings.llm_base_url if p not in ("anthropic", "none") else None}


async def ask(question: str, context: dict) -> dict:
    import json
    p = settings.llm_provider
    model = settings.llm_model or DEFAULT_MODELS.get(p, "")
    user = f"Context JSON:\n{json.dumps(context, default=str)}\n\nQuestion: {question}"

    if p == "anthropic":
        import anthropic
        client = anthropic.AsyncAnthropic(api_key=settings.llm_api_key or None)
        resp = await client.beta.messages.create(
            model=model, max_tokens=16000, system=SYSTEM,
            messages=[{"role": "user", "content": user}],
            output_config={"effort": "medium"},
            betas=["server-side-fallback-2026-07-01"], fallbacks="default",
        )
        if resp.stop_reason == "refusal":
            return {"provider": p, "model": model, "answer": "The model declined to answer this request."}
        text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
        return {"provider": p, "model": model, "answer": text}

    if p in ("ollama", "openai", "openai-compatible", "lmstudio", "vllm", "openrouter"):
        headers = {"Authorization": f"Bearer {settings.llm_api_key}"} if settings.llm_api_key else {}
        async with httpx.AsyncClient(timeout=180) as client:
            r = await client.post(settings.llm_base_url.rstrip("/") + "/chat/completions", headers=headers, json={
                "model": model, "temperature": 0.3,
                "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}],
            })
            r.raise_for_status()
            return {"provider": p, "model": model, "answer": r.json()["choices"][0]["message"]["content"]}

    raise RuntimeError("LLM_PROVIDER is not configured (set it to ollama, openai or anthropic)")
