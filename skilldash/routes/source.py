"""source 域路由 handler:目标库列表/切换、来源 skill 列表/搜索、自定义来源、
GitHub 安装(_steal_skill)、本机插件状态。

从 serve.py 拆出的 mixin。handler 逻辑原样搬出,self 引用不变。运行态缓存
(_targets_cache)封装为 serve 基类的 self._targets_cache_hit/_store;业务函数
(install_skill 等)在 skilldash.source_ops;均无循环依赖。
"""
from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path
from urllib.parse import urlparse, parse_qs

from skilldash.content_hash import check_content_changes
from skilldash.discovery import (
    _agent_from_path,
    _classify_skill_role,
    _classify_skill_dir_detail,
    _discover_command_dirs,
    _discover_skill_dirs,
    _is_skill_entry,
    _scan_commands,
    _summarize_skill_roles,
    _skill_entry_kind,
)
from skilldash.host_inspectors import host_profile_summaries_by_agent, load_claude_plugin_state, load_mcp_inventory
from skilldash.paths import CACHE_DIR, STATE_DIR
from skilldash.source_ops import install_skill
from skilldash.understanding import compact_understanding, understand_skill


READINESS_META = {
    "uninitialized": {"label": "未初始化", "desc": "Agent 目录不存在或完全空"},
    "configured-empty": {"label": "已配置/空", "desc": "有 skills/ 或 mcp.json,但 0 skill"},
    "builtin-only": {"label": "仅内置", "desc": "只有宿主自带 skill"},
    "light": {"label": "轻度使用", "desc": "少量 skill(1-9)"},
    "heavy": {"label": "重度使用", "desc": "10+ skill,或连接器/插件/已启用 MCP 较多"},
}


def _derive_agent_form(agent: str, dirs: list[dict], profile_summary) -> str:
    """Agent 形态:cli / app / ide。

    主信号看路径(目录里带的路径前缀比 agent 名更稳):
    - ~/Library/Application Support/* → app(桌面 App 内置的 skill 数据根)
    - .workbuddy/.codebuddy(buddy family) → app(有 .app bundle,桌面 App)
    - 其他 .xxx dotdir(含 .claude/.codex/.cursor 等)→ cli

    没有目录信息时退回 profile_summary.family;都没有则 cli(默认形态,
    绝大多数 dotdir agent 都是 CLI)。
    """
    summary = profile_summary or {}
    for d in dirs:
        rel = (d.get("rel") or "").lower()
        if "/library/application support/" in rel:
            return "app"
        if "/.workbuddy/" in rel or "/.codebuddy/" in rel:
            return "app"
    family = (summary.get("family") or "").lower()
    if family in ("app", "desktop", "electron"):
        return "app"
    if family in ("ide",):
        return "ide"
    return "cli"


def _extension_breakdown(dirs: list[dict]) -> dict:
    """按 extension_type 聚合每个 Agent 的构成(skill/builtin/plugin/...)。

    只统计 type=='skills' 的目录(commands 目录无 extension_type)。
    返回 dict,key 是已知的 extension_type 取值,value 是 skill 计数和。
    """
    breakdown = {}
    for d in dirs:
        if d.get("type") != "skills":
            continue
        ext = d.get("extension_type") or "unknown"
        breakdown[ext] = breakdown.get(ext, 0) + d.get("count", 0)
    return breakdown


def _derive_group_readiness(dirs, total_skills, profile_summary):
    """Agent 级就绪度:uninitialized / configured-empty / builtin-only / light / heavy。

    用 active_skills(只算 skill/builtin/plugin/connector,排除市场货架 catalog 和缓存)
    反映真实使用程度——total_skills 含库存会把有大 marketplace 的 Agent 全判成 heavy。
    uninitialized 用 total_skills==0(连货架都没有才算真空);configured-empty 是有货架
    但 0 活跃 skill。heavy 条件:10+ 活跃 skill,或连接器/插件运行包多,或已启用 MCP 多。
    详见 docs/skill-model.md 第 7 节。
    """
    summary = profile_summary or {}
    src_count = summary.get("source_root_count", 0)
    mcp_count = summary.get("mcp_server_count", 0)
    mcp_enabled = summary.get("mcp_enabled_count", 0)
    has_user_root = any(d.get("layer") in ("active-root", "user-installed", "app-embedded") for d in dirs)
    has_builtin = any(d.get("extension_type") == "builtin" for d in dirs)
    runtime_extensions = sum(
        d.get("count", 0) for d in dirs
        if d.get("extension_type") in ("connector", "plugin")
    )
    active_skills = sum(
        d.get("count", 0) for d in dirs
        if d.get("extension_type") in ("skill", "builtin", "plugin", "connector")
    )
    if total_skills == 0 and src_count == 0 and mcp_count == 0:
        return "uninitialized"
    if active_skills == 0:
        return "configured-empty"
    if not has_user_root and has_builtin and runtime_extensions == 0:
        return "builtin-only"
    if active_skills >= 10 or runtime_extensions >= 6 or mcp_enabled >= 5:
        return "heavy"
    return "light"


