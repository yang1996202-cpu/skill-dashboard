"""Cleanup governance and executable dry-run planning."""

from __future__ import annotations

import hashlib
import re
import time
from collections import Counter
from pathlib import Path

from .decisions import _is_marked_multi_agent_deployment
from .discovery import (
    _agent_from_path,
    _classify_skill_dir_detail,
    _discover_skill_dirs,
    _sample_skill_names,
)
from .overlap import _find_same_name_duplicates, _skill_md_hash


def _cleanup_plan_item(skills_dir, current_target):
    """Build one dry-run cleanup decision for a discovered skills directory."""
    skills_dir = Path(skills_dir)
    governance = _classify_skill_dir_detail(skills_dir)
    count = sum(1 for d in skills_dir.iterdir()
                if (d.is_dir() or d.is_symlink()) and (d / "SKILL.md").exists())
    agent = _agent_from_path(str(skills_dir))
    is_current = str(skills_dir) == current_target
    category = governance.get("category", "unknown")
    layer = governance.get("layer", "unknown")
    policy = governance.get("policy", "review")

    if is_current:
        group = "protect"
        decision = "保留当前运行目录"
        next_state = "继续作为当前技能库，只做单个 skill 级别整理"
        risk = "high"
        can_execute = False
        reasons = ["当前正在使用的目标目录", *governance.get("evidence", [])]
    elif policy == "manage":
        group = "protect"
        decision = "保护用户技能库"
        next_state = "保留在日常管理；删除必须落到单个 skill 或明确目录"
        risk = "medium"
        can_execute = False
        reasons = ["用户自建或项目级技能库", *governance.get("evidence", [])]
    elif policy == "review":
        group = "review"
        decision = "进入人工复核"
        next_state = "先对比来源、相似度和原文，再决定迁移/删除/保留"
        risk = "medium"
        can_execute = False
        reasons = ["项目级、跨 Agent 副本或未知运行态目录，需先看内容", *governance.get("evidence", [])]
    elif policy == "observe":
        group = "observe"
        decision = "只观察不清理"
        next_state = "作为市场、插件或宿主来源证据保留，不进日常删除队列"
        risk = "low"
        can_execute = False
        reasons = ["marketplace、插件或宿主内置来源目录，只解释来源", *governance.get("evidence", [])]
    else:
        group = "hide"
        decision = "默认隐藏"
        next_state = "从日常视图排除；如需释放空间，应由对应包管理器或宿主工具处理"
        risk = "low"
        can_execute = False
        reasons = ["缓存、测试样例或系统工件", *governance.get("evidence", [])]

    return {
        "path": str(skills_dir),
        "rel": str(skills_dir).replace(str(Path.home()), "~"),
        "agent": agent,
        "count": count,
        "sample_skills": _sample_skill_names(skills_dir),
        "is_current": is_current,
        "category": category,
        "layer": layer,
        "layer_label": governance.get("layer_label", layer),
        "policy": policy,
        "policy_label": governance.get("policy_label", policy),
        "classification_confidence": governance.get("confidence", "medium"),
        "group": group,
        "decision": decision,
        "next_state": next_state,
        "risk": risk,
        "can_execute": can_execute,
        "reasons": list(dict.fromkeys([r for r in reasons if r]))[:5],
    }

