#!/usr/bin/env python3
"""Skill Dashboard — 可视化 skill-manager 数据的轻量 WebUI
零依赖，只用 Python 3 标准库。
"""

import json
import os
import re
import subprocess
import sys
import time
import webbrowser
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse

PORT = 3457
STATE_DIR = Path.home() / ".skill-manager" / "state"
SKILL_MGR = Path.home() / ".claude/skills/skill-manager/scripts/skill-mgr.sh"
HTML_FILE = Path(__file__).parent / "index.html"
CACHE_DIR = Path(__file__).parent / ".cache"
DIAG_LOG = Path(__file__).parent / ".cache" / "diag.log"


def _cache_path(target_path):
    """Get cache file path for a target."""
    # Use a safe filename from the target path
    safe = re.sub(r'[^\w]', '_', target_path)
    return CACHE_DIR / f"{safe}.json"


def load_cached_diagnosis(target_path):
    """Load cached diagnosis for a target, or None."""
    cp = _cache_path(target_path)
    if cp.exists():
        try:
            return json.loads(cp.read_text("utf-8"))
        except Exception:
            pass
    return None


def save_cached_diagnosis(target_path, data):
    """Save diagnosis result to cache."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cp = _cache_path(target_path)
    cp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def python_quick_check(target_path):
    """Python-only structure check — no bash, no skill-mgr.
    Returns: health_score, structure_issues, summary."""
    target_dir = Path(target_path)
    if not target_dir.is_dir():
        return None

    skills = []
    structure_issues = []
    no_desc = 0
    broken = 0
    symlinks = 0
    entities = 0

    for d in sorted(target_dir.iterdir()):
        if not d.is_dir() and not d.is_symlink():
            continue
        skill_md = d / "SKILL.md"
        name = d.name

        # Kind detection
        if d.is_symlink():
            if d.resolve().exists():
                kind = "symlink"
                symlinks += 1
            else:
                kind = "broken_symlink"
                broken += 1
                structure_issues.append({"name": name, "note": "broken symlink", "kind": "broken_symlink"})
        else:
            if not skill_md.exists():
                continue
            kind = "entity"
            entities += 1

        # Parse frontmatter
        description = ""
        has_fm = False
        oversized = False
        try:
            text = skill_md.read_text("utf-8", errors="ignore")
            if len(text.splitlines()) > 500:
                oversized = True
            if text.startswith("---"):
                has_fm = True
                end = text.find("---", 3)
                if end > 0:
                    fm = text[3:end]
                    for line in fm.splitlines():
                        line = line.strip()
                        if line.startswith("description:"):
                            description = line.split(":", 1)[1].strip().strip("'\"")
            else:
                structure_issues.append({"name": name, "note": "missing frontmatter", "kind": "no_frontmatter"})
        except Exception:
            structure_issues.append({"name": name, "note": "read error", "kind": "read_error"})

        if not description:
            no_desc += 1

        skills.append({
            "name": name,
            "description": description,
            "kind": kind,
            "has_frontmatter": has_fm,
            "oversized": oversized,
        })

    total = len(skills)

    # ── Independent upstream detection (no skill-mgr) ──
    upstream_sources = []
    for s in skills:
        skill_dir = target_dir / s["name"]
        repo = ""
        detected = False

        # 1) Try .git remote
        git_dir = skill_dir / ".git"
        if git_dir.exists():
            try:
                r = subprocess.run(
                    ["git", "-C", str(skill_dir), "remote", "get-url", "origin"],
                    capture_output=True, text=True, timeout=5,
                )
                if r.returncode == 0:
                    url = r.stdout.strip()
                    if "github.com" in url:
                        if url.startswith("git@github.com:"):
                            repo = url.replace("git@github.com:", "").replace(".git", "")
                        elif "github.com/" in url:
                            parts = url.split("github.com/")
                            if len(parts) > 1:
                                repo = parts[1].replace(".git", "")
                    upstream_sources.append({
                        "name": s["name"],
                        "repo": repo or url,
                        "status": "unknown",
                        "source": "git-remote",
                    })
                    detected = True
            except Exception:
                pass

        # 2) Fallback: skill-mgr source metadata (steal installs)
        if not detected:
            meta_file = skill_dir / ".skill-manager-source.env"
            if meta_file.exists():
                try:
                    for line in meta_file.read_text().splitlines():
                        if line.startswith("SKILL_SOURCE_URL="):
                            url = line.split("=", 1)[1].strip().strip('"').strip("'")
                            repo = ""
                            if "github.com" in url:
                                # Normalize github.com URLs to user/repo
                                clean = url.replace("https://", "").replace("http://", "").replace("github.com/", "")
                                repo = clean.split("/")[0] + "/" + clean.split("/")[1].split("?")[0].split("#")[0] if "/" in clean else clean
                            upstream_sources.append({
                                "name": s["name"],
                                "repo": repo or url,
                                "status": "unknown",
                                "source": "steal-meta",
                            })
                            detected = True
                            break
                except Exception:
                    pass

    # ── Cleanup candidates (independent rules) ──
    cleanup_candidates = []
    for s in skills:
        if s["kind"] == "broken_symlink":
            cleanup_candidates.append(s["name"])
        elif not s["has_frontmatter"]:
            cleanup_candidates.append(s["name"])
        elif not s["description"]:
            cleanup_candidates.append(s["name"])
        elif s.get("oversized"):
            cleanup_candidates.append(s["name"])

    # Health score (mirrors skill-mgr check.sh formula)
    score = 100
    # Quantity penalty: >20, -2 per extra (max -60)
    if total > 20:
        penalty = min((total - 20) * 2, 60)
        score -= penalty
    # Structure issue penalty: -3 each
    score -= len(structure_issues) * 3
    # Missing description: proportional, max -15
    if total > 0:
        desc_penalty = min(no_desc * 15 // total, 15)
        score -= desc_penalty
    # Oversized: -2 each
    oversized_count = sum(1 for s in skills if s.get("oversized"))
    score -= oversized_count * 2
    # Clamp
    score = max(0, min(100, score))

    # Accuracy estimate (mirrors skill-mgr)
    if total <= 5:
        accuracy = 96
    elif total <= 20:
        accuracy = 96 - (total - 5)
    else:
        accuracy = max(15, int(96 * (2.71828 ** (-0.005 * (total - 5) ** 1.3))))

    # Level
    if score >= 80:
        level = "green"
    elif score >= 50:
        level = "yellow"
    else:
        level = "red"

    return {
        "health_score": {
            "score": score,
            "level": level,
            "accuracy_estimate": accuracy,
        },
        "structure_issues": structure_issues,
        "overlap_groups": [],  # populated by frontend fallback
        "upstream_sources": upstream_sources,
        "cleanup_candidates": list(dict.fromkeys(cleanup_candidates)),  # dedup, preserve order
        "summary": {
            "total": total,
            "entities": entities,
            "symlinks": symlinks,
            "broken_symlinks": broken,
            "no_description": no_desc,
            "structure_issues": len(structure_issues),
            "oversized": oversized_count,
            "runtime_ready": entities - len(structure_issues),
        },
        "source": "python-quick-check",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }


class DashboardHandler(BaseHTTPRequestHandler):
    """Serve index.html and API endpoints."""

    def do_GET(self):
        path = urlparse(self.path).path

        if path == "/" or path == "/index.html":
            self._serve_file(HTML_FILE, "text/html; charset=utf-8")
        elif path == "/api/scan":
            self._serve_json(STATE_DIR / "latest-scan.json")
        elif path == "/api/health":
            self._serve_json(STATE_DIR / "latest-health.json")
        elif path == "/api/history":
            self._serve_history()
        elif path == "/api/targets":
            self._list_targets()
        elif path == "/api/fast-scan":
            self._fast_scan()
        elif path == "/api/quick-check":
            self._quick_check()
        elif path == "/api/diagnosis-status":
            self._diagnosis_status()
        elif path == "/api/export":
            self._export_skills()
        elif path == "/api/openapi":
            self._openapi()
        elif path == "/api/source/skills":
            self._list_source_skills()
        elif path == "/api/custom-sources":
            self._get_custom_sources()
        elif path.startswith("/api/skill/") and path.endswith("/content"):
            name = path.split("/")[3]
            self._serve_skill_content(name)
        else:
            self.send_error(404)

    def do_POST(self):
        path = urlparse(self.path).path
        if path == "/api/target":
            self._set_target()
        elif path == "/api/diagnose":
            self._diagnose()
        elif path == "/api/steal":
            self._steal_skill()
        elif path == "/api/custom-sources":
            self._add_custom_source()
        else:
            self.send_error(404)

    def do_DELETE(self):
        path = urlparse(self.path).path
        if path.startswith("/api/skill/"):
            name = path.split("/")[3]
            self._delete_skill(name)
        elif path == "/api/custom-sources":
            self._remove_custom_source()
        else:
            self.send_error(404)

    # ── API implementations ──

    def _serve_file(self, filepath, content_type):
        try:
            data = filepath.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        except FileNotFoundError:
            self.send_error(404, f"File not found: {filepath}")

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
            self.wfile.write(b'{"error": "state file not found, run skill-mgr scan first"}')

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

    def _serve_skill_content(self, name):
        """Return SKILL.md content for a named skill."""
        target = self._current_target()
        candidates = [Path(target) / name / "SKILL.md"]
        for skill_md in candidates:
            if skill_md.exists():
                content = skill_md.read_text(encoding="utf-8")
                self._json_response({"name": name, "content": content, "path": str(skill_md)})
                return
        self._json_response({"error": f"Skill '{name}' not found"}, status=404)

    def _export_skills(self):
        """Export current target's skills as JSON."""
        target = self._current_target()
        target_dir = Path(target)
        result = []
        if target_dir.is_dir():
            for d in sorted(target_dir.iterdir()):
                if not d.is_dir():
                    continue
                skill_md = d / "SKILL.md"
                if not skill_md.exists():
                    continue
                name = d.name
                description = ""
                category = ""
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
                                elif line.startswith("category:"):
                                    category = line.split(":", 1)[1].strip().strip("'\"")
                except Exception:
                    pass
                result.append({
                    "name": name,
                    "category": category,
                    "description": description,
                })
        self._json_response({
            "exported_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "target": target,
            "skills": result,
        })

    def _openapi(self):
        """Return simple API documentation."""
        self._json_response({
            "title": "Skill Dashboard API",
            "version": "1.0",
            "endpoints": [
                {"method": "GET", "path": "/api/fast-scan", "desc": "Instant skill list + classification"},
                {"method": "GET", "path": "/api/quick-check", "desc": "Health score + structure issues + upstream + cleanup"},
                {"method": "GET", "path": "/api/targets", "desc": "List available skill directories"},
                {"method": "GET", "path": "/api/export", "desc": "Export skill manifest as JSON"},
                {"method": "GET", "path": "/api/skill/{name}/content", "desc": "Read SKILL.md content"},
                {"method": "POST", "path": "/api/target", "desc": "Switch target directory"},
                {"method": "POST", "path": "/api/diagnose", "desc": "Trigger full diagnosis (needs skill-mgr)"},
                {"method": "POST", "path": "/api/steal", "desc": "Install skill from GitHub URL"},
                {"method": "DELETE", "path": "/api/skill/{name}", "desc": "Delete a skill"},
                {"method": "PATCH", "path": "/api/skill/{name}/update", "desc": "Update skill from upstream"},
            ],
        })

    def _list_source_skills(self):
        """Return skills in a given source directory (for穿透 browsing)."""
        from urllib.parse import parse_qs
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
        source_dir = Path(source_path)
        if not source_dir.is_dir():
            self._json_response({"error": f"not a dir: {source_path}"}, status=400)
            return

        result = []
        for d in sorted(source_dir.iterdir()):
            if not d.is_dir() and not d.is_symlink():
                continue
            skill_md = d / "SKILL.md"
            if not skill_md.exists():
                continue
            name = d.name
            description = ""
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
            result.append({
                "name": name,
                "description": description,
            })
        self._json_response({
            "source": str(source_dir).replace(str(Path.home()), "~"),
            "skills": result,
            "count": len(result),
        })

    def _get_custom_sources(self):
        """Return user-defined custom source paths."""
        self._json_response(self._load_custom_sources())

    def _add_custom_source(self):
        """Add a custom source path."""
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
        p = Path(new_path)
        if not p.exists():
            self._json_response({"error": f"path does not exist: {new_path}"}, status=400)
            return
        # Must have skills/ subdir or be a skills dir itself
        skills_dir = p / "skills" if p.name != "skills" else p
        if not skills_dir.is_dir():
            self._json_response({"error": f"no skills/ subdir found in {new_path}"}, status=400)
            return
        paths = self._load_custom_sources()
        if new_path not in paths:
            paths.append(new_path)
            self._save_custom_sources(paths)
        self._json_response({"ok": True, "path": new_path, "paths": paths})

    def _remove_custom_source(self):
        """Remove a custom source path."""
        from urllib.parse import parse_qs
        query = parse_qs(urlparse(self.path).query)
        rm_path = query.get("path", [""])[0]
        if not rm_path:
            self._json_response({"error": "missing path"}, status=400)
            return
        paths = self._load_custom_sources()
        if rm_path in paths:
            paths.remove(rm_path)
            self._save_custom_sources(paths)
        self._json_response({"ok": True, "paths": paths})

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
            if not d.is_dir():
                continue
            skill_md = d / "SKILL.md"
            if not skill_md.exists():
                continue
            name = d.name
            description = ""
            category = ""
            kind = "entity"
            # Quick frontmatter parse
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
                            elif line.startswith("category:"):
                                category = line.split(":", 1)[1].strip().strip("'\"")
            except Exception:
                pass
            # Check if symlink
            if d.is_symlink():
                kind = "symlink" if d.resolve().exists() else "broken_symlink"
            skills.append({
                "name": name,
                "description": description,
                "category": category,
                "kind": kind,
                "agent": "",
            })

        # Build scan-like response
        home = Path.home()
        rel = str(target_dir).replace(str(home), "~")
        result = {
            "target": {
                "path": rel,
                "label": target_dir.parent.name,
                "total": len(skills),
                "entities": len([s for s in skills if s["kind"] == "entity"]),
                "symlinks": len([s for s in skills if s["kind"] == "symlink"]),
                "broken_symlinks": len([s for s in skills if s["kind"] == "broken_symlink"]),
            },
            "installed": skills,
            "totals": {"skills": len(skills)},
            "sources": [],
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "scan_mode": "fast",
            "duration_ms": int((time.time() - start) * 1000),
        }
        self._json_response(result)

    def _quick_check(self):
        """Python-only structure check — instant, no bash."""
        target = self._current_target()
        result = python_quick_check(target)
        if result is None:
            self._json_response({"error": "target not found"}, status=400)
            return
        self._json_response(result)

    # ── Diagnosis state (background bash process) ──
    _diag_process = None
    _diag_target = ""
    _diag_start = 0
    _diag_phase = ""  # "scan" or "check"

    def _diagnose(self):
        """Trigger skill-mgr scan+check in background. Returns immediately."""
        if not SKILL_MGR.exists():
            self._json_response({"status": "error", "error": "skill-mgr 未安装，路径: " + str(SKILL_MGR)})
            return
        target = self._current_target()

        # Check if already running
        if DashboardHandler._diag_process and DashboardHandler._diag_process.poll() is None:
            elapsed = int((time.time() - DashboardHandler._diag_start) * 1000)
            if elapsed > 120000:
                DashboardHandler._diag_process.kill()
                DashboardHandler._diag_process = None
                self._json_response({"status": "error", "error": "诊断超时 (120s)，请重试"})
                return
            phase = DashboardHandler._diag_phase or "scan"
            self._json_response({"status": "running", "target": DashboardHandler._diag_target,
                                 "elapsed_ms": elapsed, "phase": phase})
            return

        env = os.environ.copy()
        env["SKILL_MANAGER_TARGET"] = target
        try:
            # Write output to log file instead of PIPE (avoids buffer deadlock)
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            log_f = open(DIAG_LOG, "w")
            DashboardHandler._diag_process = subprocess.Popen(
                ["bash", "-c",
                 f"echo '=== SCAN START ===' && "
                 f"SKILL_MANAGER_TARGET='{target}' bash '{SKILL_MGR}' scan && "
                 f"echo '=== CHECK START ===' && "
                 f"SKILL_MANAGER_TARGET='{target}' bash '{SKILL_MGR}' check && "
                 f"echo '=== DONE ==='"],
                stdout=log_f, stderr=subprocess.STDOUT, env=env,
            )
            DashboardHandler._diag_target = target
            DashboardHandler._diag_start = time.time()
            DashboardHandler._diag_phase = "scan"
            self._json_response({"status": "started", "target": target})
        except Exception as e:
            self._json_response({"status": "error", "error": str(e)})

    def _diagnosis_status(self):
        """Poll diagnosis progress. If done, cache and return results."""
        target = self._current_target()

        # If process is running, check if it just finished
        if DashboardHandler._diag_process and DashboardHandler._diag_process.poll() is not None:
            rc = DashboardHandler._diag_process.returncode
            DashboardHandler._diag_process = None

            if rc == 0:
                try:
                    health = json.loads((STATE_DIR / "latest-health.json").read_text())
                    scan_data = json.loads((STATE_DIR / "latest-scan.json").read_text())
                    cached = {
                        "health_score": health.get("health_score"),
                        "structure_issues": health.get("structure_issues", []),
                        "overlap_groups": health.get("overlap_groups", []),
                        "upstream_sources": health.get("upstream_sources", []),
                        "cleanup_candidates": health.get("cleanup_candidates", []),
                        "summary": health.get("summary", {}),
                        "sources": scan_data.get("sources", []),
                        "totals": scan_data.get("totals", {}),
                        "source": "skill-mgr",
                        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                    }
                    save_cached_diagnosis(target, cached)
                    cached["status"] = "done"
                    cached["duration_ms"] = int((time.time() - DashboardHandler._diag_start) * 1000)
                    self._json_response(cached)
                    return
                except Exception as e:
                    self._json_response({"status": "error", "error": f"解析失败: {e}"})
                    return
            else:
                # Read last few lines of log for error info
                err_hint = ""
                try:
                    err_hint = DIAG_LOG.read_text()[-500:]
                except Exception:
                    pass
                self._json_response({"status": "error", "error": f"skill-mgr 退出码 {rc}", "log": err_hint})
                return

        # Process still running — detect phase from log
        if DashboardHandler._diag_process and DashboardHandler._diag_process.poll() is None:
            elapsed = int((time.time() - DashboardHandler._diag_start) * 1000)
            # Detect phase from log
            phase = "scan"
            try:
                log_text = DIAG_LOG.read_text()
                if "CHECK START" in log_text:
                    phase = "check"
                    DashboardHandler._diag_phase = "check"
            except Exception:
                pass
            self._json_response({"status": "running", "target": DashboardHandler._diag_target,
                                 "elapsed_ms": elapsed, "phase": phase})
            return

        # No process — check cache
        cached = load_cached_diagnosis(target)
        if cached:
            cached["status"] = "cached"
            self._json_response(cached)
            return

        self._json_response({"status": "idle"})

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
        """Scan common locations + custom sources for skill directories."""
        home = Path.home()
        prefixes = [
            home / ".claude",
            home / ".agents",
            home / ".alice",
            home / ".cc-switch",
            home / ".codex",
            home / ".hermes",
            home / ".openclaw",
            home / ".qclaw",
            home / ".workbuddy",
            home / "Downloads",
            home / "hyperframes",
        ]
        projects_dir = home / "projects"
        if projects_dir.exists():
            for p in projects_dir.iterdir():
                if p.is_dir():
                    prefixes.append(p)
        # Add custom sources
        for cs in self._load_custom_sources():
            p = Path(cs)
            if p.is_dir() and p not in prefixes:
                prefixes.append(p)

        targets = []
        seen = set()
        current = self._current_target()
        for prefix in prefixes:
            skills_dir = prefix / "skills" if prefix.name != "skills" else prefix
            if skills_dir.is_dir() and str(skills_dir) not in seen:
                seen.add(str(skills_dir))
                count = sum(1 for d in skills_dir.iterdir()
                           if d.is_dir() and (d / "SKILL.md").exists())
                if count == 0:
                    continue
                rel = str(skills_dir).replace(str(home), "~")
                name = skills_dir.parent.name
                if "claude" in rel:
                    scope, agent = "global", "Claude Code"
                elif "codex" in rel:
                    scope, agent = "global", "Codex"
                elif "agents" in rel:
                    scope, agent = "global", "通用 Agents"
                elif "alice" in rel:
                    scope, agent = "global", "Alice"
                elif "cc-switch" in rel:
                    scope, agent = "global", "CC-Switch"
                elif "workbuddy" in rel:
                    scope, agent = "global", "WorkBuddy"
                elif "projects" in rel:
                    scope, agent = "project", name
                else:
                    scope, agent = "global", name
                targets.append({
                    "path": str(skills_dir),
                    "rel": rel,
                    "name": agent,
                    "scope": scope,
                    "count": count,
                    "is_current": str(skills_dir) == current,
                })
        targets.sort(key=lambda t: (0 if t["is_current"] else 1, -t["count"]))
        self._json_response(targets)

    def _current_target(self):
        """Read current target from dedicated state file, fallback to latest-scan.json, fallback to ~/.claude/skills."""
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
        # 2) Legacy: latest-scan.json from skill-mgr
        try:
            scan = json.loads((STATE_DIR / "latest-scan.json").read_text())
            tp = scan.get("target", {}).get("path", "")
            if tp.startswith("~"):
                tp = str(Path.home() / tp[2:])
            return tp
        except Exception:
            pass
        # 3) Fallback
        return str(Path.home() / ".claude/skills")

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
        # Also update legacy latest-scan.json for compatibility
        scan_file = STATE_DIR / "latest-scan.json"
        scan_data = {}
        if scan_file.exists():
            try:
                scan_data = json.loads(scan_file.read_text("utf-8"))
            except Exception:
                pass
        scan_data["target"] = {
            "path": rel,
            "label": Path(target_path).parent.name,
        }
        scan_file.write_text(json.dumps(scan_data, ensure_ascii=False, indent=2), encoding="utf-8")

        # Now do fast scan
        self._fast_scan()

    def _delete_skill(self, name):
        """Delete a skill directory."""
        import shutil
        # Resolve skill path from scan data
        skill_dir = self._resolve_skill_dir(name)
        if not skill_dir:
            self._json_response({"error": f"Skill '{name}' not found"}, status=404)
            return
        try:
            shutil.rmtree(skill_dir)
            self._json_response({"ok": True, "name": name, "removed": str(skill_dir)})
        except Exception as e:
            self._json_response({"error": str(e)}, status=500)

    def _resolve_skill_dir(self, name):
        """Find skill directory on disk. Uses current target first."""
        # 1) Current target (always check first)
        target = self._current_target()
        candidates = [Path(target) / name]
        # 2) Fallback: ~/.claude/skills
        candidates.append(Path.home() / ".claude/skills" / name)
        # 3) Fallback: from latest-scan.json if different
        try:
            scan = json.loads((STATE_DIR / "latest-scan.json").read_text())
            tp = scan.get("target", {}).get("path", "")
            if tp.startswith("~"):
                tp = str(Path.home() / tp[2:])
            p = Path(tp) / name
            if str(p) != str(candidates[0]):
                candidates.append(p)
        except Exception:
            pass
        for d in candidates:
            if d.exists():
                return d
        return None

    def do_PATCH(self):
        """Handle skill update actions."""
        path = urlparse(self.path).path
        # Read body
        length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(length).decode('utf-8') if length else '{}'
        try:
            data = json.loads(body)
        except Exception:
            data = {}

        if path.startswith("/api/skill/") and path.endswith("/update"):
            name = path.split("/")[3]
            self._update_upstream(name)
        elif path.startswith("/api/skill/") and path.endswith("/fix"):
            name = path.split("/")[3]
            action = data.get("action", "")
            self._fix_skill(name, action)
        else:
            self.send_error(404)

    def _steal_skill(self):
        """Install a skill via skill-mgr steal."""
        if not SKILL_MGR.exists():
            self._json_response({"status": "error", "error": "skill-mgr 未安装，路径: " + str(SKILL_MGR)})
            return
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
        env = os.environ.copy()
        env["SKILL_MANAGER_TARGET"] = target
        try:
            cmd = ["bash", str(SKILL_MGR), "steal", source, "--yes"]
            if skill_name:
                cmd.append(skill_name)
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=60, env=env)
            ok = r.returncode == 0
            result = {"ok": ok, "source": source}
            if ok:
                result["output"] = r.stdout[-500:] if r.stdout else "installed"
                # Re-scan after install
                python_quick_check(target)
            else:
                result["error"] = r.stderr[-500:] if r.stderr else r.stdout[-500:] or "steal failed"
            self._json_response(result)
        except Exception as e:
            self._json_response({"error": str(e)}, status=500)

    def _update_upstream(self, name):
        """Trigger skill-mgr steal to update from upstream."""
        if not SKILL_MGR.exists():
            self._json_response({"status": "error", "error": "skill-mgr 未安装，路径: " + str(SKILL_MGR)})
            return
        try:
            env = os.environ.copy()
            env["SKILL_MANAGER_TARGET"] = self._current_target()
            # Find source metadata
            skill_dir = self._resolve_skill_dir(name)
            meta_file = skill_dir / ".skill-manager-source.env" if skill_dir else None
            source_url = ""
            if meta_file and meta_file.exists():
                for line in meta_file.read_text().splitlines():
                    if line.startswith("SKILL_SOURCE_URL="):
                        source_url = line.split("=", 1)[1].strip('"').strip("'")
            if not source_url:
                self._json_response({"error": "No upstream source tracked"}, status=400)
                return
            r = subprocess.run(
                ["bash", str(SKILL_MGR), "steal", source_url, "--yes"],
                capture_output=True, text=True, timeout=120, env=env,
            )
            self._json_response({"ok": r.returncode == 0, "name": name, "output": r.stdout[-500:] if r.stdout else ""})
        except Exception as e:
            self._json_response({"error": str(e)}, status=500)

    def _fix_skill(self, name, action):
        """Fix a skill issue."""
        if action == "delete":
            self._delete_skill(name)
            return
        elif action == "add_frontmatter":
            skill_dir = self._resolve_skill_dir(name)
            if not skill_dir:
                self._json_response({"error": "not found"}, status=404)
                return
            skill_md = skill_dir / "SKILL.md"
            if not skill_md.exists():
                self._json_response({"error": "no SKILL.md"}, status=400)
                return
            content = skill_md.read_text("utf-8")
            if not content.startswith("---"):
                skill_md.write_text(f"---\nname: {name}\ndescription: ''\n---\n\n{content}", encoding="utf-8")
                self._json_response({"ok": True, "name": name, "fixed": "added frontmatter"})
            else:
                self._json_response({"ok": False, "error": "already has frontmatter"})
            return
        self._json_response({"error": f"unknown action: {action}"}, status=400)

    def _json_response(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        """Quieter logging — only show API calls, not static file requests."""
        msg = fmt % args
        if "/api/" in msg or "POST" in msg or "DELETE" in msg:
            sys.stderr.write(f"  {msg}\n")


def main():
    if not STATE_DIR.exists():
        print(f"⚠ State dir not found: {STATE_DIR}")
        print("  Run 'skill-mgr scan' first to generate data.")
        print(f"  Creating {STATE_DIR}...")
        STATE_DIR.mkdir(parents=True, exist_ok=True)

    server = HTTPServer(("127.0.0.1", PORT), DashboardHandler)
    url = f"http://localhost:{PORT}"
    print(f"🚀 Skill Dashboard running at {url}")
    print(f"   Data source: {STATE_DIR}")
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