class SourceRoutes:

    def _installed_plugins_api(self):
        self._json_response(self._installed_plugins())

    def _installed_plugins(self):
        """Return Claude plugins installed on this machine."""
        state = load_claude_plugin_state()
        plugins = []
        for plugin_id, records in state.get("installed", {}).items():
            for rec in records:
                plugins.append({
                    "id": plugin_id,
                    "marketplace": plugin_id.split("@")[-1] if "@" in plugin_id else "",
                    "install_path": rec.get("install_path", ""),
                    "version": rec.get("version", ""),
                    "scope": rec.get("scope", ""),
                    "enabled": plugin_id in state.get("enabled", set()),
                })
        return {
            "plugins": plugins,
            "enabled": list(state.get("enabled", set())),
            "marketplaces": list(state.get("marketplaces", {}).keys()),
        }

    def _mcp_inventory_api(self):
        """Cross-agent MCP server inventory (non-sensitive)."""
        self._json_response(load_mcp_inventory())

    def _list_source_skills(self):
        """Return skills or commands in a given source directory (for穿透 browsing)."""
        query = parse_qs(urlparse(self.path).query)
        source_path = query.get("path", [""])[0]
        if not source_path:
            self._json_response({"error": "missing path param"}, status=400)
            return
        # Normalize path placeholders
        home = str(Path.home())
        source_path = source_path.replace("${HOME}", home).replace("$HOME", home)
        if source_path.startswith("~"):
            source_path = str(Path.home() / source_path[2:])
        source_dir = Path(source_path).resolve()
        # 允许 home 下,或 discovery 发现的 macOS app bundle builtin skill(/Applications/*.app/...)
        is_app_builtin = source_dir.is_relative_to(Path("/Applications")) and ".app/" in str(source_dir)
        if not (source_dir.is_relative_to(Path.home()) or is_app_builtin):
            self._json_response({"error": "path must be under home directory or a discovered app bundle"}, status=403)
            return
        if not source_dir.is_dir():
            self._json_response({"error": f"not a dir: {source_path}"}, status=400)
            return

        is_commands = source_dir.name == "commands"
        with_understanding = query.get("understanding", ["0"])[0].lower() in ("1", "true", "yes")
        result = []
        t_parse = time.time()
        if is_commands:
            for f in sorted(source_dir.iterdir()):
                if not f.is_file() or f.suffix != ".md":
                    continue
                description = ""
                try:
                    text = f.read_text("utf-8", errors="ignore")[:2000]
                    if text.startswith("---"):
                        end = text.find("---", 3)
                        if end > 0:
                            for line in text[3:end].splitlines():
                                line = line.strip()
                                if line.startswith("description:"):
                                    description = line.split(":", 1)[1].strip().strip("'\"")
                                    break
                    if not description:
                        for line in text.splitlines():
                            line = line.strip().lstrip("#").strip()
                            if line and not line.startswith("---"):
                                description = line[:160]
                                break
                except Exception:
                    pass
                result.append({
                    "name": f.stem,
                    "description": description,
                    "kind": "command",
                    "understanding": None,
                })
        else:
            for d in sorted(source_dir.iterdir()):
                if not _is_skill_entry(d, include_broken=True):
                    continue
                skill_md = d / "SKILL.md"
                name = d.name
                description = ""
                kind = _skill_entry_kind(d)
                if skill_md.exists():
                    try:
                        text = skill_md.read_text("utf-8", errors="ignore")[:2000]
                        if text.startswith("---"):
                            end = text.find("---", 3)
                            if end > 0:
                                fm = text[3:end]
                                for line in fm.splitlines():
                                    line = line.strip()
                                    if line.startswith("description:"):
                                        description = line.split(":", 1)[1].strip().strip("'\"")
                    except Exception:
                        pass
                understanding = None
                if with_understanding and skill_md.exists():
                    try:
                        understanding = compact_understanding(understand_skill(d, CACHE_DIR))
                    except Exception:
                        understanding = None
                result.append({
                    "name": name,
                    "description": description,
                    "kind": kind,
                    **_classify_skill_role(d),
                    "understanding": understanding,
                })
        self._json_response({
            "source": str(source_dir).replace(str(Path.home()), "~"),
            "type": "commands" if is_commands else "skills",
            "skills": result,
            "count": len(result),
        })

    def _search_skills(self):
        """Lightweight cross-directory skill name search.

        Only matches skill directory names; does not read SKILL.md content.
        Query: /api/search-skills?q=xxx&limit=50
        """
        query = parse_qs(urlparse(self.path).query)
        q = (query.get("q", [""])[0] or "").strip().lower()
        try:
            limit = min(200, int(query.get("limit", ["50"])[0]))
        except ValueError:
            limit = 50
        if len(q) < 2:
            self._json_response({"error": "query must be at least 2 characters"}, status=400)
            return

        home = Path.home()
        skill_dirs = _discover_skill_dirs()
        current_target = self._current_target() or ""
        current_agent = _agent_from_path(str(current_target)) if current_target else ""

        results = []
        total_matches = 0
        for skills_dir in skill_dirs:
            try:
                for entry in sorted(skills_dir.iterdir()):
                    if not _is_skill_entry(entry, include_broken=True):
                        continue
                    if q not in entry.name.lower():
                        continue
                    total_matches += 1
                    if len(results) >= limit:
                        continue
                    rel = str(skills_dir).replace(str(home), "~")
                    results.append({
                        "name": entry.name,
                        "dir": str(skills_dir),
                        "rel": rel,
                        "agent": _agent_from_path(str(skills_dir)),
                        "category": _classify_skill_dir_detail(skills_dir).get("category", "unknown"),
                        "scope": "project" if "/projects/" in rel else "global",
                        "kind": _skill_entry_kind(entry),
                        **_classify_skill_role(entry),
                    })
            except (PermissionError, OSError):
                continue
            if len(results) >= limit:
                break

        grouped = {}
        for r in results:
            grouped.setdefault(r["agent"], []).append(r)

        groups = [
            {"agent": agent, "skills": skills}
            for agent, skills in sorted(
                grouped.items(),
                key=lambda item: (0 if item[0] == current_agent else 1, -len(item[1]))
            )
        ]

        self._json_response({
            "q": q,
            "total_matches": total_matches,
            "returned": len(results),
            "groups": groups,
        })

    def _get_custom_sources(self):
        """Return user-defined custom source paths."""
        self._json_response(self._load_custom_sources())

    def _add_custom_source(self):
        """Add a custom source path. Checks for duplicates against auto-discovered dirs."""
        length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(length).decode('utf-8') if length else '{}'
        try:
            data = json.loads(body)
        except Exception:
            data = {}
        new_path = data.get("path", "").strip()
        if not new_path:
            self._json_response({"error": "missing path"}, status=400)
            return
        # Expand ~
        if new_path.startswith("~"):
            new_path = str(Path.home() / new_path[2:])
        p = Path(new_path).resolve()
        if not p.exists():
            self._json_response({"error": f"path does not exist: {new_path}"}, status=400)
            return
        # Must have skills/ subdir or be a skills dir itself
        skills_dir = p / "skills" if p.name != "skills" else p
        if not skills_dir.is_dir():
            self._json_response({"error": f"no skills/ subdir found in {new_path}"}, status=400)
            return
        # Check duplicate against auto-discovered directories (inode-level)
        try:
            new_stat = p.stat()
            new_inode = (new_stat.st_dev, new_stat.st_ino)
            discovered = _discover_skill_dirs()
            for d in discovered:
                try:
                    if d.resolve().stat().st_dev == new_inode[0] and d.resolve().stat().st_ino == new_inode[1]:
                        self._json_response({"ok": True, "path": new_path, "skipped": True,
                                             "message": f"已在自动发现中: {d}"})
                        self._log_history(
                            "add_source",
                            paths=[new_path],
                            count=0,
                            source="custom_sources",
                            status="blocked",
                            detail={"path": new_path, "reason": f"already discovered: {d}"},
                        )
                        return
                except OSError:
                    continue
        except OSError:
            pass
        paths = self._load_custom_sources()
        added = new_path not in paths
        if added:
            paths.append(new_path)
            self._save_custom_sources(paths)
        self._json_response({"ok": True, "path": new_path, "paths": paths})
        self._log_history(
            "add_source",
            paths=[new_path],
            count=1,
            source="custom_sources",
            status="ok",
            detail={"path": new_path, "added": added},
        )

    def _remove_custom_source(self):
        """Remove a custom source path."""
        query = parse_qs(urlparse(self.path).query)
        rm_path = query.get("path", [""])[0]
        if not rm_path:
            self._json_response({"error": "missing path"}, status=400)
            return
        paths = self._load_custom_sources()
        removed = rm_path in paths
        if removed:
            paths.remove(rm_path)
            self._save_custom_sources(paths)
        self._json_response({"ok": True, "paths": paths})
        self._log_history(
            "remove_source",
            paths=[rm_path],
            count=1 if removed else 0,
            source="custom_sources",
            status="ok" if removed else "blocked",
            detail={"path": rm_path, "removed": removed},
        )

    def _load_custom_sources(self):
        """Load user-defined custom source paths."""
        try:
            cf = STATE_DIR / "custom-sources.json"
            if cf.exists():
                return json.loads(cf.read_text())
        except Exception:
            pass
        return []

    def _save_custom_sources(self, paths):
        """Save user-defined custom source paths."""
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        cf = STATE_DIR / "custom-sources.json"
        cf.write_text(json.dumps(paths, ensure_ascii=False, indent=2), encoding="utf-8")

    def _list_targets(self):
        """List all discovered skill directories grouped by agent.
        Uses shared _discover_skill_dirs for directory discovery.
        Results are cached for 60 seconds to avoid repeated filesystem scans.
        """
        query = parse_qs(urlparse(self.path).query)
        force_refresh = query.get("refresh", ["0"])[0].lower() in ("1", "true", "yes")
        cached = self._targets_cache_hit(force_refresh)
        if cached is not None:
            current = self._current_target()
            for t in cached.get("targets", []):
                t["is_current"] = t["path"] == current
            self._json_response(cached)
            return

        home = Path.home()
        current = self._current_target()

        # Reuse shared discovery
        skill_dirs = _discover_skill_dirs()
        command_dirs = _discover_command_dirs()

        targets = []
        for skills_dir in skill_dirs:
            count = sum(1 for d in skills_dir.iterdir()
                       if (d.is_dir() or d.is_symlink()) and (d / "SKILL.md").exists())
            if count == 0:
                continue
            rel = str(skills_dir).replace(str(home), "~")
            # Use shared agent detection
            agent = _agent_from_path(str(skills_dir))
            scope = "project" if "projects/" in rel else "global"
            governance = _classify_skill_dir_detail(skills_dir)
            role_summary = _summarize_skill_roles(skills_dir)
            targets.append({
                "path": str(skills_dir),
                "rel": rel,
                "name": agent,
                "scope": scope,
                "count": count,
                "type": "skills",
                "is_current": str(skills_dir) == current,
                **role_summary,
                **governance,
            })

        # Add command directories
        for commands_dir in command_dirs:
            count = sum(1 for f in commands_dir.iterdir() if f.is_file() and f.suffix == ".md")
            if count == 0:
                continue
            rel = str(commands_dir).replace(str(home), "~")
            agent = _agent_from_path(str(commands_dir))
            scope = "project" if "projects/" in rel else "global"
            targets.append({
                "path": str(commands_dir),
                "rel": rel,
                "name": agent,
                "scope": scope,
                "count": count,
                "type": "commands",
                "is_current": False,
                "category": "commands",
                "policy": "review",
                "layer": "commands",
                "layer_label": "命令",
                "policy_label": "复核",
            })

        # Claude 已安装插件 → 能力来源条目（enabled→当前可用 active-plugin，disabled→来源库存 installed-disabled）
        # discovery 排除了 plugins/cache（防灌水），这里把 load_claude_plugin_state 读到的 plugin 单独注入
        plugin_state = load_claude_plugin_state(home)
        _enabled_plugins = plugin_state.get("enabled") or set()
        for plugin_id, records in (plugin_state.get("installed") or {}).items():
            for record in records:
                install_path = record.get("install_path")
                if not install_path:
                    continue
                ip = Path(install_path).expanduser()
                if not ip.exists():
                    continue
                skills_root = ip / "skills"
                if skills_root.exists():
                    count = sum(1 for d in skills_root.iterdir() if d.is_dir() and (d / "SKILL.md").exists())
                else:
                    count = 0
                is_enabled = plugin_id in _enabled_plugins
                targets.append({
                    "path": install_path,
                    "rel": plugin_id,
                    "name": "Claude Code",
                    "scope": "global",
                    "count": count,
                    "type": "plugin",
                    "extension_type": "plugin",
                    "is_current": install_path == current,
                    "runtime_state": "enabled" if is_enabled else "installed",
                    "layer": "claude-plugin",
                    "layer_label": "已启用插件" if is_enabled else "已安装未启用",
                    "policy": "observe",
                })

        targets.sort(key=lambda t: (0 if t["is_current"] else 1, -t["count"]))

        # Group by agent name
        profile_summaries = host_profile_summaries_by_agent(home)
        grouped = {}
        for t in targets:
            agent = t["name"]
            if agent not in grouped:
                grouped[agent] = {
                    "agent": agent,
                    "dirs": [],
                    "total_skills": 0,
                    "profile_summary": profile_summaries.get(agent),
                }
            grouped[agent]["dirs"].append(t)
            grouped[agent]["total_skills"] += t["count"]

        # Derive per-agent readiness after aggregation (layer/extension_type now known)
        for _agent, _g in grouped.items():
            _summary = _g.get("profile_summary")
            _r = _derive_group_readiness(_g["dirs"], _g["total_skills"], _summary)
            _g["readiness"] = _r
            _g["readiness_label"] = READINESS_META[_r]["label"]
            # profile_family 单独挂顶层(便于前端不挖 profile_summary);profile_summary 已含
            _g["profile_family"] = _summary.get("family") if _summary else None
            # Agent 形态:cli / app / ide
            _g["agent_form"] = _derive_agent_form(_agent, _g["dirs"], _summary)
            # 按 extension_type 聚合构成
            _g["extension_breakdown"] = _extension_breakdown(_g["dirs"])

        # Sort groups: current target's group first, then by total skills desc
        current_agent = next((t["name"] for t in targets if t["is_current"]), "")
        groups = sorted(grouped.values(),
                        key=lambda g: (0 if g["agent"] == current_agent else 1, -g["total_skills"]))

        # Flat list for backward compat + grouped view
        result = {"targets": targets, "groups": groups}
        self._targets_cache_store(result)
        self._json_response(result)

    def _set_target(self):
        """Switch target — fast scan directly, no bash subprocess."""
        length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(length).decode('utf-8') if length else '{}'
        try:
            data = json.loads(body)
        except Exception:
            data = {}
        target_path = data.get("target", "")
        if not target_path:
            self._json_response({"error": "missing target"}, status=400)
            return
        if target_path.startswith("~"):
            target_path = str(Path.home() / target_path[2:])
        if not Path(target_path).is_dir():
            self._json_response({"error": f"not a directory: {target_path}"}, status=400)
            return

        # Write to dedicated state file so _current_target picks it up
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        home = Path.home()
        rel = str(target_path).replace(str(home), "~")
        ct_file = STATE_DIR / "current-target.json"
        ct_file.write_text(json.dumps({"path": rel, "label": Path(target_path).parent.name}, ensure_ascii=False, indent=2), encoding="utf-8")

        self._log_history(
            "switch_target",
            paths=[rel],
            count=0,
            source="target_switcher",
            status="ok",
            detail={"label": Path(target_path).parent.name},
        )

        # Now do fast scan
        self._fast_scan()

    def _steal_skill(self):
        """Install a skill from GitHub URL — pure Python, no dashboard."""
        length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(length).decode('utf-8') if length else '{}'
        try:
            data = json.loads(body)
        except Exception:
            data = {}
        source = data.get("source", "").strip()
        skill_name = data.get("name", "").strip()
        if not source:
            self._json_response({"error": "missing source URL"}, status=400)
            return

        target = self._current_target()
        result = install_skill(source, target, preferred_name=skill_name or None)
        self._json_response(result)
        status = "ok" if result.get("ok") or result.get("success") else "failed"
        self._log_history(
            "install",
            paths=[result.get("path", "") or source],
            count=1 if status == "ok" else 0,
            source="steal",
            status=status,
            detail={"source": source, "name": skill_name or result.get("name", ""), "error": result.get("error", "")},
        )