def build_cleanup_plan(current_target, scope="daily", restrict_dirs=None):
    """Build a conservative dry-run cleanup plan.

    The plan is deliberately non-destructive. It explains directory state and
    recommended next state, but it does not declare anything directly deletable.

    restrict_dirs: 可选的目录路径集合(字符串)。命中时只看这些目录(再做 daily/deep
    过滤),让"问题与整理"治理 tab 尊重用户选的扫描范围,不再全量 daily。
    为空/None 时维持原行为(全量 _discover_skill_dirs)。
    """
    started = time.time()
    if restrict_dirs:
        restrict_norm = {str(Path(d).expanduser().resolve()) for d in restrict_dirs if d}
        all_dirs = [
            d for d in _discover_skill_dirs()
            if str(Path(d).expanduser().resolve()) in restrict_norm
        ]
    else:
        all_dirs = _discover_skill_dirs()
    items = []
    for skills_dir in all_dirs:
        try:
            item = _cleanup_plan_item(skills_dir, current_target)
        except Exception:
            continue
        if item["count"] <= 0:
            continue
        if scope == "daily" and item["group"] in ("observe", "hide"):
            continue
        items.append(item)

    group_meta = {
        "protect": {
            "label": "保护区",
            "intent": "当前目录和用户技能库。可以整理单个 skill，但不建议做目录级清理。",
        },
        "review": {
            "label": "复核区",
            "intent": "导入副本、项目目录、App 本地库和备份。这里是清理潜力区，但必须先看证据。",
        },
        "observe": {
            "label": "观察区",
            "intent": "marketplace、插件来源和宿主内置包。它们解释“从哪里来”，默认不删除。",
        },
        "hide": {
            "label": "隐藏区",
            "intent": "包管理缓存、测试样例和系统缓存。默认不进日常管理，也不由本工具直接删除。",
        },
    }
    groups = []
    for key in ("protect", "review", "observe", "hide"):
        group_items = [item for item in items if item["group"] == key]
        if not group_items:
            continue
        groups.append({
            "key": key,
            **group_meta[key],
            "directory_count": len(group_items),
            "skill_count": sum(item["count"] for item in group_items),
            "items": group_items,
        })

    policy_counts = Counter(item["policy"] for item in items)
    group_counts = Counter(item["group"] for item in items)
    summary = {
        "directories": len(items),
        "skills": sum(item["count"] for item in items),
        "protect": group_counts.get("protect", 0),
        "review": group_counts.get("review", 0),
        "observe": group_counts.get("observe", 0),
        "hide": group_counts.get("hide", 0),
        "review_skills": sum(item["count"] for item in items if item["group"] == "review"),
        "direct_delete": 0,
        "policies": dict(policy_counts),
    }
    rules = [
        {
            "name": "先保护运行目录",
            "text": "当前目录和用户技能库只能做 skill 级整理，不做目录级一键删除。",
        },
        {
            "name": "复核区先看证据",
            "text": "导入副本、项目技能、App 本地库和备份只进入复核，不默认删除。",
        },
        {
            "name": "市场/内置包只观察",
            "text": "marketplace、插件包和宿主内置 skill 解释来源，不作为用户垃圾处理。",
        },
        {
            "name": "缓存默认隐藏",
            "text": "包管理缓存和测试样例不进入日常视图；清空间应交给对应包管理器或宿主工具。",
        },
    ]
    return {
        "schema": 1,
        "mode": "dry-run",
        "scope": scope,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "duration_ms": int((time.time() - started) * 1000),
        "current_target": current_target,
        "summary": summary,
        "rules": rules,
        "groups": groups,
    }

