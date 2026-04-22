"""JSON extraction helpers shared across pipeline stages."""
from __future__ import annotations

import json
import re
from typing import Any, Optional


def strip_fences(text: str) -> str:
    s = text.strip()
    m = re.match(r"^```(?:json)?\s*(.*?)\s*```\s*$", s, re.DOTALL)
    return m.group(1).strip() if m else s


def extract_json_object(text: str) -> Optional[dict[str, Any]]:
    cleaned = strip_fences(text)
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        parsed = json.loads(cleaned[start:end + 1])
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def extract_json_array(text: str) -> Optional[list[Any]]:
    cleaned = strip_fences(text)
    start = cleaned.find("[")
    end = cleaned.rfind("]")
    if start < 0 or end <= start:
        return None
    try:
        parsed = json.loads(cleaned[start:end + 1])
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, list) else None
