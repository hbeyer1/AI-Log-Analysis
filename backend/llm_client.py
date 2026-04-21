"""LLM provider wrapper. Anthropic primary, OpenAI optional."""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

from anthropic import AsyncAnthropic


# Per-million-token prices (USD) for cost estimation.
# Update when models/pricing change.
ANTHROPIC_PRICES = {
    "claude-opus-4-7": {"in": 15.0, "out": 75.0},
    "claude-sonnet-4-6": {"in": 3.0, "out": 15.0},
    "claude-haiku-4-5-20251001": {"in": 1.0, "out": 5.0},
}

DEFAULT_MODEL_HEAVY = "claude-sonnet-4-6"
DEFAULT_MODEL_LIGHT = "claude-haiku-4-5-20251001"


@dataclass
class CompletionResult:
    text: str
    input_tokens: int
    output_tokens: int
    model: str

    @property
    def cost_usd(self) -> float:
        return estimate_cost(self.model, self.input_tokens, self.output_tokens)


def estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    prices = ANTHROPIC_PRICES.get(model)
    if not prices:
        return 0.0
    return (input_tokens * prices["in"] + output_tokens * prices["out"]) / 1_000_000


class AnthropicClient:
    def __init__(self, api_key: Optional[str] = None):
        key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not key:
            raise RuntimeError("ANTHROPIC_API_KEY not set")
        self._client = AsyncAnthropic(api_key=key)

    async def complete(
        self,
        prompt: str,
        *,
        model: str = DEFAULT_MODEL_HEAVY,
        max_tokens: int = 1024,
        system: Optional[str] = None,
        temperature: Optional[float] = None,
    ) -> CompletionResult:
        kwargs = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": prompt}],
        }
        if system:
            kwargs["system"] = system
        if temperature is not None:
            kwargs["temperature"] = temperature
        msg = await self._client.messages.create(**kwargs)
        text_parts = [b.text for b in msg.content if getattr(b, "type", None) == "text"]
        return CompletionResult(
            text="".join(text_parts).strip(),
            input_tokens=msg.usage.input_tokens,
            output_tokens=msg.usage.output_tokens,
            model=model,
        )


def rough_token_count(text: str) -> int:
    """Rough heuristic: ~4 chars per token. Good enough for pre-run cost estimates."""
    return max(1, len(text) // 4)