def _execution_action_for_item(item, strategy="conservative"):
    """Convert one cleanup-plan item into a concrete, still non-running action."""
    layer = item.get("layer", "")
    group = item.get("group", "")
    operation_seed = f"{item.get('path', '')}|{strategy}|{layer}|{group}"
    base = {
        "id": hashlib.sha1(operation_seed.encode("utf-8", errors="ignore")).hexdigest()[:16],
        "path": item.get("path", ""),
        "rel": item.get("rel", ""),
        "agent": item.get("agent", ""),
        "count": item.get("count", 0),
        "sample_skills": item.get("sample_skills", []),
        "from_state": item.get("layer_label") or layer,
        "layer": layer,
        "policy": item.get("policy", ""),
        "policy_label": item.get("policy_label", ""),
        "layer_label": item.get("layer_label", "") or layer,
        "evidence": item.get("reasons", []),
        "risk": item.get("risk", "medium"),
    }

    if group == "protect":
        return {
            **base,
            "phase": "protect",
            "operation": "lock_keep",
            "label": "锁定保留",
            "to_state": "日常管理",
            "why": "这是当前运行目录或用户技能库，目录级清理风险过高。",
            "rollback": "无文件变更；只是保护规则。",
            "ready": True,
            "destructive": False,
            "requires_confirmation": False,
        }

    if group == "observe":
        return {
            **base,
            "phase": "organize",
            "operation": "keep_observed",
            "label": "收纳到观察区",
            "to_state": "全量审计可见，日常视图隐藏",
            "why": "这是市场、插件或宿主来源证据，适合解释来源，不适合当用户垃圾删除。",
            "rollback": "切换到全量审计即可继续查看。",
            "ready": True,
            "destructive": False,
            "requires_confirmation": False,
        }

    if strategy == "declutter" and layer in ("fixture-example",):
        return {
            **base,
            "phase": "candidate",
            "operation": "move_skills_to_trash",
            "label": "示例包移入垃圾站",
            "to_state": "垃圾站/确认后再清空",
            "why": "这是示例或测试用 skill 包，不像当前宿主正在使用的技能库；适合在全量线索里集中处理。",
            "rollback": "先移入本工具垃圾站，确认无误后再清空。",
            "ready": False,
            "destructive": True,
            "requires_confirmation": True,
        }

    if group == "hide":
        return {
            **base,
            "phase": "organize",
            "operation": "hide_cache",
            "label": "收纳到隐藏区",
            "to_state": "默认不进入日常管理",
            "why": "这是缓存、测试样例或系统工件；本工具不直接接管它的生命周期。",
            "rollback": "切换到全量审计即可继续查看。",
            "ready": True,
            "destructive": False,
            "requires_confirmation": False,
        }

    if strategy == "declutter" and layer in ("backup-snapshot", "imported-copy", "downloaded-package"):
        # 目录路径特征 → 大白话分类
        layer_cn = {"backup-snapshot": "备份快照", "imported-copy": "导入副本", "downloaded-package": "下载包"}.get(layer, "副本目录")
        return {
            **base,
            "phase": "candidate",
            "operation": "move_skills_to_trash",
            "label": "候选移入垃圾站",
            "to_state": "垃圾站/快照后再物理删除",
            "why": f"这个目录像是{layer_cn}（{item.get('rel') or item.get('path', '')}），里面的 skill 大多在别处有副本或不再用。可逐个确认移入垃圾站，能恢复。",
            "rollback": "先移入本工具垃圾站或保留快照，确认无误后再清空。",
            "ready": False,
            "destructive": True,
            "requires_confirmation": True,
        }

    return {
        **base,
        "phase": "review",
        "operation": "manual_review",
        "label": "进入人工复核",
        "to_state": "待定：保留、迁移、合并或移入垃圾站",
        "why": "这个目录看起来有清理潜力，但工具没法判断它现在还用不用——可能是项目级、跨 Agent 副本或未知运行态。建议先点开看看里面的 skill，再决定保留、移走还是删。",
        "rollback": "无文件变更；复核后再生成具体删除动作。",
        "ready": True,
        "destructive": False,
        "requires_confirmation": False,
    }

def _duplicate_keeper_sort_key(loc, current_target):
    """Prefer the copy most likely to be actively used."""
    path = Path(loc.get("dir", "")).expanduser()
    try:
        resolved = path.resolve()
        current = Path(current_target).expanduser().resolve()
    except Exception:
        resolved = path
        current = Path(current_target)
    governance = _classify_skill_dir_detail(path)
    layer = governance.get("layer", "")
    policy = governance.get("policy", "")
    if resolved == current:
        priority = 0
    elif layer == "active-root":
        priority = 1
    elif policy == "manage":
        priority = 2
    elif layer == "project-local":
        priority = 3
    elif layer in ("app-local-library", "plugin-marketplace", "vendor-bundled"):
        priority = 4
    elif policy == "review":
        priority = 5
    else:
        priority = 6
    return (priority, str(path).lower())

