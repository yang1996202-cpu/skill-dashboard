#!/usr/bin/env python3
"""Skill Dashboard — 零依赖本地 WebUI，可视化管理 AI skill 文件"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import urllib.parse
import webbrowser
from collections import Counter
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse, parse_qs

from skilldash.understanding import compact_understanding, understand_skill

from skilldash.cleanup import (
    _duplicate_skill_execute_allowed,
    _is_cleanup_execute_allowed,
    build_cleanup_execution_plan,
    build_cleanup_plan,
)
from skilldash.content_hash import check_content_changes, record_content_hash
from skilldash.decisions import (
    _duplicate_decision_key,
    _load_duplicate_decisions,
    _save_duplicate_decisions,
)
from skilldash.discovery import (
    _agent_from_path,
    _classify_skill_dir_detail,
    _discover_command_dirs,
    _discover_skill_dirs,
    _is_skill_entry,
    _scan_commands,
    _scan_global_categories,
    _skill_entry_kind,
    _skill_marker_exists,
)
from skilldash.source_ops import (
    GITHUB_TOKEN,
    check_upstream_status,
    create_snapshot,
    install_skill,
    update_skill,
)
from skilldash.host_inspectors import host_profile_summaries_by_agent, load_claude_plugin_state
from skilldash.routes.cleanup import CleanupRoutes
from skilldash.routes.skill import SkillRoutes
from skilldash.routes.source import SourceRoutes
from skilldash.routes.system import SystemRoutes
from skilldash.overlap import _find_same_name_duplicates
from skilldash.paths import (
    CACHE_DIR,
    DIAG_LOG,
    DUPLICATE_DECISIONS_FILE,
    HTML_FILE,
    PORT,
    STATE_DIR,
    STATIC_DIR,
    load_cached_diagnosis,
)

# ── 运行态缓存(/api/targets 缓存;GitHub API 缓存在 skilldash.source_ops)──
_targets_cache = None  # cached /api/targets response
_targets_cache_ts = 0  # timestamp of last targets cache


# ── Diagnosis state (module-level, protected by lock) ──
_diag_lock = threading.Lock()
_diag_process = None
_diag_target = ""
_diag_start = 0
_diag_phase = ""


class DashboardHandler(SkillRoutes, SourceRoutes, CleanupRoutes, SystemRoutes, BaseHTTPRequestHandler):
    """Serve index.html and API endpoints."""

    def _read_json(self):
        """Read and parse JSON body from request. Returns dict or None."""
        try:
            length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(length).decode('utf-8') if length else '{}'
            return json.loads(body)
        except Exception:
            return None

    @staticmethod
    def _path_part(path, index):
        parts = path.split("/")
        if len(parts) <= index:
            return ""
        return urllib.parse.unquote(parts[index])

    @staticmethod
    def _validate_skill_name(name):
        """Sanitize skill name from URL. Rejects path traversal attempts."""
        if not name or '..' in name or '/' in name or '\\' in name:
            return None
        if name.startswith('.') or name.startswith('-'):
            return None
        # Allow chars observed in skill directory names while blocking paths.
        if not re.match(r'^[a-zA-Z0-9._@+\-()]+$', name):
            return None
        return name

    def _check_csrf(self):
        """Reject cross-origin write requests. Returns True if safe."""
        origin = self.headers.get("Origin", "")
        referer = self.headers.get("Referer", "")
        # Allow requests with no Origin/Referer (curl, CLI tools, direct browser nav)
        if not origin and not referer:
            return True
        # Check Origin first (preferred)
        if origin:
            allowed = [f"http://127.0.0.1:{PORT}", f"http://localhost:{PORT}"]
            return origin in allowed
        # Fallback to Referer
        if referer:
            parsed = urlparse(referer)
            return parsed.hostname in ("127.0.0.1", "localhost") and parsed.port == PORT
        return True

    def _csrf_reject(self):
        """Send a 403 CSRF rejection response."""
        self._json_response({"error": "CSRF check failed — cross-origin request rejected"}, status=403)

    # ── 路由表:(method, rule) → (handler, param) ──
    # rule 为 str:精确匹配;为 (prefix, suffix):前缀+后缀,suffix="" 表示纯前缀
    # param: None=无参;"name"=解析路径第3段为 skill 名并校验;"path"=传完整 path
    _ROUTES_EXACT = {
        ("GET", "/"):                           ("_serve_index", None),
        ("GET", "/index.html"):                 ("_serve_index", None),
        ("GET", "/api/history"):                ("_serve_history", None),
        ("GET", "/api/targets"):                ("_list_targets", None),
        ("GET", "/api/cleanup-plan"):           ("_cleanup_plan", None),
        ("GET", "/api/cleanup-execution-plan"): ("_cleanup_execution_plan", None),
        ("GET", "/api/duplicate-decisions"):    ("_list_duplicate_decisions", None),
        ("GET", "/api/category-order"):         ("_get_category_order", None),
        ("GET", "/api/fast-scan"):              ("_fast_scan", None),
        ("GET", "/api/diagnosis-status"):       ("_diagnosis_status", None),
        ("GET", "/api/openapi"):                ("_openapi", None),
        ("GET", "/api/understand"):             ("_serve_understanding", None),
        ("GET", "/api/source/skills"):          ("_list_source_skills", None),
        ("GET", "/api/search-skills"):          ("_search_skills", None),
        ("GET", "/api/custom-sources"):         ("_get_custom_sources", None),
        ("GET", "/api/global-stats"):           ("_global_stats", None),
        ("GET", "/api/installed-plugins"):      ("_installed_plugins_api", None),
        ("GET", "/api/scan-result"):            ("_scan_result", None),
        ("GET", "/api/trash"):                  ("_list_trash", None),
        ("GET", "/api/preview"):                ("_preview_route", None),
        ("POST", "/api/target"):                ("_set_target", None),
        ("POST", "/api/diagnose"):              ("_diagnose", None),
        ("POST", "/api/scan-run"):              ("_run_scan", None),
        ("POST", "/api/cleanup-execute"):       ("_cleanup_execute", None),
        ("POST", "/api/duplicate-decision"):    ("_duplicate_decision", None),
        ("POST", "/api/steal"):                 ("_steal_skill", None),
        ("POST", "/api/copy-skill"):            ("_copy_skill", None),
        ("POST", "/api/batch-delete"):          ("_batch_delete", None),
        ("POST", "/api/custom-sources"):        ("_add_custom_source", None),
        ("POST", "/api/category-order"):        ("_set_category_order", None),
        ("DELETE", "/api/custom-sources"):      ("_remove_custom_source", None),
        ("DELETE", "/api/duplicate-decision"):  ("_remove_duplicate_decision", None),
        ("DELETE", "/api/trash"):               ("_empty_trash", None),
    }

    _ROUTES_PREFIX = [
        # (method, prefix, suffix, handler, param)  suffix="" 表示纯前缀
        ("GET", "/static/", "", "_serve_static", "path"),
        ("GET", "/api/trash/", "/restore", "_restore_trash", "path"),
        ("GET", "/api/skill/", "/content", "_serve_skill_content", "name"),
        ("GET", "/api/skill/", "/upstream", "_check_skill_upstream", "name"),
        ("POST", "/api/trash/", "/restore", "_restore_trash", "path"),
        ("POST", "/api/skill/", "/rehash", "_rehash_skill", "name"),
        ("DELETE", "/api/skill/", "", "_delete_skill", "name"),
        ("DELETE", "/api/trash/", "", "_delete_trash", "path"),
        ("PATCH", "/api/skill/", "/update", "_update_upstream", "name"),
        ("PATCH", "/api/skill/", "/fix", "_fix_skill", "name"),
    ]

    def _dispatch(self, method):
        """路由分发:先查精确表,再遍历前缀表。"""
        path = urlparse(self.path).path
        route = self._ROUTES_EXACT.get((method, path))
        if route:
            self._invoke(route[0], route[1], path)
            return
        for m, prefix, suffix, handler_name, param in self._ROUTES_PREFIX:
            if m != method or not path.startswith(prefix):
                continue
            if suffix and not path.endswith(suffix):
                continue
            self._invoke(handler_name, param, path)
            return
        self.send_error(404)

    def _invoke(self, handler_name, param, path):
        """按 param 约定调用 handler。"""
        handler = getattr(self, handler_name)
        if param is None:
            handler()
        elif param == "name":
            name = self._validate_skill_name(self._path_part(path, 3))
            if not name:
                self.send_error(400, "Invalid skill name")
                return
            handler(name)
        else:  # "path"
            handler(path)

    def do_GET(self):
        self._dispatch("GET")

    def do_POST(self):
        if not self._check_csrf():
            self._csrf_reject()
            return
        self._dispatch("POST")

    def do_DELETE(self):
        if not self._check_csrf():
            self._csrf_reject()
            return
        self._dispatch("DELETE")

    # ── 路由 handler 包装(从原 do_ 内联业务抽离)──
    def _serve_index(self):
        self._serve_file(HTML_FILE, "text/html; charset=utf-8")

    def _global_stats(self):
        self._json_response(_scan_global_categories())


    def _scan_result(self):
        self._serve_json(CACHE_DIR / "scan-result.json")


    # ── API implementations ──

    def _serve_file(self, filepath, content_type):
        try:
            data = filepath.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            self.send_header("Pragma", "no-cache")
            self.send_header("Expires", "0")
            self.end_headers()
            self.wfile.write(data)
        except FileNotFoundError:
            self.send_error(404, f"File not found: {filepath}")

    def _serve_static(self, path):
        """Serve static frontend assets without exposing arbitrary files."""
        rel = path.removeprefix("/static/").lstrip("/")
        if not rel or ".." in rel or rel.startswith("."):
            self.send_error(404)
            return
        filepath = (STATIC_DIR / rel).resolve()
        try:
            if not filepath.is_relative_to(STATIC_DIR.resolve()):
                self.send_error(404)
                return
        except Exception:
            self.send_error(404)
            return
        suffix = filepath.suffix.lower()
        content_type = {
            ".css": "text/css; charset=utf-8",
            ".js": "application/javascript; charset=utf-8",
            ".json": "application/json; charset=utf-8",
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".svg": "image/svg+xml",
        }.get(suffix, "application/octet-stream")
        self._serve_file(filepath, content_type)

    def _serve_json(self, filepath):
        try:
            data = filepath.read_text(encoding="utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(data.encode("utf-8"))
        except FileNotFoundError:
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"error": "state file not found, switch target to generate data"}')

    def _log_history(self, op, paths=None, count=0, source="", status="ok", detail=None):
        """Append one operation entry to history.jsonl.

        Args:
            op: operation key, e.g. move_to_trash, empty_trash, restore, delete
            paths: list of affected paths (relative to home when possible)
            count: number of skills/items affected
            source: which UI/API triggered the action
            status: ok|failed|blocked
            detail: arbitrary extra context
        """
        try:
            STATE_DIR.mkdir(parents=True, exist_ok=True)
            hist_file = STATE_DIR / "history.jsonl"
            home = str(Path.home())
            rel_paths = []
            for p in (paths or []):
                s = str(p)
                if s.startswith(home):
                    s = "~" + s[len(home):]
                rel_paths.append(s)
            entry = {
                "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "op": op,
                "source": source,
                "status": status,
                "count": count,
                "paths": rel_paths[:50],
                "detail": detail or {},
            }
            with hist_file.open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception:
            pass


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















    # ── Trash ──





    def _fast_scan(self):
        """Direct Python directory scan — milliseconds instead of bash subprocess."""
        target = self._current_target()
        target_dir = Path(target)
        if not target_dir.is_dir():
            self._json_response({"error": f"not a dir: {target}"}, status=400)
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

    # ── Diagnosis (uses module-level globals + lock) ──

    def _run_scan(self):
        """Run full scan across all discovered skill directories."""
        body = self._read_json() or {}

        directories = body.get("directories", [])
        requested_scope = body.get("scope") or ("deep" if not directories else "custom")
        # If no directories specified, scan all discovered dirs
        home = Path.home()
        if not directories:
            skill_dirs = _discover_skill_dirs()
            directories = [str(d) for d in skill_dirs
                          if sum(1 for x in d.iterdir()
                                if (x.is_dir() or x.is_symlink()) and (x / "SKILL.md").exists()) > 0]

        # Always run all check types
        checks = body.get("checks", ["same-name", "upstream", "content-changes"])

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

        # Per-directory checks
        for tdir in valid_dirs:
            dir_skills = []
            try:
                for d in sorted(tdir.iterdir()):
                    if (d.is_dir() or d.is_symlink()) and (d / "SKILL.md").exists():
                        dir_skills.append({"name": d.name})
            except Exception:
                continue

            if not dir_skills:
                continue

            # Upstream tracking
            if "upstream" in checks:
                for s in dir_skills:
                    skill_dir = tdir / s["name"]
                    try:
                        status = check_upstream_status(skill_dir)
                        # 允许有 repo 的 unknown 也进入：本地检测到 .git remote / lock 来源，
                        # 但 GitHub API 限流或未配 token 时无法判定版本（status=unknown）。
                        # 仍展示来源，让未配 token 的用户看到"哪些 skill 可追踪"。
                        if status.get("repo") and status.get("status") in ("current", "outdated", "unknown"):
                            result["upstream_sources"].append({
                                "name": s["name"],
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
                    except Exception:
                        pass

            # Content changes
            if "content-changes" in checks:
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

    def _diagnose(self):
        """Trigger Python-only diagnosis in background. No dashboard needed."""
        global _diag_process, _diag_target, _diag_start, _diag_phase
        target = self._current_target()

        with _diag_lock:
            # Check if already running
            if _diag_process and _diag_process.poll() is None:
                elapsed = int((time.time() - _diag_start) * 1000)
                if elapsed > 60000:
                    _diag_process.kill()
                    _diag_process = None
                    self._json_response({"status": "error", "error": "诊断超时 (60s)，请重试"})
                    return
                self._json_response({"status": "running", "target": _diag_target,
                                     "elapsed_ms": elapsed, "phase": "check"})
                return

            try:
                CACHE_DIR.mkdir(parents=True, exist_ok=True)
                log_f = open(DIAG_LOG, "w")
                worker_script = Path(__file__).parent / "_diag_worker.py"
                _diag_process = subprocess.Popen(
                    [sys.executable, str(worker_script), target],
                    stdout=log_f, stderr=subprocess.STDOUT,
                )
                _diag_target = target
                _diag_start = time.time()
                _diag_phase = "check"
                self._json_response({"status": "started", "target": target})
            except Exception as e:
                self._json_response({"status": "error", "error": str(e)})

    def _diagnosis_status(self):
        """Poll diagnosis progress. If done, cache and return results."""
        global _diag_process, _diag_target
        # Use the target captured when diagnosis started, not the current one
        # (user may have switched targets while diagnosis was running)
        target = _diag_target or self._current_target()

        with _diag_lock:
            # If process is running, check if it just finished
            if _diag_process and _diag_process.poll() is not None:
                _diag_process = None
                cached = load_cached_diagnosis(target)
                if cached:
                    cached["status"] = "done"
                    cached["duration_ms"] = int((time.time() - _diag_start) * 1000)
                    self._json_response(cached)
                    return
                else:
                    self._json_response({"status": "error", "error": "诊断完成但缓存未找到"})
                    return

            # Process still running
            if _diag_process and _diag_process.poll() is None:
                elapsed = int((time.time() - _diag_start) * 1000)
                self._json_response({"status": "running", "target": _diag_target,
                                     "elapsed_ms": elapsed, "phase": "check"})
                return

        # No process — check cache
        cached = load_cached_diagnosis(target)
        if cached:
            cached["status"] = "cached"
            self._json_response(cached)
            return

        self._json_response({"status": "idle"})




    def _targets_cache_hit(self, force_refresh=False):
        """Return deep-copied /api/targets cache payload if still valid, else None."""
        global _targets_cache, _targets_cache_ts
        if _targets_cache and not force_refresh and (time.time() - _targets_cache_ts) < 60:
            return json.loads(json.dumps(_targets_cache))
        return None

    def _targets_cache_store(self, data):
        global _targets_cache, _targets_cache_ts
        _targets_cache = data
        _targets_cache_ts = time.time()

    def _invalidate_runtime_caches(self):
        """Invalidate filesystem-derived caches after moving/deleting skills."""
        global _targets_cache, _targets_cache_ts
        _targets_cache = None
        _targets_cache_ts = 0
        for cache_name in ("global-categories.json",):
            try:
                (CACHE_DIR / cache_name).unlink()
            except FileNotFoundError:
                pass
            except Exception:
                pass

    def _current_target(self):
        """Read current target from dedicated state file, fallback to ~/.claude/skills."""
        # 1) Dedicated state file (most reliable)
        try:
            ct = json.loads((STATE_DIR / "current-target.json").read_text())
            tp = ct.get("path", "")
            if tp.startswith("~"):
                tp = str(Path.home() / tp[2:])
            if tp and Path(tp).is_dir():
                return tp
        except Exception:
            pass
        # 2) Fallback
        return str(Path.home() / ".claude/skills")






    def do_PATCH(self):
        """Handle skill update actions."""
        if not self._check_csrf():
            self._csrf_reject()
            return
        self._dispatch("PATCH")






    def _json_response(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def log_message(self, fmt, *args):
        """Quieter logging — only show API calls, not static file requests."""
        msg = fmt % args
        if "/api/" in msg or "POST" in msg or "DELETE" in msg:
            sys.stderr.write(f"  {msg}\n")


def main():
    if not STATE_DIR.exists():
        print(f"⚠ State dir not found: {STATE_DIR}")
        print(f"  Creating {STATE_DIR}...")
        STATE_DIR.mkdir(parents=True, exist_ok=True)

    server = ThreadingHTTPServer(("127.0.0.1", PORT), DashboardHandler)
    url = f"http://localhost:{PORT}"
    print(f"🚀 Skill Dashboard running at {url}")
    print(f"   Data source: {STATE_DIR}")
    print(f"   Install: POST /api/steal {{\"source\": \"https://github.com/...\"}}")
    print(f"   Update:  PATCH /api/skill/{{name}}/update")
    print(f"   Press Ctrl+C to stop")
    print()

    # Auto-open browser
    webbrowser.open(url)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n👋 Stopped.")
        server.server_close()


if __name__ == "__main__":
    main()
