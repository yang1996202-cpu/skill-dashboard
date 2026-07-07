"""scan 域路由 handler:fast-scan/二哥扫描/全域统计/理解。

从 serve.py 拆出的 mixin。业务依赖 understanding/discovery/source_ops/content_hash/overlap,
顶层 import 无循环。
"""
from __future__ import annotations

import json
import os
import shutil
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlparse, parse_qs

from skilldash.content_hash import check_content_changes
from skilldash.discovery import (
    _agent_from_path,
    _classify_skill_dir_detail,
    _discover_skill_dirs,
    _is_skill_entry,
    _scan_commands,
    _scan_global_categories,
    _skill_entry_kind,
)
from skilldash.overlap import _find_same_name_duplicates
from skilldash.paths import CACHE_DIR
from skilldash.source_ops import (
    GITHUB_TOKEN,
    check_upstream_status,
    clear_upstream_hash_cache,
    detect_source_local,
    get_github_rate_limit,
)
from skilldash.understanding import compact_understanding, understand_skill


class ScanRoutes:

    def _clear_upstream_cache(self):
        """POST /api/upstream-cache/clear — 强制清空 upstream hash 缓存,
        下次「开始上游检测」对每个 skill 都走真实 GitHub API(不受 24h 短路)。
        """
        clear_upstream_hash_cache()
        self._json_response({"ok": True, "msg": "upstream hash 缓存已清空"})

    def _global_stats(self):
        self._json_response(_scan_global_categories())

    def _scan_result(self):
        self._serve_json(CACHE_DIR / "scan-result.json")

    def _serve_understanding(self):
        """Return cached rule-based understanding for one skill.

        Query:
          /api/understand?name=<skill>
          /api/understand?dir=<skills-dir>&name=<skill>
        """
        qs = parse_qs(urlparse(self.path).query)
        raw_name = qs.get("name", [""])[0]
        name = self._validate_skill_name(raw_name)
        if not name:
            self._json_response({"error": "invalid skill name"}, status=400)
            return

        base = qs.get("dir", [""])[0] or self._current_target()
        try:
            base_dir = Path(base).expanduser().resolve()
            home = Path.home().resolve()
            if not base_dir.is_relative_to(home):
                self._json_response({"error": "dir must be under home directory"}, status=403)
                return
            skill_dir = base_dir / name
            if not skill_dir.is_dir() or not (skill_dir / "SKILL.md").exists():
                self._json_response({"error": "skill not found"}, status=404)
                return
            data = understand_skill(skill_dir, CACHE_DIR)
            data["dir"] = str(base_dir)
            data["agent"] = _agent_from_path(str(base_dir))
            data["directory"] = _classify_skill_dir_detail(base_dir)
            self._json_response(data)
        except Exception as e:
            self._json_response({"error": str(e)}, status=500)

    def _fast_scan(self):
        """Direct Python directory scan — milliseconds instead of bash subprocess."""
        # ?path= 临时扫指定目录(不切 target,便于快速预览项目级 skill);
        # 没传则用 current_target(原行为)。
        query = parse_qs(urlparse(self.path).query)
        path_arg = query.get("path", [""])[0]
        if path_arg:
            home_str = str(Path.home())
            path_arg = path_arg.replace("${HOME}", home_str).replace("$HOME", home_str)
            if path_arg.startswith("~"):
                path_arg = str(Path.home() / path_arg[2:])
            try:
                target_dir = Path(path_arg).resolve()
            except Exception:
                self._json_response({"error": f"invalid path: {path_arg}"}, status=400)
                return
            if not target_dir.is_relative_to(Path.home()):
                self._json_response({"error": "path must be under home directory"}, status=403)
                return
        else:
            target = self._current_target()
            target_dir = Path(target)
        if not target_dir.is_dir():
            self._json_response({"error": f"not a dir: {target_dir}"}, status=400)
            return

        start = time.time()
        skills = []
        for d in sorted(target_dir.iterdir()):
            if not _is_skill_entry(d, include_broken=True):
                continue
            skill_md = d / "SKILL.md"
            name = d.name
            description = ""
            category = ""
            kind = _skill_entry_kind(d)
            # Quick frontmatter parse
            if skill_md.exists():
                try:
                    text = skill_md.read_text("utf-8", errors="ignore")[:2000]
                    if text.startswith("---"):
                        end = text.find("---", 3)
                        if end > 0:
                            fm = text[3:end]
                            fm_lines = fm.splitlines()
                            for i, line in enumerate(fm_lines):
                                stripped = line.strip()
                                if stripped.startswith("description:"):
                                    val = stripped.split(":", 1)[1].strip()
                                    # Strip YAML multiline indicators (>, |, >-, |-, >+, |+)
                                    if val in (">", "|", ">-", "|-", ">+", "|+"):
                                        # Collect indented continuation lines
                                        parts = []
                                        for cont in fm_lines[i + 1:]:
                                            if cont and not cont[0].isspace():
                                                break
                                            parts.append(cont.strip())
                                        description = " ".join(parts)
                                    elif val.startswith('"') and val.endswith('"'):
                                        description = val[1:-1]
                                    elif val.startswith("'") and val.endswith("'"):
                                        description = val[1:-1]
                                    else:
                                        description = val.strip("'\"")
                                elif stripped.startswith("category:"):
                                    category = stripped.split(":", 1)[1].strip().strip("'\"")
                except Exception:
                    pass
            understanding = None
            if skill_md.exists():
                try:
                    understanding = compact_understanding(understand_skill(d, CACHE_DIR))
                except Exception:
                    understanding = None
            skills.append({
                "name": name,
                "description": description,
                "category": category,
                "kind": kind,
                "agent": "",
                "understanding": understanding,
            })

        # Build scan-like response
        home = Path.home()
        rel = str(target_dir).replace(str(home), "~")

        # Discover commands for the current target's agent
        commands = []
        try:
            agent_root = target_dir.parent
            if agent_root.name == ".claude":
                commands_dir = agent_root / "commands"
                if commands_dir.is_dir():
                    commands = _scan_commands([commands_dir])
        except Exception:
            pass

        broken = [s for s in skills if s["kind"] in ("broken_symlink", "broken_skill_link")]
        structure_issues = [
            {"name": s["name"], "kind": s["kind"], "dir": str(target_dir / s["name"])}
            for s in broken
        ]

        result = {
            "target": {
                "path": rel,
                "label": target_dir.parent.name,
                "total": len(skills),
                "entities": len([s for s in skills if s["kind"] == "entity"]),
                "symlinks": len([s for s in skills if s["kind"] == "symlink"]),
                "broken_symlinks": len(broken),
            },
            "installed": skills,
            "commands": commands,
            "structure_issues": structure_issues,
            "totals": {"skills": len(skills), "commands": len(commands)},
            "sources": [],
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "scan_mode": "fast",
            "duration_ms": int((time.time() - start) * 1000),
        }
        self._json_response(result)

    def _run_scan(self):
        """Run full scan across all discovered skill directories."""
        body = self._read_json() or {}

        directories = body.get("directories", [])
        requested_scope = body.get("scope") or ("deep" if not directories else "custom")
        home = Path.home()
        if not directories:
            # 不 fallback 全量:前端 targets 没加载完时会误传空 directories,fallback 会烧 GitHub API 全量扫描 + 污染共享缓存。
            # 前端 runScan 已在 directories 空时不调 scan-run;此处防御性返回 400。
            self._json_response({"error": "未指定扫描目录(targets 可能还在加载),请稍候再点"}, status=400)
            return

        # Default checks 不含 upstream:upstream 烧 GitHub API(未认证 60 次/小时,
        # 全量扫描会打爆),用户在二哥扫描面板主动勾"上游"才查。same-name/content-changes 纯本地 0 API。
        checks = body.get("checks", ["same-name", "content-changes"])

        # Validate directories
        valid_dirs = []
        for d in directories:
            p = Path(d).expanduser().resolve()
            if p.is_dir() and p.is_relative_to(home):
                valid_dirs.append(p)
        if not valid_dirs:
            self._json_response({"error": "没有有效的 skill 目录"}, status=400)
            return

        t0 = time.time()
        result = {
            "upstream_sources": [],
            "duplicates_same_name": [],
            "duplicates_identical": [],
            "content_changes": None,
            "source_status": [],
            "scanned_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "scanned_dirs": len(valid_dirs),
            "scope": requested_scope,
            "scan_schema_version": 4,
            "checks": checks,
            "github_token_configured": bool(GITHUB_TOKEN),
            "scanned_policy_counts": dict(Counter(
                _classify_skill_dir_detail(d).get("policy", "review") for d in valid_dirs
            )),
        }

        # 收集每个目录的 skill 名单。上游检测要跨目录全并发——串行打 GitHub API
        # 时 146 目录累计 70s+，浏览器 fetch 挂太久会 Failed to fetch。
        dir_skill_map = {}
        structure_issues = []  # 断链(broken_symlink/broken_skill_link),前端「🔴 损坏」tab 消费
        for tdir in valid_dirs:
            names = []
            try:
                for d in sorted(tdir.iterdir()):
                    if not _is_skill_entry(d, include_broken=True):
                        continue
                    kind = _skill_entry_kind(d)
                    if kind in ("broken_symlink", "broken_skill_link"):
                        structure_issues.append({"name": d.name, "kind": kind, "dir": str(tdir / d.name)})
                        continue  # 断链读不到 SKILL.md，不进 dir_skill_map(上游/同名检测无意义)
                    if (d / "SKILL.md").exists():
                        names.append(d.name)
            except Exception:
                continue
            if names:
                dir_skill_map[tdir] = names
        result["structure_issues"] = structure_issues

        # 扫前计费提示:upstream 检测会打 GitHub API,给用户知情。
        # estimate = 将查 upstream 的 skill 总数(仅当用户勾了 upstream);
        # 实际 API 消耗受 content_hash 短路(24h 缓存)和 5 分钟 _github_cache 进一步压低,
        # 这里给的是上界估计。
        result["upstream_api_estimate"] = (
            sum(len(names) for names in dir_skill_map.values())
            if "upstream" in checks else 0
        )
        result["github_rate_limit"] = get_github_rate_limit()

        # 纯本地来源检测(0 GitHub API):为 recover(待补来源)功能提供数据。
        # detect_source_local 只读本地三信号(.skill-source.env / .git remote /
        # .skill-lock.json),不判版本。**跟扫描范围**(dir_skill_map 来自用户"开始整理"
        # 勾选的目录)——与同名/上游/变更 tab 同源一致:用户选"当前目录"就只看当前目录
        # 的缺来源 skill,选"全部"才全量。只收 category=user/project(用户自管根 + 项目级),
        # 排除货架/缓存/vendor/builtin(本就没单个来源留痕,也不该让用户补)。
        user_project_dirs = {
            tdir for tdir in dir_skill_map
            if _classify_skill_dir_detail(tdir).get("category") in ("user", "project")
        }
        for tdir, names in dir_skill_map.items():
            if tdir not in user_project_dirs:
                continue
            for name in names:
                try:
                    info = detect_source_local(tdir / name)
                except Exception:
                    info = {"source": "unknown", "repo": "", "ref": "", "subdir": ""}
                result["source_status"].append({
                    "name": name,
                    "dir": str(tdir),
                    "source": info.get("source", "unknown"),
                    "repo": info.get("repo", ""),
                })

        # Content changes（本地读文件，快，保持串行）
        if "content-changes" in checks:
            for tdir in dir_skill_map:
                try:
                    changes = check_content_changes(str(tdir))
                    if changes and changes.get("total_changed", 0) > 0:
                        if result["content_changes"] is None:
                            result["content_changes"] = {"changed": [], "deleted": [], "total_tracked": 0, "total_changed": 0}
                        result["content_changes"]["changed"].extend(changes.get("changed", []))
                        result["content_changes"]["deleted"].extend(changes.get("deleted", []))
                        result["content_changes"]["total_tracked"] += changes.get("total_tracked", 0)
                        result["content_changes"]["total_changed"] += changes.get("total_changed", 0)
                except Exception:
                    pass

        # Upstream tracking（GitHub API，慢，跨目录全并发；max_workers=10 把 70s+ 压到 ~10s）
        if "upstream" in checks:
            all_tasks = [(tdir, name) for tdir, names in dir_skill_map.items() for name in names]
            def _check_upstream(task):
                tdir, name = task
                try:
                    return (tdir, name, check_upstream_status(tdir / name))
                except Exception:
                    return (tdir, name, None)
            with ThreadPoolExecutor(max_workers=10) as _upool:
                upstream_results = list(_upool.map(_check_upstream, all_tasks))
            for tdir, name, status in upstream_results:
                if not status:
                    continue
                # 允许有 repo 的 unknown 也进入：本地检测到 .git remote / lock 来源，
                # 但 GitHub API 限流或未配 token 时无法判定版本（status=unknown）。
                # 仍展示来源，让未配 token 的用户看到"哪些 skill 可追踪"。
                if status.get("repo") and status.get("status") in ("current", "outdated", "unknown"):
                    result["upstream_sources"].append({
                        "name": name,
                        "repo": status.get("repo", ""),
                        "status": status["status"],
                        "installed_commit": status.get("installed_commit", ""),
                        "latest_commit": status.get("latest_commit", ""),
                        "dir": str(tdir),
                        "source": status.get("source", "unknown"),
                        "is_symlink": status.get("is_symlink", False),
                        "link_target": status.get("link_target", ""),
                        "canonical_dir": status.get("canonical_dir", str(tdir)),
                    })

        # Cross-directory checks (need 2+ dirs)
        if len(valid_dirs) >= 2:
            if "same-name" in checks:
                dup_id, dup_sn = _find_same_name_duplicates(valid_dirs)
                result["duplicates_identical"] = dup_id
                result["duplicates_same_name"] = dup_sn

        result["duration_ms"] = int((time.time() - t0) * 1000)

        # Cache result
        try:
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            (CACHE_DIR / "scan-result.json").write_text(
                json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception:
            pass

        # Lint: verify result consistency
        result["lint"] = self._lint_scan_result(result)

        self._json_response(result)

    def _lint_scan_result(self, result):
        """Check scan result for logical inconsistencies. Returns list of warnings."""
        warnings = []

        # 1. Same-name: every group must have 2+ locations
        sn = result.get("duplicates_same_name", [])
        for dup in sn:
            if len(dup.get("locations", [])) < 2:
                warnings.append(f"same-name group '{dup.get('name','?')}' has {len(dup.get('locations',[]))} locations (need 2+)")

        # 2. Upstream: each must have name and dir
        for s in result.get("upstream_sources", []):
            if not s.get("name"):
                warnings.append(f"upstream entry missing name: {s}")
            if not s.get("dir"):
                warnings.append(f"upstream entry '{s.get('name','?')}' missing dir")

        # 3. Cross-dir same-name: count groups that span 2+ agents
        cross_agent_count = sum(1 for dup in sn if len(set(l.get("agent", "") for l in dup.get("locations", []))) >= 2)
        within_agent_count = 0
        sn_by_agent = {}
        for dup in sn:
            if len(dup.get("locations", [])) < 2:
                continue
            for loc in dup.get("locations", []):
                a = loc.get("agent", "其他")
                if a not in sn_by_agent:
                    sn_by_agent[a] = set()
                sn_by_agent[a].add(dup["name"])
        for a, names in sn_by_agent.items():
            # Count names where this agent has 2+ locations
            for dup in sn:
                agent_locs = [l for l in dup.get("locations", []) if l.get("agent", "其他") == a]
                if len(agent_locs) >= 2:
                    within_agent_count += 1

        total_shown = cross_agent_count + within_agent_count
        if total_shown != len(sn):
            # Some groups might not be shown anywhere
            pass  # This is expected if some dups only have 1 location per agent

        return {"warnings": warnings, "checks": {
            "same_name_groups": len(sn),
            "cross_agent_groups": cross_agent_count,
            "within_agent_groups": within_agent_count,
            "upstream_sources": len(result.get("upstream_sources", [])),
        }}