def _duplicate_action_kind(skills_dir, skill_name, current_target, duplicate_of="", expected_hash=""):
    """Classify an exact duplicate as trash candidate, multi-agent deployment, or blocked."""
    try:
        if not re.match(r'^[a-zA-Z0-9._@+\-]+$', skill_name or ""):
            return "blocked", "invalid skill name", {}
        path = Path(skills_dir).expanduser().resolve()
        home = Path.home().resolve()
        if not path.is_relative_to(home):
            return "blocked", "path outside home", {}
        if path == Path(current_target).expanduser().resolve():
            return "blocked", "current target is protected", {}
        skill_dir = path / skill_name
        if not (skill_dir.is_dir() and (skill_dir / "SKILL.md").exists()):
            return "blocked", "skill not found", {}

        governance = _classify_skill_dir_detail(path)
        policy = governance.get("policy")
        layer = governance.get("layer")
        duplicate_of_path = Path(duplicate_of).expanduser().resolve() if duplicate_of else None
        current_path = Path(current_target).expanduser().resolve()

        actual_hash = _skill_md_hash(skill_dir)
        if expected_hash and actual_hash != expected_hash:
            return "blocked", "content hash changed", governance
        if duplicate_of:
            kept_dir = duplicate_of_path / skill_name
            if not (kept_dir.is_dir() and (kept_dir / "SKILL.md").exists()):
                return "blocked", "kept duplicate copy is missing", governance
            if _skill_md_hash(kept_dir) != actual_hash:
                return "blocked", "kept copy is no longer identical", governance

        if policy == "review" and layer in ("backup-snapshot", "imported-copy", "downloaded-package", "app-local-library"):
            return "trash", "review-layer exact duplicate", governance
        if policy == "manage" and layer in ("active-root", "user-installed") and duplicate_of_path == current_path:
            return "multi_agent", "same skill deployed into another active agent root", governance
        return "blocked", f"policy/layer {policy}/{layer} is not single-skill cleanup candidate", governance
    except Exception as e:
        return "blocked", str(e), {}

def _duplicate_skill_execute_allowed(skills_dir, skill_name, current_target, duplicate_of="", expected_hash=""):
    """Return (allowed, reason) for moving one exact duplicate skill to trash."""
    kind, reason, _ = _duplicate_action_kind(skills_dir, skill_name, current_target, duplicate_of, expected_hash)
    if kind != "trash":
        return False, reason
    return True, "ok"

