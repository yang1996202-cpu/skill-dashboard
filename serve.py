#!/usr/bin/env python3
"""Skill Dashboard — 可视化 skill-manager 数据的轻量 WebUI
零依赖，只用 Python 3 标准库。
"""

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
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse, parse_qs

PORT = 3457
STATE_DIR = Path.home() / ".skill-manager" / "state"
HTML_FILE = Path(__file__).parent / "index.html"
CACHE_DIR = Path(__file__).parent / ".cache"
DIAG_LOG = Path(__file__).parent / ".cache" / "diag.log"


def _cache_path(target_path):
    """Get cache file path for a target. Resolves ~ and relative paths first."""
    # Normalize: expand ~ and resolve to absolute path for consistent keys
    p = Path(target_path).expanduser().resolve()
    safe = re.sub(r'[^\w]', '_', str(p))
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


# ── GitHub URL parsing ──
GITHUB_HTTPS_RE = re.compile(
    r"^https?://github\.com/(?P<owner>[^/]+)/(?P<repo>[^/]+?)(?:\.git)?"
    r"(?:/tree/(?P<ref>[^/]+)(?:/(?P<subdir>.+))?)?"
    r"/?$"
)
GITHUB_SSH_RE = re.compile(
    r"^git@github\.com:(?P<owner>[^/]+)/(?P<repo>[^/]+?)(?:\.git)?$"
)


def parse_github_url(url):
    """Parse a GitHub URL into (owner, repo, ref, subdir, clean_url).
    Supports https:// and git@ formats. Returns None if not valid.
    """
    url = url.strip()
    m = GITHUB_HTTPS_RE.match(url)
    if m:
        owner = m.group("owner")
        repo = m.group("repo")
        ref = m.group("ref") or "main"
        subdir = m.group("subdir") or ""
        clean = f"https://github.com/{owner}/{repo}"
        if subdir:
            clean += f"/tree/{ref}/{subdir}"
        return owner, repo, ref, subdir, clean
    m = GITHUB_SSH_RE.match(url)
    if m:
        owner = m.group("owner")
        repo = m.group("repo")
        return owner, repo, "main", "", f"https://github.com/{owner}/{repo}"
    return None


# ── Source metadata I/O ──
def write_source_metadata(skill_dir, repo, ref, subdir, url, commit):
    """Write .skill-manager-source.env to record upstream info."""
    meta_file = Path(skill_dir) / ".skill-manager-source.env"
    lines = [
        f"SKILL_SOURCE_PROVIDER=github",
        f"SKILL_SOURCE_REPO={repo}",
        f"SKILL_SOURCE_REF={ref}",
        f"SKILL_SOURCE_SUBDIR={subdir}",
        f"SKILL_SOURCE_URL={url}",
        f"SKILL_SOURCE_INSTALLED_COMMIT={commit}",
    ]
    meta_file.write_text("\n".join(lines) + "\n", encoding="utf-8")


def read_source_metadata(skill_dir):
    """Read .skill-manager-source.env. Returns dict or None.
    Supports both skill-mgr format (repo=, ref=) and Dashboard format (SKILL_SOURCE_REPO=).
    """
    meta_file = Path(skill_dir) / ".skill-manager-source.env"
    if not meta_file.exists():
        return None
    result = {}
    for line in meta_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            result[k] = v.strip('"').strip("'")
    # Normalize: support both skill-mgr short keys and Dashboard long keys
    normalized = {}
    key_map = {
        "SKILL_SOURCE_REPO": "repo",
        "SKILL_SOURCE_REF": "ref",
        "SKILL_SOURCE_SUBDIR": "subdir",
        "SKILL_SOURCE_URL": "source_url",
        "SKILL_SOURCE_INSTALLED_COMMIT": "installed_commit",
        "SKILL_SOURCE_PROVIDER": "provider",
    }
    for long_key, short_key in key_map.items():
        if long_key in result:
            normalized[short_key] = result[long_key]
        elif short_key in result:
            normalized[short_key] = result[short_key]
    # Also expose long keys for convenience
    for long_key, short_key in key_map.items():
        if short_key in normalized:
            result[long_key] = normalized[short_key]
    return result


