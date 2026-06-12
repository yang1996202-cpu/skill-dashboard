"""Shared filesystem paths and cache helpers for Skill Dashboard."""

from __future__ import annotations

import json
import re
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent
PORT = 3457
STATE_DIR = BASE_DIR / ".data" / "state"
HTML_FILE = BASE_DIR / "index.html"
STATIC_DIR = BASE_DIR / "static"
CACHE_DIR = BASE_DIR / ".data" / "cache"
DIAG_LOG = CACHE_DIR / "diag.log"
DUPLICATE_DECISIONS_FILE = STATE_DIR / "duplicate-decisions.json"
SIMILAR_DECISIONS_FILE = STATE_DIR / "similar-decisions.json"
CONTENT_HASH_FILE = STATE_DIR / "content-hashes.json"


def _cache_path(target_path):
    """Get cache file path for a target. Resolves ~ and relative paths first."""
    p = Path(target_path).expanduser().resolve()
    safe = re.sub(r'[^\w]', '_', str(p))
    return CACHE_DIR / f"{safe}.json"


def load_cached_diagnosis(target_path):
    """Load cached diagnosis for a target, or None."""
    cp = _cache_path(target_path)
    if cp.exists():
        try:
            return json.loads(cp.read_text("utf-8"))
        except Exception:
            pass
    return None


def save_cached_diagnosis(target_path, data):
    """Save diagnosis result to cache."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cp = _cache_path(target_path)
    cp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
