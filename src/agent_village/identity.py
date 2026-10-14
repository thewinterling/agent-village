"""Deterministic avatar appearance.

Derived purely from the session id, so the same agent looks the same across daemon restarts
without storing a single byte of wardrobe state.
"""

from __future__ import annotations

import hashlib
from typing import Any

BODY_COLOURS = [
    "#e8705a",
    "#f2a65a",
    "#f7d060",
    "#8fc77f",
    "#5fb3a1",
    "#5a9ee8",
    "#8a7ae8",
    "#d070b8",
    "#b98a6a",
    "#9aa5b1",
]

HAIR_COLOURS = ["#2b1d14", "#5a3a22", "#9c6b3f", "#e0b45a", "#e8e4df", "#45506b", "#8c3b3b"]

HAT_SHAPES = ["none", "none", "none", "cap", "beanie", "band", "tuft"]

SKIN_TONES = ["#f6dcc8", "#eec4a2", "#d9a77e", "#b9825a", "#8d5b3c", "#5f3c28"]

HAIR_STYLES = ["short", "buzz", "long", "bun", "ponytail", "curly"]

TROUSER_COLOURS = ["#37405a", "#2b2d33", "#5a4a3a", "#3f5a4a", "#6b6f7a", "#1f3350", "#7a5a48"]

#: Model family -> accessory, so you can see at a glance which model is burning your quota.
MODEL_ACCESSORY = {
    "opus": "crown",
    "sonnet": "cap",
    "haiku": "band",
    "composer": "visor",
    "gpt": "goggles",
    "gemini": "halo",
}

_HASH_FIELDS = 9


def _digest(session_id: str) -> list[int]:
    raw = hashlib.sha256(session_id.encode("utf-8")).digest()
    return [raw[i] for i in range(_HASH_FIELDS)]


def model_accessory(model: str | None) -> str:
    if not model:
        return "none"
    lowered = model.lower()
    for key, accessory in MODEL_ACCESSORY.items():
        if key in lowered:
            return accessory
    return "none"


def appearance(session_id: str, model: str | None = None) -> dict[str, Any]:
    """Stable visual identity for one agent."""
    h = _digest(session_id)
    return {
        "body": BODY_COLOURS[h[0] % len(BODY_COLOURS)],
        "hair": HAIR_COLOURS[h[1] % len(HAIR_COLOURS)],
        "hat": HAT_SHAPES[h[2] % len(HAT_SHAPES)],
        "height": round(0.85 + (h[3] % 30) / 100, 2),
        "girth": round(0.85 + (h[4] % 30) / 100, 2),
        "gaitPhase": round((h[5] % 100) / 100, 2),
        "accessory": model_accessory(model),
        "skin": SKIN_TONES[h[6] % len(SKIN_TONES)],
        "hairStyle": HAIR_STYLES[h[7] % len(HAIR_STYLES)],
        "trousers": TROUSER_COLOURS[h[8] % len(TROUSER_COLOURS)],
    }
