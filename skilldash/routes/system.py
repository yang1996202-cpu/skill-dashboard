"""system 域路由 handler:history、openapi。

从 serve.py 拆出,作为 DashboardHandler 的 mixin。handler 逻辑原样搬出,
self 引用不变,仅物理分文件。STATE_DIR 从 skilldash.paths 导入;
self._json_response / self.send_error / self.headers / self.rfile
由 DashboardHandler 基类提供。
"""
import json
import time
from collections import Counter
from datetime import datetime, timedelta
from urllib.parse import urlparse, parse_qs

from skilldash.paths import STATE_DIR


class SystemRoutes:
    """history / openapi 等 system 级路由 handler。"""

    def _serve_operation_stats(self):
        """Aggregate operation counts from full history.jsonl (not truncated)."""
        hist_file = STATE_DIR / "history.jsonl"
        try:
            lines = hist_file.read_text(encoding="utf-8").strip().split("\n")
        except FileNotFoundError:
            self._json_response({"totals": {}, "recent": {}, "since": None})
            return
        totals = Counter()
        recent = Counter()
        now = datetime.now()
        week_ago = now - timedelta(days=7)
        earliest = None
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            op = entry.get("op", "")
            if entry.get("status") != "ok":
                continue
            totals[op] += 1
            ts_str = entry.get("ts", "")
            if ts_str:
                try:
                    ts = datetime.strptime(ts_str, "%Y-%m-%dT%H:%M:%S")
                    if earliest is None or ts < earliest:
                        earliest = ts
                    if ts >= week_ago:
                        recent[op] += 1
                except ValueError:
                    pass
        self._json_response({
            "totals": dict(totals),
            "recent": dict(recent),
            "since": earliest.strftime("%Y-%m-%d") if earliest else None,
        })

    def _serve_governance_stats(self):
        """治理成效:从全量 history.jsonl 聚合,给仪表盘「治理成果」组用。

        - cleanup_total / cleanup_by_reason: move_to_trash 的 count 累加(不限 status,
          与 trash-stats.deleted_total 同口径),按 detail.reason 分桶;无 reason 归
          uncategorized(早期未分类)。
        - update/install/copy/attach: 对应 op 的 count 累加(处理的 skill 个数)。
        - scan_total: scan_run 操作次数(= 点了多少次"开始整理")。
        reason 是 2026-07 才加的埋点,历史 319 次 move_to_trash 无 reason → 全归 uncategorized。
        """
        hist_file = STATE_DIR / "history.jsonl"
        cleanup_by_reason = Counter()
        cleanup_total = 0
        update_total = install_total = copy_total = attach_total = 0
        scan_total = 0
        try:
            for line in hist_file.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    e = json.loads(line)
                except json.JSONDecodeError:
                    continue
                op = e.get("op", "")
                cnt = e.get("count") or 0
                detail = e.get("detail") or {}
                if op == "move_to_trash":
                    cleanup_total += cnt
                    cleanup_by_reason[detail.get("reason") or "uncategorized"] += cnt
                elif op == "update":
                    update_total += cnt
                elif op == "install":
                    install_total += cnt
                elif op == "copy":
                    copy_total += cnt
                elif op == "attach_source":
                    attach_total += cnt
                elif op == "scan_run":
                    scan_total += 1
        except FileNotFoundError:
            pass
        self._json_response({
            "cleanup_total": cleanup_total,
            "cleanup_by_reason": dict(cleanup_by_reason),
            "update_total": update_total,
            "install_total": install_total,
            "copy_total": copy_total,
            "attach_total": attach_total,
            "scan_total": scan_total,
        })

    def _serve_history(self):
        hist_file = STATE_DIR / "history.jsonl"
        query = parse_qs(urlparse(self.path).query)
        try:
            limit = max(1, min(500, int(query.get("limit", ["50"])[0])))
        except Exception:
            limit = 50
        hide = set(filter(None, query.get("hide", [""])[0].split(",")))
        try:
            lines = hist_file.read_text(encoding="utf-8").strip().split("\n")
            entries = []
            for line in reversed(lines):  # 从最新往回取,先过滤后截断,避免噪音挤掉有用记录
                line = line.strip()
                if not line:
                    continue
                try:
                    e = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if e.get("op") in hide:
                    continue
                entries.append(e)
                if len(entries) >= limit:
                    break
            self._json_response(list(reversed(entries)))  # 反转回旧→新,兼容前端 .reverse()
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
