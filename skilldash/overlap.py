"""Cross-directory duplicate and similarity scans."""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

from .classification import _classify_skill
from .decisions import _similar_ignored_keys
from .discovery import _agent_from_path, _discover_skill_dirs
from .paths import CACHE_DIR
from .similarity import compute_signature_similarity


def _compute_signature_similarity(skill_refs):
    return compute_signature_similarity(
        skill_refs,
        ignored_keys=_similar_ignored_keys(),
        classify_skill=_classify_skill,
    )


def _skill_md_hash(skill_dir):
    """Return a short stable hash for one skill's SKILL.md."""
    try:
        return hashlib.sha256((Path(skill_dir) / "SKILL.md").read_bytes()).hexdigest()[:12]
    except Exception:
        return "error"

def _find_same_name_duplicates(dirs):
    """Find same-name skills across the given directory list.
    Returns (duplicates_identical, duplicates_same_name, all_skills_map).
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

def _find_agent_cross_dir_similar(dirs):
    """Per-agent light-signature cross-directory similarity for the given directory list.
    Returns {agent_name: [overlap_groups]}.
    """
    dir_skills = {}  # dir_path -> [skill_names]
    agent_dirs = {}  # agent -> [dir_paths]
    for tdir in dirs:
        dir_path = str(tdir)
        skills_in_dir = []
        try:
            entries = sorted(tdir.iterdir())
        except Exception:
            continue
        for d in entries:
            if not (d.is_dir() or d.is_symlink()):
                continue
            if not (d / "SKILL.md").exists():
                continue
            skills_in_dir.append(d.name)
        if skills_in_dir:
            dir_skills[dir_path] = skills_in_dir
            agent = _agent_from_path(dir_path)
            agent_dirs.setdefault(agent, []).append(dir_path)

    agent_similar = {}
    for agent_name, agent_dir_list in agent_dirs.items():
        if len(agent_dir_list) < 2:
            continue
        agent_refs = []
        seen_names = set()
        for dp in agent_dir_list:
            for skill_name in dir_skills.get(dp, []):
                if skill_name in seen_names:
                    continue
                seen_names.add(skill_name)
                agent_refs.append({"name": skill_name, "dir": dp, "agent": agent_name})
        if 1 < len(agent_refs):
            groups = _compute_signature_similarity(agent_refs)
            if groups:
                agent_similar[agent_name] = groups
    return agent_similar

def detect_cross_dir_overlaps():
    """Detect duplicate and similar skills across ALL directories.
    Uses _find_same_name_duplicates and _find_agent_cross_dir_similar helpers.
    Cached for 5 minutes.
    """
    # Cache (5-min TTL)
    cache_file = CACHE_DIR / "cross-dir-overlaps.json"
    if cache_file.exists():
        try:
            cached = json.loads(cache_file.read_text("utf-8"))
            if cached.get("_schema") == 3 and time.time() - cached.get("_ts", 0) < 300:
                return {k: v for k, v in cached.items() if not k.startswith("_")}
        except Exception:
            pass

    all_dirs = _discover_skill_dirs()

    # Same-name detection
    duplicates_identical, duplicates_same_name = _find_same_name_duplicates(all_dirs)

    # Agent summary
    agent_identical = {}
    for dup in duplicates_identical:
        for loc in dup["locations"]:
            agent_identical.setdefault(loc["agent"], []).append(dup["name"])
    agent_summary_final = []
    for ag, names in sorted(agent_identical.items(), key=lambda x: -len(x[1])):
        agent_summary_final.append({
            "agent": ag,
            "identical_count": len(names),
            "skills_sample": names[:5],
        })

    prunable = sum(d["dir_count"] - 1 for d in duplicates_identical)

    # Cross-directory light-signature similarity
    agent_similar = _find_agent_cross_dir_similar(all_dirs)

    # Count unique skills across all dirs for stats
    unique_names = set()
    total_dirs = 0
    for tdir in all_dirs:
        try:
            has_any = False
            for d in tdir.iterdir():
                if (d.is_dir() or d.is_symlink()) and (d / "SKILL.md").exists():
                    unique_names.add(d.name)
                    has_any = True
            if has_any:
                total_dirs += 1
        except Exception:
            pass

    result = {
        "duplicates_identical": duplicates_identical,
        "duplicates_same_name": duplicates_same_name,
        "agent_summary": agent_summary_final,
        "agent_similar": agent_similar,
        "total_unique_names": len(unique_names),
        "total_dirs_scanned": total_dirs,
        "total_identical": len(duplicates_identical),
        "total_same_name": len(duplicates_same_name),
        "total_prunable": prunable,
        "total_identical_locations": sum(d["dir_count"] for d in duplicates_identical),
    }

    try:
        cache_file.write_text(
            json.dumps({"_schema": 3, "_ts": time.time(), **result}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception:
        pass

    return result
