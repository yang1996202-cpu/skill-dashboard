"""Local runtime decisions that should not be committed to Git."""

from __future__ import annotations

import hashlib
import json

from .paths import DUPLICATE_DECISIONS_FILE, STATE_DIR


def _duplicate_decision_key(skill_name, content_hash, decision="multi_agent_deployment"):
    raw = f"{decision}|{skill_name}|{content_hash}"
    return hashlib.sha1(raw.encode("utf-8", errors="ignore")).hexdigest()[:20]

def _load_duplicate_decisions():
    try:
        data = json.loads(DUPLICATE_DECISIONS_FILE.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            data.setdefault("multi_agent_deployment", {})
            return data
    except Exception:
        pass
    return {"schema": 1, "multi_agent_deployment": {}}

def _save_duplicate_decisions(data):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    data["schema"] = 1
    DUPLICATE_DECISIONS_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

def _is_marked_multi_agent_deployment(skill_name, content_hash):
    decisions = _load_duplicate_decisions()
    key = _duplicate_decision_key(skill_name, content_hash)
    return key in decisions.get("multi_agent_deployment", {})
