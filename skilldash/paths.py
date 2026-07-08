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
CONTENT_HASH_FILE = STATE_DIR / "content-hashes.json"
UPSTREAM_HASH_CACHE_FILE = STATE_DIR / "upstream-hash-cache.json"


def _cache_path(target_path):
    """Get cache file path for a target. Resolves ~ and relative paths first."""
    p = Path(target_path).expanduser().resolve()
    safe = re.sub(r'[^\w]', '_', str(p))
    return CACHE_DIR / f"{safe}.json"
