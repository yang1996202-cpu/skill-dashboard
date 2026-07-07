"""Cross-directory duplicate scans."""

from __future__ import annotations

import hashlib
from pathlib import Path

from .discovery import _agent_from_path


def _skill_md_hash(skill_dir):
    """Return a short stable hash for one skill's SKILL.md."""
    try:
        return hashlib.sha256((Path(skill_dir) / "SKILL.md").read_bytes()).hexdigest()[:12]
    except Exception:
        return "error"

def _find_same_name_duplicates(dirs):
    """Find same-name skills across the given directory list.

    Returns (duplicates_identical, duplicates_same_name).
    """
    all_skills = {}  # name -> [{dir, agent}]
    for tdir in dirs:
        dir_path = str(tdir)
        try:
            entries = sorted(tdir.iterdir())
        except Exception:
            continue
        for d in entries:
            if not (d.is_dir() or d.is_symlink()):
                continue
            if not (d / "SKILL.md").exists():
                continue
            name = d.name
            if name not in all_skills:
                all_skills[name] = []
            all_skills[name].append({
                "dir": dir_path,
                "agent": _agent_from_path(dir_path),
                "is_symlink": d.is_symlink(),  # 本体判定用:软链当副本、实体当本体(删软链不丢数据)
            })

    duplicates_identical = []
    duplicates_same_name = []
    for name, locations in all_skills.items():
        if len(locations) < 2:
            continue
        hashes = {}
        for loc in locations:
            h = _skill_md_hash(Path(loc["dir"]) / name)
            loc["hash"] = h
            hashes.setdefault(h, []).append(loc)

        agents = list(set(loc["agent"] for loc in locations))
        entry = {
            "name": name,
            "locations": locations,
            "agent_count": len(agents),
            "dir_count": len(locations),
            "hash_count": len(hashes),
        }
        if len(hashes) == 1:
            entry["type"] = "identical"
            duplicates_identical.append(entry)
        else:
            entry["type"] = "same_name_diff"
            duplicates_same_name.append(entry)

    duplicates_identical.sort(key=lambda d: d["dir_count"], reverse=True)
    duplicates_same_name.sort(key=lambda d: d["dir_count"], reverse=True)
    return duplicates_identical, duplicates_same_name