def _build_exact_duplicate_skill_actions(dirs, current_target, excluded_dirs=None):
    """Build trash candidates for exact duplicate single skills.

    Directory-level candidates remain dominant. This only adds single-skill
    actions for review-layer libraries that are not already being moved as a
    whole directory.
    """
    excluded_dirs = {str(Path(d).expanduser().resolve()) for d in (excluded_dirs or [])}
    duplicates_identical, _ = _find_same_name_duplicates(dirs)
    actions = []
    seen = set()
    for dup in duplicates_identical:
        name = dup.get("name", "")
        locations = dup.get("locations", [])
        if len(locations) < 2:
            continue
        keeper = sorted(locations, key=lambda loc: _duplicate_keeper_sort_key(loc, current_target))[0]
        keeper_dir = keeper.get("dir", "")
        for loc in locations:
            loc_dir = loc.get("dir", "")
            if loc_dir == keeper_dir:
                continue
            try:
                loc_resolved = str(Path(loc_dir).expanduser().resolve())
            except Exception:
                loc_resolved = loc_dir
            if loc_resolved in excluded_dirs:
                continue
            kind, reason, governance = _duplicate_action_kind(
                loc_dir,
                name,
                current_target,
                duplicate_of=keeper_dir,
                expected_hash=loc.get("hash", ""),
            )
            if kind == "blocked":
                continue
            key = (loc_resolved, name)
            if key in seen:
                continue
            seen.add(key)
            seed = f"{loc_resolved}|{name}|exact-duplicate|{keeper_dir}|{loc.get('hash', '')}"
            if kind == "multi_agent":
                content_hash = loc.get("hash", "")
                if _is_marked_multi_agent_deployment(name, content_hash):
                    continue
                actions.append({
                    "id": hashlib.sha1(seed.encode("utf-8", errors="ignore")).hexdigest()[:16],
                    "path": loc_resolved,
                    "rel": str(Path(loc_resolved)).replace(str(Path.home()), "~"),
                    "agent": loc.get("agent") or _agent_from_path(loc_resolved),
                    "count": 1,
                    "skill_name": name,
                    "duplicate_of": keeper_dir,
                    "content_hash": content_hash,
                    "sample_skills": [name],
                    "from_state": governance.get("layer_label") or governance.get("layer", ""),
                    "layer": governance.get("layer", ""),
                    "policy": governance.get("policy", ""),
                    "policy_label": governance.get("policy_label", ""),
                    "layer_label": governance.get("layer_label", "") or governance.get("layer", ""),
                    "evidence": list({e["text"]: e for e in [
                        {"type": "dup", "text": f"{name} 的 SKILL.md 内容跟当前目录里那份完全一致（hash 匹配）"},
                        {"type": "keeper", "text": f"保留的副本在：{str(Path(keeper_dir).expanduser()).replace(str(Path.home()), '~') if keeper_dir else '当前目录'}"},
                        {"type": "policy", "text": "它在另一个 Agent 的根目录，更像多端部署副本，不是重复垃圾"},
                    ]}.values())[:5],
                    "risk": "low",
                    "phase": "deploy",
                    "operation": "mark_multi_agent_deploy",
                    "label": "多端部署副本",
                    "to_state": "保留在对应 Agent 根目录",
                    "why": f"{name} 的内容跟当前目录那份完全一样，但它装在另一个 Agent 的根目录里——更像是为了在多个 Agent 里都能用而部署的副本，不是垃圾。可以标记为「多端部署」，标记后同一内容的提醒不再出现。",
                    "rollback": "无文件变更；标记后仅隐藏同一内容 hash 的重复提醒。",
                    "ready": True,
                    "destructive": False,
                    "requires_confirmation": False,
                })
                continue

            actions.append({
                "id": hashlib.sha1(seed.encode("utf-8", errors="ignore")).hexdigest()[:16],
                "path": loc_resolved,
                "rel": str(Path(loc_resolved)).replace(str(Path.home()), "~"),
                "agent": loc.get("agent") or _agent_from_path(loc_resolved),
                "count": 1,
                "skill_name": name,
                "duplicate_of": keeper_dir,
                "content_hash": loc.get("hash", ""),
                "sample_skills": [name],
                "from_state": governance.get("layer_label") or governance.get("layer", ""),
                "evidence": list({e["text"]: e for e in [
                    {"type": "dup", "text": f"{name} 的 SKILL.md 内容跟另一份完全一致（hash 匹配）"},
                    {"type": "keeper", "text": f"保留的副本在：{str(Path(keeper_dir).expanduser()).replace(str(Path.home()), '~') if keeper_dir else '另一目录'}"},
                ]}.values())[:5],
                "risk": "medium",
                "phase": "candidate",
                "operation": "move_skill_to_trash",
                "label": "完全重复移入垃圾站",
                "to_state": "垃圾站/保留另一份完全相同副本",
                "why": f"{name} 的内容跟另一份完全一样。保留你更可能在用的那份（{str(Path(keeper_dir).expanduser()).replace(str(Path.home()), '~') if keeper_dir else '另一目录'}），这个重复副本可以删——只移入垃圾站，需要时能恢复。",
                "rollback": "从垃圾站恢复到原路径即可。",
                "ready": False,
                "destructive": True,
                "requires_confirmation": True,
            })
    actions.sort(key=lambda a: (a.get("agent", ""), a.get("skill_name", ""), a.get("path", "")))
    return actions

