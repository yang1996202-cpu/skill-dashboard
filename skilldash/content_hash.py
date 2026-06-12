"""Directory-aware SKILL.md content hash tracking."""

from __future__ import annotations

import hashlib
import json
import threading
import time
from pathlib import Path

from .paths import CONTENT_HASH_FILE


_hash_lock = threading.Lock()


def _load_content_hashes():
    """Load content hashes from state file."""
    if CONTENT_HASH_FILE.exists():
        try:
            return json.loads(CONTENT_HASH_FILE.read_text("utf-8"))
        except Exception:
            return {}
    return {}

def _save_content_hashes(data):
    """Atomically save content hashes."""
    CONTENT_HASH_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = CONTENT_HASH_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(CONTENT_HASH_FILE)

def _hash_key(skill_path):
    """Build a directory-aware hash key to avoid collisions across agents.

    Walks up from *skill_path* to find the ``skills/`` boundary, then uses
    ``<agent_prefix>/<subpath_within_skills>`` as the key.  Examples:

    - ``~/.claude/skills/foo``      -> ``.claude/foo``
    - ``~/.cursor/skills/foo``      -> ``.cursor/foo``
    - ``~/.claude/skills/mkt/bar``  -> ``.claude/mkt/bar``

    NOTE: Breaking change from old bare-name keys.  Old hashes stored under
    plain names (e.g. ``"foo"``) will never match the new prefixed format,
    so they are effectively superseded on the next scan and age out naturally.
    """
    p = Path(skill_path).resolve()
    home = Path.home()
    try:
        rel = p.relative_to(home)
    except ValueError:
        # Absolute fallback (shouldn't happen in practice)
        return f"{p.parent.name}/{p.name}"
    parts = rel.parts
    # Walk up to find the 'skills' boundary
    for i, part in enumerate(parts):
        if part == "skills" and i + 1 < len(parts):
            agent_prefix = "/".join(parts[:i])
            skill_subpath = "/".join(parts[i + 1 :])
            return f"{agent_prefix}/{skill_subpath}"
    # Fallback: no 'skills' segment found
    return f"{p.parent.name}/{p.name}"

def _hash_prefix_for_target(target_path):
    """Derive the key prefix that ``check_content_changes`` should filter on.

    *target_path* is the skills directory itself (e.g. ``~/.claude/skills``).
    Returns the agent prefix (e.g. ``.claude``).
    """
    t = Path(target_path).resolve()
    home = Path.home()
    try:
        rel = t.relative_to(home)
    except ValueError:
        return t.name
    parts = rel.parts
    # parts[-1] should be 'skills'; prefix is everything before it
    if parts[-1] == "skills" and len(parts) > 1:
        return "/".join(parts[:-1])
    return "/".join(parts)

def record_content_hash(skill_path):
    """Compute SHA256 of SKILL.md and store it. Called during install/copy."""
    skill_md = Path(skill_path) / "SKILL.md"
    if not skill_md.exists():
        return
    try:
        content = skill_md.read_text("utf-8", errors="ignore")
        h = hashlib.sha256(content.encode("utf-8")).hexdigest()
        key = _hash_key(skill_path)
        with _hash_lock:
            hashes = _load_content_hashes()
            hashes[key] = {"hash": h, "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%S")}
            _save_content_hashes(hashes)
    except Exception:
        pass

def check_content_changes(target_path):
    """Compare current SKILL.md hashes with stored hashes.
    Returns dict with changed/deleted lists.

    Only checks keys belonging to the given target directory (matched by
    agent prefix), so cross-agent hashes are never falsely compared.
    """
    target_dir = Path(target_path)
    if not target_dir.is_dir():
        return {"changed": [], "deleted": [], "total_tracked": 0, "total_changed": 0}

    with _hash_lock:
        stored = _load_content_hashes()

    if not stored:
        return {"changed": [], "deleted": [], "total_tracked": 0, "total_changed": 0}

    # Only consider keys that belong to this target directory
    prefix = _hash_prefix_for_target(target_path) + "/"
    relevant = {k: v for k, v in stored.items() if k.startswith(prefix)}

    if not relevant:
        return {"changed": [], "deleted": [], "total_tracked": 0, "total_changed": 0}

    changed = []
    deleted = []
    tracked = 0

    for key, info in relevant.items():
        # key is like ".claude/foo" or ".claude/mkt/bar"
        # subpath after prefix is the relative path under the skills dir
        subpath = key[len(prefix) :]
        skill_md = target_dir / subpath / "SKILL.md"
        if not skill_md.exists():
            deleted.append(subpath)
            continue
        try:
            current = hashlib.sha256(
                skill_md.read_text("utf-8", errors="ignore").encode("utf-8")
            ).hexdigest()
            tracked += 1
            if current != info.get("hash"):
                changed.append({"name": subpath, "last_recorded": info.get("recorded_at", "")})
        except Exception:
            tracked += 1

    return {
        "changed": changed,
        "deleted": deleted,
        "total_tracked": tracked,
        "total_changed": len(changed),
    }
