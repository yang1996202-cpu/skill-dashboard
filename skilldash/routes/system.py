"""system 域路由 handler:history、openapi。

从 serve.py 拆出,作为 DashboardHandler 的 mixin。handler 逻辑原样搬出,
self 引用不变,仅物理分文件。STATE_DIR 从 skilldash.paths 导入;
self._json_response / self.send_error / self.headers / self.rfile
由 DashboardHandler 基类提供。
"""
import json

from skilldash.paths import STATE_DIR


class SystemRoutes:
    """history / openapi 等 system 级路由 handler。"""

    def _serve_history(self):
        hist_file = STATE_DIR / "history.jsonl"
        try:
            lines = hist_file.read_text(encoding="utf-8").strip().split("\n")
            entries = []
            for line in lines[-50:]:
                line = line.strip()
                if line:
                    try:
                        entries.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
            self._json_response(entries)
        except FileNotFoundError:
            self._json_response([])

    def _openapi(self):
        """Return simple API documentation."""
        self._json_response({
            "title": "Skill Dashboard API",
            "version": "2.0",
            "endpoints": [
                {"method": "GET", "path": "/api/fast-scan", "desc": "Instant skill list + classification"},
                {"method": "GET", "path": "/api/targets", "desc": "List available skill directories"},
                {"method": "GET", "path": "/api/cleanup-plan?scope=daily|deep", "desc": "Dry-run cleanup governance plan"},
                {"method": "GET", "path": "/api/cleanup-execution-plan?scope=&strategy=", "desc": "Executable-shaped cleanup preview without deletion"},
                {"method": "POST", "path": "/api/cleanup-execute", "desc": "Move selected cleanup candidates to trash"},
                {"method": "GET", "path": "/api/duplicate-decisions", "desc": "List local exact-duplicate handling decisions"},
                {"method": "POST", "path": "/api/duplicate-decision", "desc": "Persist exact-duplicate handling decisions"},
                {"method": "DELETE", "path": "/api/duplicate-decision?key=", "desc": "Remove a local exact-duplicate handling decision"},
                {"method": "GET", "path": "/api/global-stats", "desc": "Global category distribution across all skill libraries (cached 5min)"},
                {"method": "GET", "path": "/api/understand?dir=&name=", "desc": "Rule-based Chinese understanding for one skill"},
                {"method": "GET", "path": "/api/skill/{name}/content", "desc": "Read SKILL.md content"},
                {"method": "GET", "path": "/api/skill/{name}/upstream", "desc": "Check upstream status for a skill"},
                {"method": "POST", "path": "/api/target", "desc": "Switch target directory"},
                {"method": "POST", "path": "/api/scan-run", "desc": "Targeted scan: selected directories + analysis types"},
                {"method": "GET", "path": "/api/scan-result", "desc": "Get cached scan result"},
                {"method": "POST", "path": "/api/steal", "desc": "Install skill from GitHub URL"},
                {"method": "DELETE", "path": "/api/skill/{name}", "desc": "Delete a skill"},
                {"method": "PATCH", "path": "/api/skill/{name}/update", "desc": "Update skill from upstream"},
            ],
        })