def build_cleanup_execution_plan(current_target, scope="daily", strategy="conservative", restrict_dirs=None):
    """Build an executable-shaped plan without executing filesystem changes.

    restrict_dirs 透传给 build_cleanup_plan,限定目录范围。
    """
    cleanup_plan = build_cleanup_plan(current_target, scope, restrict_dirs=restrict_dirs)
    actions = []
    plan_dirs = []
    for group in cleanup_plan.get("groups", []):
        for item in group.get("items", []):
            plan_dirs.append(Path(item.get("path", "")))
            actions.append(_execution_action_for_item(item, strategy))

    if strategy == "declutter":
        directory_candidate_paths = {
            a.get("path", "")
            for a in actions
            if a.get("operation") == "move_skills_to_trash"
        }
        actions.extend(_build_exact_duplicate_skill_actions(
            plan_dirs,
            current_target,
            excluded_dirs=directory_candidate_paths,
        ))

    phase_meta = {
        "protect": {
            "label": "先锁定",
            "intent": "明确哪些目录永远不进入目录级删除。",
        },
        "review": {
            "label": "再复核",
            "intent": "把不确定目录变成可人工处理的核查任务。",
        },
        "organize": {
            "label": "再收纳",
            "intent": "把市场、内置包、缓存从日常管理里移开，但保留证据。",
        },
        "deploy": {
            "label": "多端部署",
            "intent": "同一个 skill 被放进多个 Agent 根目录。默认保留，可标记后不再重复提醒。",
        },
        "candidate": {
            "label": "推荐移入垃圾站",
            "intent": "备份、导入、下载或 App 本地库中的重复副本。只进垃圾站，不永久删除。",
        },
    }
    phases = []
    for key in ("protect", "review", "organize", "deploy", "candidate"):
        phase_actions = [a for a in actions if a["phase"] == key]
        if not phase_actions:
            continue
        phases.append({
            "key": key,
            **phase_meta[key],
            "action_count": len(phase_actions),
            "skill_count": sum(a.get("count", 0) for a in phase_actions),
            "actions": phase_actions,
        })

    summary = {
        "actions": len(actions),
        "ready": sum(1 for a in actions if a.get("ready")),
        "needs_confirmation": sum(1 for a in actions if a.get("requires_confirmation")),
        "destructive": sum(1 for a in actions if a.get("destructive")),
        "skills_touched": sum(a.get("count", 0) for a in actions),
        "filesystem_changes_now": 0,
    }
    return {
        "schema": 1,
        "mode": "execution-preview",
        "scope": scope,
        "strategy": strategy,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "current_target": current_target,
        "summary": summary,
        "rules": [
            "当前接口只生成动作预案，不执行文件删除。",
            "断舍离策略会推荐备份、导入、下载目录，以及复核层中 SKILL.md 完全一致的重复 skill。",
            "其他 Agent 根目录里的完全重复 skill 视为多端部署副本，默认保留，不进垃圾站候选。",
            "标记为多端部署后，同一 skill + content hash 不再重复提醒；内容变更后会重新出现。",
            "相似度线索只进入复核，不作为自动删除依据。",
            "真正执行时必须先移入垃圾站或创建快照，不能直接物理删除。",
        ],
        "phases": phases,
    }

def _is_cleanup_execute_allowed(skills_dir):
    """Return (allowed, reason) for a concrete cleanup execution path."""
    try:
        path = Path(skills_dir).expanduser().resolve()
        if not path.is_relative_to(Path.home().resolve()):
            return False, "path outside home"
        governance = _classify_skill_dir_detail(path)
        layer = governance.get("layer")
        if layer == "fixture-example":
            return True, "ok"
        if governance.get("policy") != "review":
            return False, f"policy is {governance.get('policy')}, not review"
        if layer not in ("backup-snapshot", "imported-copy", "downloaded-package"):
            return False, f"layer {layer} is not executable candidate"
        return True, "ok"
    except Exception as e:
        return False, str(e)
