"""LLM provider router. Routes each call to Anthropic or OpenAI based on the
model ID prefix. Initializes each provider lazily so the server can run with
only one of the two API keys configured."""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

from anthropic import AsyncAnthropic
from openai import AsyncOpenAI


# Per-million-token prices (USD) for post-run cost reporting only.
# Verify against each provider's pricing page before taking these at face value.
ANTHROPIC_PRICES = {
    "claude-opus-4-7": {"in": 15.0, "out": 75.0},
    "claude-sonnet-4-6": {"in": 3.0, "out": 15.0},
    "claude-haiku-4-5-20251001": {"in": 1.0, "out": 5.0},
}

OPENAI_PRICES = {
    "gpt-5": {"in": 1.25, "out": 10.0},
    "gpt-5-mini": {"in": 0.25, "out": 2.0},
    "gpt-5-nano": {"in": 0.05, "out": 0.40},
    "gpt-4.1": {"in": 2.0, "out": 8.0},
}

DEFAULT_MODEL_HEAVY = "claude-sonnet-4-6"
DEFAULT_MODEL_LIGHT = "claude-haiku-4-5-20251001"


def _is_openai_model(model: str) -> bool:
    m = (model or "").lower()
    return m.startswith("gpt-") or m.startswith("o1") or m.startswith("o3") or m.startswith("o4")


def _openai_supports_temperature(model: str) -> bool:
    """GPT-5 family and reasoning models (o1/o3/o4) reject any non-default
    temperature with a 400 error. Older chat models (gpt-4.1, gpt-4o) accept it."""
    m = (model or "").lower()
    if m.startswith("gpt-5"):
        return False
    if m.startswith("o1") or m.startswith("o3") or m.startswith("o4"):
        return False
    return True


def _price_table(model: str) -> Optional[dict[str, float]]:
    if _is_openai_model(model):
        return OPENAI_PRICES.get(model)
    return ANTHROPIC_PRICES.get(model)


def estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    prices = _price_table(model)
    if not prices:
        return 0.0
    return (input_tokens * prices["in"] + output_tokens * prices["out"]) / 1_000_000


@dataclass
class CompletionResult:
    text: str
    input_tokens: int
    output_tokens: int
    model: str

    @property
    def cost_usd(self) -> float:
        return estimate_cost(self.model, self.input_tokens, self.output_tokens)


class LLMClient:
    """Unified client. Lazily initializes whichever provider the first call needs."""

    def __init__(self) -> None:
        self._anthropic: Optional[AsyncAnthropic] = None
        self._openai: Optional[AsyncOpenAI] = None

    def _anthropic_client(self) -> AsyncAnthropic:
        if self._anthropic is None:
            key = os.environ.get("ANTHROPIC_API_KEY")
            if not key:
                raise RuntimeError("ANTHROPIC_API_KEY not set")
            self._anthropic = AsyncAnthropic(api_key=key)
        return self._anthropic

    def _openai_client(self) -> AsyncOpenAI:
        if self._openai is None:
            key = os.environ.get("OPENAI_API_KEY")
            if not key:
                raise RuntimeError("OPENAI_API_KEY not set")
            self._openai = AsyncOpenAI(api_key=key)
        return self._openai

    async def complete(
        self,
        prompt: str,
        *,
        model: str = DEFAULT_MODEL_HEAVY,
        max_tokens: int = 1024,
        system: Optional[str] = None,
        temperature: Optional[float] = None,
    ) -> CompletionResult:
        if _is_openai_model(model):
            return await self._complete_openai(
                prompt, model=model, max_tokens=max_tokens,
                system=system, temperature=temperature,
            )
        return await self._complete_anthropic(
            prompt, model=model, max_tokens=max_tokens,
            system=system, temperature=temperature,
        )

    async def _complete_anthropic(
        self, prompt: str, *, model: str, max_tokens: int,
        system: Optional[str], temperature: Optional[float],
    ) -> CompletionResult:
        client = self._anthropic_client()
        kwargs: dict = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": prompt}],
        }
        if system:
            kwargs["system"] = system
        if temperature is not None:
            kwargs["temperature"] = temperature
        msg = await client.messages.create(**kwargs)
        text_parts = [b.text for b in msg.content if getattr(b, "type", None) == "text"]
        return CompletionResult(
            text="".join(text_parts).strip(),
            input_tokens=msg.usage.input_tokens,
            output_tokens=msg.usage.output_tokens,
            model=model,
        )

    async def _complete_openai(
        self, prompt: str, *, model: str, max_tokens: int,
        system: Optional[str], temperature: Optional[float],
    ) -> CompletionResult:
        client = self._openai_client()
        messages: list[dict] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        kwargs: dict = {
            "model": model,
            "messages": messages,
            "max_completion_tokens": max_tokens,
        }
        if temperature is not None and _openai_supports_temperature(model):
            kwargs["temperature"] = temperature
        resp = await client.chat.completions.create(**kwargs)
        choice = resp.choices[0]
        text = (choice.message.content or "").strip()
        usage = getattr(resp, "usage", None)
        in_tok = getattr(usage, "prompt_tokens", 0) if usage else 0
        out_tok = getattr(usage, "completion_tokens", 0) if usage else 0
        return CompletionResult(
            text=text,
            input_tokens=in_tok,
            output_tokens=out_tok,
            model=model,
        )


# Backwards-compatible alias for older call sites.
AnthropicClient = LLMClient