# ── Snapshot ──
def create_snapshot(skill_dir):
    """Create a timestamped backup of a skill directory."""
    skill_dir = Path(skill_dir)
    if not skill_dir.exists():
        return None
    snap_dir = skill_dir.parent / ".snapshots"
    snap_dir.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%d_%H%M%S")
    snap_path = snap_dir / f"{skill_dir.name}_{ts}"
    shutil.copytree(skill_dir, snap_path)
    return str(snap_path)


# ── Install skill from GitHub (pure Python) ──
def install_skill(source_url, target_path, preferred_name=None):
    """Install a skill from a GitHub URL. Pure Python, no skill-mgr.

    Steps:
      1. Parse GitHub URL (owner/repo/ref/subdir)
      2. git clone --depth 1 to temp dir
      3. Find SKILL.md (handle subdirectories)
      4. If target exists, create snapshot
      5. shutil.copytree to target
      6. Write .skill-manager-source.env

    Returns: {"ok": bool, "name": str, "output": str, "error": str, "snapshot": str}
    """

    parsed = parse_github_url(source_url)
    if not parsed:
        return {"ok": False, "error": f"不是有效的 GitHub URL: {source_url}"}
    owner, repo, ref, subdir, clean_url = parsed

    # Check git availability
    git_check = subprocess.run(["git", "--version"], capture_output=True, text=True)
    if git_check.returncode != 0:
        return {"ok": False, "error": "当前环境缺少 git，无法从 GitHub 安装"}

    tmp_root = tempfile.mkdtemp(prefix="skill_install_")
    clone_dir = Path(tmp_root) / "repo"

    try:
        # Clone
        clone_url = f"https://github.com/{owner}/{repo}.git"
        branch_args = ["--branch", ref] if ref else []
        clone_cmd = ["git", "clone", "--depth", "1"] + branch_args + [clone_url, str(clone_dir)]
        clone_res = subprocess.run(clone_cmd, capture_output=True, text=True, timeout=60)
        if clone_res.returncode != 0:
            shutil.rmtree(tmp_root, ignore_errors=True)
            return {"ok": False, "error": f"git clone 失败: {clone_res.stderr[-300:] or clone_res.stdout[-300:]}"}

        # Find SKILL.md
        search_dir = clone_dir / subdir if subdir else clone_dir
        candidates = []
        if subdir and (search_dir / "SKILL.md").exists():
            candidates = [search_dir]
        else:
            for d in sorted(clone_dir.rglob("SKILL.md")):
                candidates.append(d.parent)

        if not candidates:
            shutil.rmtree(tmp_root, ignore_errors=True)
            return {"ok": False, "error": "仓库里没有找到 SKILL.md"}

        # Select skill directory
        if len(candidates) == 1:
            selected_dir = candidates[0]
        else:
            # Multiple skills — try preferred_name match
            if preferred_name:
                for c in candidates:
                    if c.name == preferred_name:
                        selected_dir = c
                        break
                else:
                    names = ", ".join(c.name for c in candidates[:5])
                    shutil.rmtree(tmp_root, ignore_errors=True)
                    return {"ok": False, "error": f"仓库里有多个 skill，请指定名称。找到: {names}"}
            else:
                names = ", ".join(c.name for c in candidates[:5])
                shutil.rmtree(tmp_root, ignore_errors=True)
                return {"ok": False, "error": f"仓库里有多个 skill，请指定名称。找到: {names}"}

        selected_name = preferred_name or selected_dir.name
        selected_rel = str(selected_dir.relative_to(clone_dir)) if selected_dir != clone_dir else ""

        # Get installed commit
        commit_res = subprocess.run(
            ["git", "-C", str(clone_dir), "log", "-1", "--format=%H", "--", selected_rel],
            capture_output=True, text=True, timeout=10,
        )
        installed_commit = commit_res.stdout.strip() or ""
        if not installed_commit:
            commit_res = subprocess.run(
                ["git", "-C", str(clone_dir), "rev-parse", "HEAD"],
                capture_output=True, text=True, timeout=10,
            )
            installed_commit = commit_res.stdout.strip()

        target = Path(target_path)
        dest_dir = target / selected_name

        # Snapshot if exists
        snapshot_path = None
        if dest_dir.exists() or dest_dir.is_symlink():
            snapshot_path = create_snapshot(dest_dir)

        # Copy
        if dest_dir.exists() or dest_dir.is_symlink():
            if dest_dir.is_symlink():
                dest_dir.unlink()
            elif dest_dir.is_dir():
                shutil.rmtree(dest_dir)
            else:
                dest_dir.unlink()
        shutil.copytree(selected_dir, dest_dir)

        # Write metadata
        write_source_metadata(dest_dir, f"{owner}/{repo}", ref, selected_rel, clean_url, installed_commit)

        output = f"安装到 {target_path}/{selected_name}\n来源: {owner}/{repo}@{ref}"
        if selected_rel:
            output += f"\n子目录: {selected_rel}"
        output += f"\n提交: {installed_commit[:7]}"
        if snapshot_path:
            output += f"\n快照: {snapshot_path}"

        shutil.rmtree(tmp_root, ignore_errors=True)
        return {
            "ok": True,
            "name": selected_name,
            "output": output,
            "snapshot": snapshot_path,
        }

    except Exception as e:
        shutil.rmtree(tmp_root, ignore_errors=True)
        return {"ok": False, "error": str(e)}


