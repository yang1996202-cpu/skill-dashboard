#!/usr/bin/env python3
"""Skill Dashboard — 零依赖本地 WebUI，可视化管理 AI skill 文件。

HTTP 入口与基础设施:路由表分发、CSRF、静态文件、JSON 响应、运行态缓存。
各 domain 的 handler 在 skilldash/routes/*(DashboardHandler 多继承);
GitHub 业务在 skilldash/source_ops;扫描/治理在 skilldash 子模块。
"""

import json
import re
import sys
import time
import urllib.parse
import webbrowser
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse

from skilldash.paths import CACHE_DIR, HTML_FILE, PORT, STATE_DIR, STATIC_DIR
from skilldash.routes.cleanup import CleanupRoutes
from skilldash.routes.scan import ScanRoutes
from skilldash.routes.skill import SkillRoutes
from skilldash.routes.source import SourceRoutes
from skilldash.routes.system import SystemRoutes

# ── 运行态缓存(/api/targets 缓存;GitHub API 缓存在 skilldash.source_ops)──
_targets_cache = None  # cached /api/targets response
_targets_cache_ts = 0  # timestamp of last targets cache


class DashboardHandler(SkillRoutes, SourceRoutes, CleanupRoutes, ScanRoutes, SystemRoutes, BaseHTTPRequestHandler):
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
        ("GET", "/api/fast-scan"):              ("_fast_scan", None),
        ("GET", "/api/diagnosis-status"):       ("_diagnosis_status", None),
        ("GET", "/api/openapi"):                ("_openapi", None),
        ("GET", "/api/understand"):             ("_serve_understanding", None),
        ("GET", "/api/source/skills"):          ("_list_source_skills", None),
        ("GET", "/api/search-skills"):          ("_search_skills", None),
        ("GET", "/api/custom-sources"):         ("_get_custom_sources", None),
        ("GET", "/api/global-stats"):           ("_global_stats", None),
        ("GET", "/api/installed-plugins"):      ("_installed_plugins_api", None),
        ("GET", "/api/mcp-inventory"):          ("_mcp_inventory_api", None),
        ("GET", "/api/scan-result"):            ("_scan_result", None),
        ("GET", "/api/trash"):                  ("_list_trash", None),
        ("GET", "/api/trash/stats"):            ("_trash_stats", None),
        ("GET", "/api/preview"):                ("_preview_route", None),
        ("POST", "/api/target"):                ("_set_target", None),
        ("POST", "/api/diagnose"):              ("_diagnose", None),
        ("POST", "/api/scan-run"):              ("_run_scan", None),
        ("POST", "/api/cleanup-execute"):       ("_cleanup_execute", None),
        ("POST", "/api/duplicate-decision"):    ("_duplicate_decision", None),
        ("POST", "/api/steal"):                 ("_steal_skill", None),
        ("POST", "/api/steal-npx"):             ("_steal_npx", None),
        ("POST", "/api/code-search"):           ("_code_search", None),
        ("POST", "/api/search-source"):         ("_search_source", None),
        ("POST", "/api/probe-source"):          ("_probe_source", None),
        ("POST", "/api/attach-source"):         ("_attach_source", None),
        ("POST", "/api/copy-skill"):            ("_copy_skill", None),
        ("POST", "/api/batch-delete"):          ("_batch_delete", None),
        ("POST", "/api/custom-sources"):        ("_add_custom_source", None),
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
        """Serve index.html,给 /static/*.js|*.css 注入 ?v=<mtime> 做 cache-busting。

        文件一改 mtime 变 → URL 变 → 浏览器强制重下。根治"改前端但用户浏览器
        缓存旧/混合 JS 导致功能不生效/状态乱"(2026-06-28)。_dispatch 已用
        urlparse 剥 query,_serve_static 收到的 path 不含 ?v=,不受影响。
        """
        try:
            html = HTML_FILE.read_text("utf-8")
            def _add_v(m):
                rel = m.group(1)
                try:
                    v = str(int((STATIC_DIR / rel).stat().st_mtime))
                except Exception:
                    v = "0"
                return f"/static/{rel}?v={v}"
            html = re.sub(r"/static/([\w./-]+\.(?:js|css))", _add_v, html)
            data = html.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            self.end_headers()
            self.wfile.write(data)
        except FileNotFoundError:
            self.send_error(404, "index.html not found")


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


    # ── Trash ──


    # ── Diagnosis (uses module-level globals + lock) ──


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
