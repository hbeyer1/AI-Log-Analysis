"""Load and save prompt text files in backend/prompts/."""
from __future__ import annotations

from pathlib import Path


PROMPTS_DIR = Path(__file__).parent / "prompts"


def load_prompt(name: str) -> str:
    return (PROMPTS_DIR / name).read_text()


def save_prompt(name: str, content: str) -> None:
    (PROMPTS_DIR / name).write_text(content)