# ── Check upstream status (pure Python, no gh CLI) ──
def check_upstream_status(skill_dir):
    """Check if a skill is behind its upstream GitHub source.
    Returns: {"status": "current"|"outdated"|"unknown", "installed_commit": str, "latest_commit": str, "repo": str, "ahead_by": int, "error": str}
    """
    meta = read_source_metadata(skill_dir)
    if not meta:
        # Try .git remote
        git_dir = Path(skill_dir) / ".git"
        if git_dir.exists():
            try:
                r = subprocess.run(
                    ["git", "-C", str(skill_dir), "remote", "get-url", "origin"],
                    capture_output=True, text=True, timeout=5,
                )
                if r.returncode == 0:
                    url = r.stdout.strip()
                    parsed = parse_github_url(url)
                    if parsed:
                        owner, repo, ref, subdir, clean_url = parsed
                        # Get local HEAD
                        lr = subprocess.run(
                            ["git", "-C", str(skill_dir), "rev-parse", "HEAD"],
                            capture_output=True, text=True, timeout=5,
                        )
                        local_commit = lr.stdout.strip() if lr.returncode == 0 else ""
                        # Query GitHub API for latest
                        latest = _github_latest_commit(f"{owner}/{repo}", ref, subdir)
                        if latest:
                            if local_commit and latest == local_commit:
                                return {"status": "current", "installed_commit": local_commit, "latest_commit": latest, "repo": f"{owner}/{repo}", "ahead_by": 0}
                            else:
                                return {"status": "outdated", "installed_commit": local_commit, "latest_commit": latest, "repo": f"{owner}/{repo}", "ahead_by": None}
                        return {"status": "unknown", "installed_commit": local_commit, "latest_commit": "", "repo": f"{owner}/{repo}", "error": "无法查询 GitHub API"}
            except Exception:
                pass
        return {"status": "unknown", "error": "没有来源记录"}

    repo = meta.get("SKILL_SOURCE_REPO", "")
    ref = meta.get("SKILL_SOURCE_REF", "main")
    subdir = meta.get("SKILL_SOURCE_SUBDIR", "")
    installed_commit = meta.get("SKILL_SOURCE_INSTALLED_COMMIT", "")
    url = meta.get("SKILL_SOURCE_URL", "")

    if not repo:
        return {"status": "unknown", "error": "来源记录不完整"}

    latest = _github_latest_commit(repo, ref, subdir)
    if not latest:
        return {"status": "unknown", "installed_commit": installed_commit, "latest_commit": "", "repo": repo, "error": "GitHub API 查询失败"}

    if installed_commit and latest == installed_commit:
        return {"status": "current", "installed_commit": installed_commit, "latest_commit": latest, "repo": repo, "ahead_by": 0}
    else:
        # Try to get ahead_by via compare API
        ahead_by = _github_compare_ahead_by(repo, installed_commit, latest)
        return {"status": "outdated", "installed_commit": installed_commit, "latest_commit": latest, "repo": repo, "ahead_by": ahead_by}


# ── GitHub API helpers with rate-limit protection ──
_github_cache = {}  # (url,) -> (timestamp, result)
_github_cache_ttl = 300  # 5 minutes
_github_rate_limited = False  # global flag: stop querying after hitting rate limit


def _github_api_get(url):
    """Fetch GitHub API with TTL cache and rate-limit detection.
    Returns (data, rate_limited_bool).
    """
    global _github_rate_limited

    # Check cache
    now = time.time()
    cached = _github_cache.get(url)
    if cached and (now - cached[0]) < _github_cache_ttl:
        return cached[1], False

    # If we already hit rate limit this session, skip
    if _github_rate_limited:
        return None, True

    try:
        req = urllib.request.Request(url, headers={"User-Agent": "skill-dashboard"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw = resp.read().decode("utf-8")
            # Check remaining rate limit from response headers
            remaining = resp.headers.get("X-RateLimit-Remaining", "")
            if remaining == "0":
                _github_rate_limited = True
            data = json.loads(raw)
            _github_cache[url] = (now, data)
            return data, False
    except urllib.error.HTTPError as e:
        if e.code == 403 or e.code == 429:
            _github_rate_limited = True
        return None, True
    except Exception:
        return None, False


def _github_latest_commit(repo, ref="main", subdir=""):
    """Query GitHub API for latest commit on a ref/path. Cached + rate-limit protected."""
    if subdir:
        url = f"https://api.github.com/repos/{repo}/commits?path={urllib.parse.quote(subdir)}&sha={urllib.parse.quote(ref)}&per_page=1"
    else:
        url = f"https://api.github.com/repos/{repo}/commits/{urllib.parse.quote(ref)}"
    data, limited = _github_api_get(url)
    if limited or data is None:
        return ""
    if isinstance(data, list):
        return data[0].get("sha", "") if data else ""
    return data.get("sha", "")


def _github_compare_ahead_by(repo, base, head):
    """Query GitHub compare API for commits ahead. Cached + rate-limit protected."""
    url = f"https://api.github.com/repos/{repo}/compare/{base}...{head}"
    data, limited = _github_api_get(url)
    if limited or data is None:
        return None
    return data.get("ahead_by")


# ── Update skill from upstream ──
def update_skill(skill_name, target_path):
    """Update a skill by re-installing from its tracked upstream source.
    Returns: {"ok": bool, "name": str, "output": str, "error": str}
    """
    skill_dir = Path(target_path) / skill_name
    meta = read_source_metadata(skill_dir)
    if meta:
        url = meta.get("SKILL_SOURCE_URL", "")
        if url:
            return install_skill(url, target_path, preferred_name=skill_name)

    # Fallback: try .git remote
    git_dir = skill_dir / ".git"
    if git_dir.exists():
        try:
            r = subprocess.run(
                ["git", "-C", str(skill_dir), "remote", "get-url", "origin"],
                capture_output=True, text=True, timeout=5,
            )
            if r.returncode == 0:
                url = r.stdout.strip()
                parsed = parse_github_url(url)
                if parsed:
                    return install_skill(url, target_path, preferred_name=skill_name)
        except Exception:
            pass

    return {"ok": False, "error": "没有找到上游来源记录，无法更新"}


# ── Diagnosis state (module-level, protected by lock) ──
_diag_lock = threading.Lock()
_diag_process = None
_diag_target = ""
_diag_start = 0
_diag_phase = ""


class DashboardHandler(BaseHTTPRequestHandler):
    """Serve index.html and API endpoints."""

    @staticmethod
    def _validate_skill_name(name):
        """Sanitize skill name from URL. Rejects path traversal attempts."""
        if not name or '..' in name or '/' in name or '\\' in name:
            return None
        if name.startswith('.') or name.startswith('-'):
            return None
        # Allow letters, digits, hyphens, underscores, dots, @, +
        if not re.match(r'^[a-zA-Z0-9._@+\-]+$', name):
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
            name = self._validate_skill_name(path.split("/")[3])
            if not name:
                self.send_error(400, "Invalid skill name")
            else:
                self._serve_skill_content(name)
        elif path.startswith("/api/skill/") and path.endswith("/upstream"):
            name = self._validate_skill_name(path.split("/")[3])
            if not name:
                self.send_error(400, "Invalid skill name")
            else:
                self._check_skill_upstream(name)
        else:
            self.send_error(404)

    def do_POST(self):
        if not self._check_csrf():
            self._csrf_reject()
            return
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
        if not self._check_csrf():
            self._csrf_reject()
            return
        path = urlparse(self.path).path
        if path.startswith("/api/skill/"):
            name = self._validate_skill_name(path.split("/")[3])
            if not name:
                self.send_error(400, "Invalid skill name")
            else:
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
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            self.send_header("Pragma", "no-cache")
            self.send_header("Expires", "0")
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
            self.wfile.write(b'{"error": "state file not found, switch target to generate data"}')

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

    def _check_skill_upstream(self, name):
        """Check upstream status for a single skill."""
        target = self._current_target()
        skill_dir = Path(target) / name
        if not skill_dir.exists():
            self._json_response({"error": f"Skill '{name}' not found"}, status=404)
            return
        result = check_upstream_status(skill_dir)
        result["name"] = name
        self._json_response(result)

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
            "version": "2.0",
            "endpoints": [
                {"method": "GET", "path": "/api/fast-scan", "desc": "Instant skill list + classification"},
                {"method": "GET", "path": "/api/quick-check", "desc": "Health score + structure issues + upstream + cleanup"},
                {"method": "GET", "path": "/api/targets", "desc": "List available skill directories"},
                {"method": "GET", "path": "/api/export", "desc": "Export skill manifest as JSON"},
                {"method": "GET", "path": "/api/skill/{name}/content", "desc": "Read SKILL.md content"},
                {"method": "GET", "path": "/api/skill/{name}/upstream", "desc": "Check upstream status for a skill"},
                {"method": "POST", "path": "/api/target", "desc": "Switch target directory"},
                {"method": "POST", "path": "/api/diagnose", "desc": "Trigger full diagnosis (Python-only)"},
                {"method": "POST", "path": "/api/steal", "desc": "Install skill from GitHub URL"},
                {"method": "DELETE", "path": "/api/skill/{name}", "desc": "Delete a skill"},
                {"method": "PATCH", "path": "/api/skill/{name}/update", "desc": "Update skill from upstream"},
            ],
        })

    def _list_source_skills(self):
        """Return skills in a given source directory (for穿透 browsing)."""
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

    # ── Diagnosis (uses module-level globals + lock) ──

    def _diagnose(self):
        """Trigger Python-only diagnosis in background. No skill-mgr needed."""
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
        global _diag_process
        target = self._current_target()

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
        """Scan common locations + nested subdirs + custom sources for skill directories.
        Mirrors skill-mgr scan breadth: standard paths, deep subdirs, project nests.
        """
        home = Path.home()

        # Phase 1: Collect candidate directories
        candidates = []
        seen_paths = set()

        def add_dir(d):
            if d.is_dir() and str(d) not in seen_paths:
                seen_paths.add(str(d))
                candidates.append(d)

        # 1A) Standard agent prefixes — scan prefix/skills AND one level of subdirs
        standard_prefixes = [
            home / ".claude",
            home / ".agents",
            home / ".alice",
            home / ".cc-switch",
            home / ".codex",
            home / ".hermes",
            home / ".openclaw",
            home / ".qclaw",
            home / ".workbuddy",
            home / ".codebuddy",
            home / ".cursor",
            home / ".cola",
            home / "Downloads",
            home / "hyperframes",
            home / "AI-Skills",
            home / ".config" / "opencode",
            home / "Documents",
        ]
        for prefix in standard_prefixes:
            if not prefix.exists():
                continue
            # Direct skills/ dir
            add_dir(prefix / "skills")
            # One-level subdirs under skills/ (e.g. skills/gstack, skills/openclaw-imports)
            skills_dir = prefix / "skills"
            if skills_dir.is_dir():
                for sub in skills_dir.iterdir():
                    if sub.is_dir():
                        add_dir(sub)
            # Special nested patterns (e.g. hermes/hermes-agent/skills)
            for deep in ["hermes-agent", "skills-marketplace", "connectors-marketplace",
                         "extensions", "workspaces", "backups", "skill-backups"]:
                deep_dir = prefix / deep
                if deep_dir.is_dir():
                    for sub in deep_dir.iterdir():
                        if sub.is_dir():
                            add_dir(sub)
                            # One more level for hermes-agent/skills/xxx
                            for sub2 in sub.iterdir():
                                if sub2.is_dir():
                                    add_dir(sub2)

        # 1B) Projects — scan projects/*/.claude/skills etc.
        projects_dir = home / "projects"
        if projects_dir.exists():
            for p in projects_dir.iterdir():
                if not p.is_dir():
                    continue
                add_dir(p / ".claude" / "skills")
                add_dir(p / "skills")
                # Also scan project root as a prefix
                add_dir(p / ".agents" / "skills")
                add_dir(p / ".codex" / "skills")

        # 1C) Downloads — scan for .claude/skills nests
        downloads = home / "Downloads"
        if downloads.is_dir():
            for d in downloads.iterdir():
                if d.is_dir():
                    add_dir(d / ".claude" / "skills")
                    add_dir(d / "skills")

        # 1D) Custom sources
        for cs in self._load_custom_sources():
            p = Path(cs)
            if p.is_dir():
                add_dir(p)

        # Phase 2: Filter to actual skill directories
        targets = []
        current = self._current_target()
        for skills_dir in candidates:
            count = sum(1 for d in skills_dir.iterdir()
                       if d.is_dir() and (d / "SKILL.md").exists())
            if count == 0:
                continue
            rel = str(skills_dir).replace(str(home), "~")
            name = skills_dir.name if skills_dir.name != "skills" else skills_dir.parent.name
            # Agent label detection
            rel_lower = rel.lower()
            if "claude" in rel_lower and ".claude" in rel_lower:
                agent = "Claude Code"
            elif "codex" in rel_lower:
                agent = "Codex"
            elif "agents" in rel_lower:
                agent = "通用 Agents"
            elif "alice" in rel_lower:
                agent = "Alice"
            elif "cc-switch" in rel_lower:
                agent = "CC-Switch"
            elif "workbuddy" in rel_lower:
                agent = "WorkBuddy"
            elif "codebuddy" in rel_lower:
                agent = "CodeBuddy"
            elif "hermes" in rel_lower:
                agent = "Hermes"
            elif "cursor" in rel_lower:
                agent = "Cursor"
            elif "openclaw" in rel_lower:
                agent = "OpenClaw"
            elif "qclaw" in rel_lower:
                agent = "QClaw"
            elif "cola" in rel_lower:
                agent = "Cola"
            elif "downloads" in rel_lower:
                agent = "Downloads"
            elif "projects" in rel_lower:
                agent = "项目: " + skills_dir.parent.name
            elif "hyperframes" in rel_lower:
                agent = "HyperFrames"
            else:
                agent = name
            scope = "project" if "projects/" in rel else "global"
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
        if not self._check_csrf():
            self._csrf_reject()
            return
        path = urlparse(self.path).path
        # Read body
        length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(length).decode('utf-8') if length else '{}'
        try:
            data = json.loads(body)
        except Exception:
            data = {}

        if path.startswith("/api/skill/") and path.endswith("/update"):
            name = self._validate_skill_name(path.split("/")[3])
            if not name:
                self.send_error(400, "Invalid skill name")
            else:
                self._update_upstream(name)
        elif path.startswith("/api/skill/") and path.endswith("/fix"):
            name = self._validate_skill_name(path.split("/")[3])
            if not name:
                self.send_error(400, "Invalid skill name")
            else:
                action = data.get("action", "")
                self._fix_skill(name, action)
        else:
            self.send_error(404)

    def _steal_skill(self):
        """Install a skill from GitHub URL — pure Python, no skill-mgr."""
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

    def _update_upstream(self, name):
        """Update a skill from its upstream source — pure Python."""
        target = self._current_target()
        result = update_skill(name, target)
        self._json_response(result)

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
        self.send_header("Access-Control-Allow-Origin", "*")
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
        print(f"  Creating {STATE_DIR}...")
        STATE_DIR.mkdir(parents=True, exist_ok=True)

    server = HTTPServer(("127.0.0.1", PORT), DashboardHandler)
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
