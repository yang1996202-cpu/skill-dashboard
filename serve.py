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
    _scan_commands,
    _scan_global_categories,
)
from skilldash.host_inspectors import discover_host_profiles, host_profile_summaries_by_agent, load_claude_plugin_state
from skilldash.overlap import (
    _find_same_name_duplicates,
    detect_cross_dir_overlaps,
)
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

def _skill_marker_exists(skill_dir):
    """True for a real SKILL.md or a broken SKILL.md symlink."""
    marker = Path(skill_dir) / "SKILL.md"
    return marker.exists() or marker.is_symlink()

def _is_skill_entry(skill_dir, include_broken=False):
    """Return whether a path is a skill entry the UI should manage.

    Broken symlinks do not have readable SKILL.md, but they are still cleanup
    residues in a skills directory and should be removable through the UI.
    """
    p = Path(skill_dir)
    if p.is_symlink():
        return include_broken or (p / "SKILL.md").exists()
    if not p.is_dir():
        return False
    return (p / "SKILL.md").exists() or (include_broken and (p / "SKILL.md").is_symlink())

def _skill_entry_kind(skill_dir):
    p = Path(skill_dir)
    marker = p / "SKILL.md"
    if p.is_symlink():
        return "symlink" if p.exists() else "broken_symlink"
    if marker.is_symlink() and not marker.exists():
        return "broken_skill_link"
    return "entity"

def python_quick_check(target_path):
    """Python-only structure check — no bash, no dashboard.
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
        if not _is_skill_entry(d, include_broken=True):
            continue
        skill_md = d / "SKILL.md"
        name = d.name

        # Kind detection
        kind = _skill_entry_kind(d)
        if kind == "symlink":
            symlinks += 1
        elif kind == "broken_symlink":
            broken += 1
            structure_issues.append({"name": name, "note": "broken symlink", "kind": "broken_symlink"})
        elif kind == "broken_skill_link":
            broken += 1
            structure_issues.append({"name": name, "note": "broken SKILL.md symlink", "kind": "broken_skill_link"})
        else:
            entities += 1

        # Parse frontmatter
        description = ""
        has_fm = False
        oversized = False
        if skill_md.exists():
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

    # ── Independent upstream detection (no dashboard) ──
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

        # 2) Fallback: dashboard source metadata (steal installs)
        if not detected:
            meta_file = skill_dir / ".skill-source.env"
            if not meta_file.exists():
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

        # 3) Fallback: Vercel skills CLI lock file (npx skills add)
        if not detected:
            vercel = read_vercel_skill_lock(skill_dir)
            if vercel:
                upstream_sources.append({
                    "name": s["name"],
                    "repo": vercel.get("source", ""),
                    "status": "unknown",
                    "source": "vercel-lock",
                })
                detected = True

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

    # Health score (mirrors dashboard check.sh formula)
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

    # Accuracy estimate (mirrors dashboard)
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

    # Content change detection
    content_changes = check_content_changes(target_path)

    return {
        "health_score": {
            "score": score,
            "level": level,
            "accuracy_estimate": accuracy,
        },
        "structure_issues": structure_issues,
        "upstream_sources": upstream_sources,
        "cleanup_candidates": list(dict.fromkeys(cleanup_candidates)),  # dedup, preserve order
        "content_changes": content_changes,
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
    """Write .skill-source.env to record upstream info."""
    meta_file = Path(skill_dir) / ".skill-source.env"
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
    """Read .skill-source.env. Returns dict or None.
    Supports short keys (repo=, ref=) and long keys (SKILL_SOURCE_REPO=).
    """
    meta_file = Path(skill_dir) / ".skill-source.env"
    if not meta_file.exists():
        # Backward compat: read old filename
        meta_file = Path(skill_dir) / ".skill-manager-source.env"
    if not meta_file.exists():
        return None
    result = {}
    for line in meta_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            result[k] = v.strip('"').strip("'")
    # Normalize: support both short keys and long keys
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


def read_vercel_skill_lock(skill_dir):
    """Read Vercel `skills` CLI lock file and return the entry for this skill.

    Vercel skills CLI (`npx skills add`) stores source metadata in
    ~/.agents/.skill-lock.json. Each entry tracks the GitHub source, ref,
    skill path, and the Git tree SHA of the installed skill folder.
    """
    try:
        lock_path = Path.home() / ".agents" / ".skill-lock.json"
        if not lock_path.exists():
            return None
        lock = json.loads(lock_path.read_text(encoding="utf-8"))
        skills = lock.get("skills", {})
        skill_name = Path(skill_dir).resolve().name
        entry = skills.get(skill_name)
        if not entry:
            return None
        if entry.get("sourceType") != "github" or not entry.get("source"):
            return None
        return entry
    except Exception:
        return None


def _normalize_skill_path_for_tree(skill_path):
    """Convert a SKILL.md path to its containing folder path for Git tree lookup."""
    if not skill_path:
        return ""
    p = skill_path.replace("\\", "/")
    if p.endswith("/SKILL.md"):
        p = p[:-9]
    elif p.endswith("SKILL.md"):
        p = p[:-8]
    return p.strip("/")


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
    """Install a skill from a GitHub URL. Pure Python, no dashboard.

    Steps:
      1. Parse GitHub URL (owner/repo/ref/subdir)
      2. git clone --depth 1 to temp dir
      3. Find SKILL.md (handle subdirectories)
      4. If target exists, create snapshot
      5. shutil.copytree to target
      6. Write .skill-source.env

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

        # Record content hash for change detection
        record_content_hash(dest_dir)

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

    Returns: {"status": "current"|"outdated"|"unknown", "installed_commit": str,
              "latest_commit": str, "repo": str, "ahead_by": int, "error": str,
              "source": "steal-meta"|"git-remote"|"vercel-lock"|"unknown"}
    """
    # Detect symlink so the UI can explain that the real source lives elsewhere.
    is_symlink = skill_dir.is_symlink()
    link_target = ""
    canonical_dir = str(skill_dir)
    if is_symlink:
        try:
            link_target = str(skill_dir.readlink())
            resolved = skill_dir.resolve()
            if resolved.exists() and (resolved / "SKILL.md").exists():
                canonical_dir = str(resolved)
        except Exception:
            pass

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
                        result = {"source": "git-remote", "repo": f"{owner}/{repo}", "is_symlink": is_symlink, "canonical_dir": canonical_dir}
                        if is_symlink and link_target:
                            result["link_target"] = link_target
                        if latest:
                            if local_commit and latest == local_commit:
                                result.update({"status": "current", "installed_commit": local_commit, "latest_commit": latest, "ahead_by": 0})
                            else:
                                result.update({"status": "outdated", "installed_commit": local_commit, "latest_commit": latest, "ahead_by": None})
                        else:
                            result.update({"status": "unknown", "installed_commit": local_commit, "latest_commit": "", "error": "无法查询 GitHub API"})
                        return result
            except Exception:
                pass
        # Try Vercel skills CLI lock file (npx skills add)
        vercel = read_vercel_skill_lock(skill_dir)
        if vercel:
            repo = vercel.get("source", "")
            ref = vercel.get("ref", "main")
            skill_path = vercel.get("skillPath", "")
            installed_hash = vercel.get("skillFolderHash", "")
            normalized_path = _normalize_skill_path_for_tree(skill_path)
            latest_hash = _github_tree_sha_for_path(repo, normalized_path, ref)
            result = {"source": "vercel-lock", "repo": repo, "is_symlink": is_symlink, "canonical_dir": canonical_dir}
            if is_symlink and link_target:
                result["link_target"] = link_target
            if latest_hash:
                if latest_hash == installed_hash:
                    result.update({"status": "current", "installed_commit": installed_hash, "latest_commit": latest_hash, "ahead_by": 0})
                else:
                    result.update({"status": "outdated", "installed_commit": installed_hash, "latest_commit": latest_hash, "ahead_by": None})
            else:
                result.update({"status": "unknown", "installed_commit": installed_hash, "latest_commit": "", "error": "无法查询 GitHub API"})
            return result

        return {"status": "unknown", "error": "没有来源记录", "source": "unknown", "is_symlink": is_symlink, "canonical_dir": canonical_dir}

    repo = meta.get("SKILL_SOURCE_REPO", "")
    ref = meta.get("SKILL_SOURCE_REF", "main")
    subdir = meta.get("SKILL_SOURCE_SUBDIR", "")
    installed_commit = meta.get("SKILL_SOURCE_INSTALLED_COMMIT", "")
    url = meta.get("SKILL_SOURCE_URL", "")

    result = {"source": "steal-meta", "repo": repo, "is_symlink": is_symlink, "canonical_dir": canonical_dir}
    if is_symlink and link_target:
        result["link_target"] = link_target

    if not repo:
        result.update({"status": "unknown", "error": "来源记录不完整"})
        return result

    latest = _github_latest_commit(repo, ref, subdir)
    if not latest:
        result.update({"status": "unknown", "installed_commit": installed_commit, "latest_commit": "", "error": "GitHub API 查询失败"})
        return result

    if installed_commit and latest == installed_commit:
        result.update({"status": "current", "installed_commit": installed_commit, "latest_commit": latest, "ahead_by": 0})
    else:
        # Try to get ahead_by via compare API
        ahead_by = _github_compare_ahead_by(repo, installed_commit, latest)
        result.update({"status": "outdated", "installed_commit": installed_commit, "latest_commit": latest, "ahead_by": ahead_by})
    return result


# ── GitHub API helpers with rate-limit protection ──
_github_cache = {}  # (url,) -> (timestamp, result)
_github_cache_ttl = 300  # 5 minutes
_targets_cache = None  # cached /api/targets response
_targets_cache_ts = 0  # timestamp of last targets cache
_github_rate_limit_reset = 0  # timestamp when rate limit resets; 0 means not limited


def _github_rate_limited_now():
    """Return True if we are currently inside a known GitHub rate-limit window."""
    global _github_rate_limit_reset
    if _github_rate_limit_reset == 0:
        return False
    if time.time() >= _github_rate_limit_reset:
        _github_rate_limit_reset = 0
        return False
    return True


def _load_github_token():
    """Load GitHub token from env var or project .env file.

    Never log or return the raw token. The token is only used to sign
    outbound GitHub API requests.
    """
    token = os.environ.get("GITHUB_TOKEN", "")
    if token:
        return token
    env_file = Path(__file__).parent / ".env"
    if env_file.exists():
        try:
            for line in env_file.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line.startswith("GITHUB_TOKEN="):
                    return line.split("=", 1)[1].strip().strip('"\'')
        except Exception:
            pass
    return ""


GITHUB_TOKEN = _load_github_token()


def _invalidate_runtime_caches():
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


def _github_api_get(url):
    """Fetch GitHub API with TTL cache and rate-limit detection.
    Returns (data, rate_limited_bool).
    """
    global _github_rate_limit_reset

    # Check cache
    now = time.time()
    cached = _github_cache.get(url)
    if cached and (now - cached[0]) < _github_cache_ttl:
        return cached[1], False

    # If we are inside a known rate-limit window, skip immediately
    if _github_rate_limited_now():
        return None, True

    try:
        headers = {"User-Agent": "skill-dashboard"}
        if GITHUB_TOKEN:
            headers["Authorization"] = f"token {GITHUB_TOKEN}"
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw = resp.read().decode("utf-8")
            # Check remaining rate limit from response headers
            remaining = resp.headers.get("X-RateLimit-Remaining", "")
            reset_ts = resp.headers.get("X-RateLimit-Reset", "")
            if remaining == "0":
                try:
                    _github_rate_limit_reset = int(reset_ts) if reset_ts else int(now + 3600)
                except Exception:
                    _github_rate_limit_reset = int(now + 3600)
            data = json.loads(raw)
            _github_cache[url] = (now, data)
            return data, False
    except urllib.error.HTTPError as e:
        limited = e.code == 403 or e.code == 429
        if limited:
            reset_ts = e.headers.get("X-RateLimit-Reset", "")
            try:
                _github_rate_limit_reset = int(reset_ts) if reset_ts else int(now + 3600)
            except Exception:
                _github_rate_limit_reset = int(now + 3600)
        return None, limited
    except Exception:
        return None, False


def _github_latest_commit(repo, ref="main", subdir=""):
    """Query GitHub API for the latest commit sha of a repo (or a sub-path).
    Cached + rate-limit protected via _github_api_get. Returns sha string or ''.
    """
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


def _github_tree_sha_for_path(repo, path, ref="main"):
    """Fetch the Git tree SHA for a directory path inside a repo.

    Returns the tree SHA of the folder at `path`, or the root tree SHA if
    `path` is empty. Used to compare against Vercel skills lock hashes.
    """
    commit_url = f"https://api.github.com/repos/{repo}/commits/{urllib.parse.quote(ref)}"
    data, limited = _github_api_get(commit_url)
    if limited or data is None:
        return ""
    tree_sha = data.get("commit", {}).get("tree", {}).get("sha", "")
    if not tree_sha:
        return ""

    if not path:
        return tree_sha

    parts = path.strip("/").split("/")
    current_sha = tree_sha
    for part in parts:
        url = f"https://api.github.com/repos/{repo}/git/trees/{current_sha}"
        data, limited = _github_api_get(url)
        if limited or data is None:
            return ""
        found = None
        for item in data.get("tree", []):
            if item.get("path") == part and item.get("type") == "tree":
                found = item
                break
        if not found:
            return ""
        current_sha = found.get("sha", "")
    return current_sha


# ── Update skill from upstream ──
def update_skill(skill_name, target_path):
    """Update a skill by re-installing from its tracked upstream source.
    Resolves symlinks to the canonical copy and understands Vercel skills lock.
    Returns: {"ok": bool, "name": str, "output": str, "error": str}
    """
    skill_dir = Path(target_path) / skill_name

    # If the entry is a symlink, update the canonical copy it points to.
    if skill_dir.is_symlink():
        try:
            resolved = skill_dir.resolve()
            if resolved.exists() and (resolved / "SKILL.md").exists():
                skill_dir = resolved
                target_path = str(resolved.parent)
        except Exception:
            pass

    meta = read_source_metadata(skill_dir)
    if meta:
        url = meta.get("SKILL_SOURCE_URL", "")
        if url:
            return install_skill(url, target_path, preferred_name=skill_name)

    # Fallback 1: try .git remote
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

    # Fallback 2: Vercel skills CLI lock file (npx skills add)
    vercel = read_vercel_skill_lock(skill_dir)
    if vercel:
        source = vercel.get("source", "")
        if source:
            if "/" in source and not source.startswith(("http://", "https://")):
                url = f"https://github.com/{source}"
            else:
                url = source
            return install_skill(url, target_path, preferred_name=skill_name)

    return {"ok": False, "error": "没有找到上游来源记录，无法更新"}


# ── Diagnosis state (module-level, protected by lock) ──
_diag_lock = threading.Lock()
_diag_process = None
_diag_target = ""
_diag_start = 0
_diag_phase = ""


class DashboardHandler(BaseHTTPRequestHandler):
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

    def do_GET(self):
        path = urlparse(self.path).path

        if path == "/" or path == "/index.html":
            self._serve_file(HTML_FILE, "text/html; charset=utf-8")
        elif path.startswith("/static/"):
            self._serve_static(path)
        elif path == "/api/scan":
            self._serve_json(STATE_DIR / "latest-scan.json")
        elif path == "/api/health":
            self._serve_json(STATE_DIR / "latest-health.json")
        elif path == "/api/history":
            self._serve_history()
        elif path == "/api/targets":
            self._list_targets()
        elif path == "/api/host-profiles":
            self._json_response({"profiles": discover_host_profiles()})
        elif path == "/api/cleanup-plan":
            self._cleanup_plan()
        elif path == "/api/cleanup-execution-plan":
            self._cleanup_execution_plan()
        elif path == "/api/duplicate-decisions":
            self._list_duplicate_decisions()
        elif path == "/api/category-order":
            f = STATE_DIR / "category-order.json"
            data = f.read_text(encoding="utf-8") if f.exists() else "[]"
            self._json_response(json.loads(data))
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
        elif path == "/api/understand":
            self._serve_understanding()
        elif path == "/api/source/skills":
            self._list_source_skills()
        elif path == "/api/search-skills":
            self._search_skills()
        elif path == "/api/custom-sources":
            self._get_custom_sources()
        elif path == "/api/global-stats":
            self._json_response(_scan_global_categories())
        elif path == "/api/installed-plugins":
            self._json_response(self._installed_plugins())
        elif path == "/api/global-overlap":
            self._json_response(detect_cross_dir_overlaps())
        elif path == "/api/scan-result":
            self._serve_json(CACHE_DIR / "scan-result.json")
        elif path == "/api/trash":
            self._list_trash()
        elif path.startswith("/api/trash/") and path.endswith("/restore"):
            self._restore_trash(path)
        elif path.startswith("/api/skill/") and path.endswith("/content"):
            name = self._validate_skill_name(self._path_part(path, 3))
            if not name:
                self.send_error(400, "Invalid skill name")
            else:
                self._serve_skill_content(name)
        elif path == "/api/preview":
            # Preview skill from any directory: /api/preview?dir=...&name=...
            from urllib.parse import parse_qs
            qs = parse_qs(urlparse(self.path).query)
            preview_dir = qs.get("dir", [""])[0]
            preview_name = qs.get("name", [""])[0]
            if preview_dir and preview_name:
                self._serve_preview(preview_dir, preview_name)
            else:
                self.send_error(400, "Missing dir or name")
        elif path.startswith("/api/skill/") and path.endswith("/upstream"):
            name = self._validate_skill_name(self._path_part(path, 3))
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
        elif path == "/api/scan-run":
            self._run_scan()
        elif path == "/api/cleanup-execute":
            self._cleanup_execute()
        elif path == "/api/duplicate-decision":
            self._duplicate_decision()
        elif path == "/api/steal":
            self._steal_skill()
        elif path == "/api/copy-skill":
            self._copy_skill()
        elif path == "/api/batch-delete":
            self._batch_delete()
        elif path == "/api/custom-sources":
            self._add_custom_source()
        elif path == "/api/category-order":
            try:
                length = int(self.headers.get('Content-Length', 0))
                raw = self.rfile.read(length).decode('utf-8') if length else '[]'
                data = json.loads(raw)
                if isinstance(data, list):
                    (STATE_DIR / "category-order.json").write_text(
                        json.dumps(data, ensure_ascii=False), encoding="utf-8"
                    )
                    self._json_response({"ok": True})
                else:
                    self.send_error(400, "Expected JSON array")
            except Exception as e:
                self._json_response({"error": str(e)}, 400)
        elif path.startswith("/api/trash/") and path.endswith("/restore"):
            self._restore_trash(path)
        elif path.startswith("/api/skill/") and path.endswith("/rehash"):
            name = self._validate_skill_name(self._path_part(path, 3))
            if not name:
                self.send_error(400, "Invalid skill name")
            else:
                self._rehash_skill(name)
        else:
            self.send_error(404)

    def do_DELETE(self):
        if not self._check_csrf():
            self._csrf_reject()
            return
        path = urlparse(self.path).path
        query = parse_qs(urlparse(self.path).query)
        if path.startswith("/api/skill/"):
            name = self._validate_skill_name(self._path_part(path, 3))
            if not name:
                self.send_error(400, "Invalid skill name")
            else:
                target = query.get("target", [""])[0]
                self._delete_skill(name, target or None)
        elif path == "/api/custom-sources":
            self._remove_custom_source()
        elif path == "/api/duplicate-decision":
            self._remove_duplicate_decision()
        elif path == "/api/trash":
            self._empty_trash()
        elif path.startswith("/api/trash/"):
            # Permanent delete: DELETE /api/trash/{trash_dir_name}
            self._delete_trash(path)
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

    def _serve_preview(self, dir_path, name):
        """Preview SKILL.md from any directory (no target switch needed).
        Query param ?full=1 returns full content instead of 500-char preview."""
        resolved = Path(dir_path).resolve()
        if not resolved.is_relative_to(Path.home()):
            self._json_response({"error": "dir must be under home directory"}, status=403)
            return
        skill_md = resolved / name / "SKILL.md"
        if not skill_md.exists():
            self._json_response({"error": "not found"}, status=404)
            return
        try:
            content = skill_md.read_text(encoding="utf-8", errors="ignore")
            # Extract description from frontmatter
            desc = ""
            if content.startswith("---"):
                end = content.find("---", 3)
                if end > 0:
                    fm = content[3:end]
                    for line in fm.split("\n"):
                        if line.strip().startswith("description:"):
                            desc = line.split(":", 1)[1].strip().strip("'\"")
                            break
            # Body (skip frontmatter)
            body = content
            if content.startswith("---"):
                end = content.find("---", 3)
                if end > 0:
                    body = content[end + 3:].strip()
            qs = parse_qs(urlparse(self.path).query)
            if qs.get("full", [""])[0] == "1":
                preview = body
            else:
                preview = body[:500] + ("…" if len(body) > 500 else "")
            self._json_response({
                "name": name,
                "dir": dir_path,
                "agent": _agent_from_path(dir_path),
                "description": desc,
                "preview": preview,
                "size": len(content),
            })
        except Exception as e:
            self._json_response({"error": str(e)}, status=500)

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
                {"method": "GET", "path": "/api/host-profiles", "desc": "Non-secret host source/MCP profiles for agent-specific scanners"},
                {"method": "GET", "path": "/api/cleanup-plan?scope=daily|deep", "desc": "Dry-run cleanup governance plan"},
                {"method": "GET", "path": "/api/cleanup-execution-plan?scope=&strategy=", "desc": "Executable-shaped cleanup preview without deletion"},
                {"method": "POST", "path": "/api/cleanup-execute", "desc": "Move selected cleanup candidates to trash"},
                {"method": "GET", "path": "/api/duplicate-decisions", "desc": "List local exact-duplicate handling decisions"},
                {"method": "POST", "path": "/api/duplicate-decision", "desc": "Persist exact-duplicate handling decisions"},
                {"method": "DELETE", "path": "/api/duplicate-decision?key=", "desc": "Remove a local exact-duplicate handling decision"},
                {"method": "GET", "path": "/api/global-stats", "desc": "Global category distribution across all skill libraries (cached 5min)"},
                {"method": "GET", "path": "/api/export", "desc": "Export skill manifest as JSON"},
                {"method": "GET", "path": "/api/understand?dir=&name=", "desc": "Rule-based Chinese understanding for one skill"},
                {"method": "GET", "path": "/api/skill/{name}/content", "desc": "Read SKILL.md content"},
                {"method": "GET", "path": "/api/skill/{name}/upstream", "desc": "Check upstream status for a skill"},
                {"method": "POST", "path": "/api/target", "desc": "Switch target directory"},
                {"method": "POST", "path": "/api/diagnose", "desc": "Trigger full diagnosis (Python-only)"},
                {"method": "POST", "path": "/api/scan-run", "desc": "Targeted scan: selected directories + analysis types"},
                {"method": "GET", "path": "/api/scan-result", "desc": "Get cached scan result"},
                {"method": "POST", "path": "/api/steal", "desc": "Install skill from GitHub URL"},
                {"method": "DELETE", "path": "/api/skill/{name}", "desc": "Delete a skill"},
                {"method": "PATCH", "path": "/api/skill/{name}/update", "desc": "Update skill from upstream"},
            ],
        })

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

    def _cleanup_plan(self):
        """Return a conservative dry-run cleanup plan for discovered skill dirs."""
        query = parse_qs(urlparse(self.path).query)
        scope = query.get("scope", ["daily"])[0]
        if scope not in ("daily", "deep"):
            scope = "daily"
        self._json_response(build_cleanup_plan(self._current_target(), scope))

    def _cleanup_execution_plan(self):
        """Return executable-shaped cleanup actions without applying them."""
        query = parse_qs(urlparse(self.path).query)
        scope = query.get("scope", ["daily"])[0]
        strategy = query.get("strategy", ["conservative"])[0]
        if scope not in ("daily", "deep"):
            scope = "daily"
        if strategy not in ("conservative", "declutter"):
            strategy = "conservative"
        self._json_response(build_cleanup_execution_plan(self._current_target(), scope, strategy))

    def _list_duplicate_decisions(self):
        """Return local exact-duplicate handling decisions."""
        data = _load_duplicate_decisions()
        entries = []
        for key, entry in data.get("multi_agent_deployment", {}).items():
            if not isinstance(entry, dict):
                continue
            item = dict(entry)
            item["key"] = key
            entries.append(item)
        entries.sort(key=lambda x: x.get("decided_at", ""), reverse=True)
        self._json_response({
            "schema": 1,
            "state_file": str(DUPLICATE_DECISIONS_FILE),
            "ignored_by_git": True,
            "decisions": entries,
            "count": len(entries),
        })

    def _duplicate_decision(self):
        """Persist a local decision for exact duplicate handling."""
        body = self._read_json() or {}
        decision = body.get("decision", "")
        skill_name = self._validate_skill_name(body.get("skill_name", ""))
        content_hash = body.get("content_hash", "")
        if decision != "multi_agent_deployment":
            self._json_response({"error": "unsupported decision"}, status=400)
            return
        if not skill_name:
            self._json_response({"error": "invalid skill name"}, status=400)
            return
        if not re.match(r'^[a-fA-F0-9]{8,64}$', content_hash or ""):
            self._json_response({"error": "invalid content hash"}, status=400)
            return

        data = _load_duplicate_decisions()
        key = _duplicate_decision_key(skill_name, content_hash)
        entry = {
            "decision": decision,
            "skill_name": skill_name,
            "content_hash": content_hash,
            "path": body.get("path", ""),
            "duplicate_of": body.get("duplicate_of", ""),
            "decided_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
        data.setdefault("multi_agent_deployment", {})[key] = entry
        _save_duplicate_decisions(data)
        self._json_response({"ok": True, "key": key, "entry": entry})
        self._log_history(
            "mark_duplicate_decision",
            paths=[body.get("path", "")],
            count=1,
            source="duplicate_decision",
            status="ok",
            detail={"skill_name": skill_name, "content_hash": content_hash, "decision": decision},
        )

    def _remove_duplicate_decision(self):
        """Remove one local exact-duplicate handling decision."""
        query = parse_qs(urlparse(self.path).query)
        key = query.get("key", [""])[0]
        if not re.match(r'^[a-fA-F0-9]{20}$', key or ""):
            self._json_response({"error": "invalid decision key"}, status=400)
            return
        data = _load_duplicate_decisions()
        bucket = data.setdefault("multi_agent_deployment", {})
        existed = key in bucket
        if existed:
            del bucket[key]
            _save_duplicate_decisions(data)
        self._json_response({"ok": True, "removed": existed, "key": key})
        self._log_history(
            "remove_duplicate_decision",
            paths=[],
            count=1 if existed else 0,
            source="duplicate_decision",
            status="ok" if existed else "blocked",
            detail={"key": key, "existed": existed},
        )

    def _cleanup_execute(self):
        """Execute selected cleanup candidate actions by moving skills to trash."""
        body = self._read_json() or {}
        actions = body.get("actions", [])
        if not isinstance(actions, list) or not actions:
            self._json_response({"error": "actions is empty"}, status=400)
            return

        ok, fail, skipped = 0, 0, 0
        changed_paths = []
        details = []
        max_skills = 500
        for action in actions[:100]:
            if not isinstance(action, dict):
                skipped += 1
                continue
            operation = action.get("operation", "")
            path = action.get("path", "")
            if operation not in ("move_skills_to_trash", "move_skill_to_trash") or not path:
                skipped += 1
                details.append({"path": path, "status": "skipped", "reason": "unsupported operation"})
                continue
            if operation == "move_skills_to_trash":
                allowed, reason = _is_cleanup_execute_allowed(path)
                if not allowed:
                    fail += 1
                    details.append({"path": path, "status": "blocked", "reason": reason})
                    continue
                skills_dir = Path(path).expanduser().resolve()
                moved = 0
                failed_names = []
                try:
                    skill_dirs = [d for d in sorted(skills_dir.iterdir(), key=lambda x: x.name.lower())
                                  if (d.is_dir() or d.is_symlink()) and (d / "SKILL.md").exists()]
                    for skill_dir in skill_dirs:
                        if ok >= max_skills:
                            skipped += 1
                            failed_names.append(f"{skill_dir.name}: safety cap reached")
                            continue
                        try:
                            self._trash_dir(skill_dir)
                            ok += 1
                            moved += 1
                        except Exception as e:
                            fail += 1
                            failed_names.append(f"{skill_dir.name}: {e}")
                    changed_paths.append(str(skills_dir))
                    details.append({
                        "path": str(skills_dir),
                        "status": "moved",
                        "moved": moved,
                        "failed": failed_names[:10],
                    })
                except Exception as e:
                    fail += 1
                    details.append({"path": str(skills_dir), "status": "failed", "reason": str(e)})
                continue

            skill_name = self._validate_skill_name(action.get("skill_name", ""))
            if not skill_name:
                fail += 1
                details.append({"path": path, "status": "blocked", "reason": "invalid skill name"})
                continue
            allowed, reason = _duplicate_skill_execute_allowed(
                path,
                skill_name,
                self._current_target(),
                duplicate_of=action.get("duplicate_of", ""),
                expected_hash=action.get("content_hash", ""),
            )
            if not allowed:
                fail += 1
                details.append({"path": path, "name": skill_name, "status": "blocked", "reason": reason})
                continue
            if ok >= max_skills:
                skipped += 1
                details.append({"path": path, "name": skill_name, "status": "skipped", "reason": "safety cap reached"})
                continue
            try:
                skills_dir = Path(path).expanduser().resolve()
                self._trash_dir(skills_dir / skill_name)
                ok += 1
                changed_paths.append(str(skills_dir))
                details.append({
                    "path": str(skills_dir),
                    "name": skill_name,
                    "status": "moved",
                    "moved": 1,
                })
            except Exception as e:
                fail += 1
                details.append({"path": path, "name": skill_name, "status": "failed", "reason": str(e)})

        self._json_response({
            "ok": True,
            "moved": ok,
            "failed": fail,
            "skipped": skipped,
            "changed_paths": changed_paths,
            "details": details,
        })
        self._log_history(
            "move_to_trash",
            paths=changed_paths,
            count=ok,
            source="cleanup_execute",
            status="ok" if fail == 0 else ("failed" if ok == 0 else "partial"),
            detail={"failed": fail, "skipped": skipped, "actions": len(actions)},
        )

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
        if not source_dir.is_relative_to(Path.home()):
            self._json_response({"error": "path must be under home directory"}, status=403)
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

    # ── Trash ──

    def _list_trash(self):
        """List all trashed skills."""
        trash_dir = STATE_DIR.parent / "trash"
        items = []
        if trash_dir.is_dir():
            for d in sorted(trash_dir.iterdir(), reverse=True):
                if not d.is_dir():
                    continue
                meta_path = d / ".trash-meta.json"
                try:
                    meta = json.loads(meta_path.read_text("utf-8"))
                except Exception:
                    meta = {"name": d.name, "original_path": "", "trashed_at": ""}
                kind = meta.get("kind", "skill")
                if kind == "symlink":
                    payload = d / meta.get("payload", meta.get("name", ""))
                    skill_count = 1 if payload.exists() or payload.is_symlink() else 0
                elif (d / "SKILL.md").exists():
                    skill_count = 1
                    kind = "skill"
                else:
                    skill_count = sum(
                        1 for c in d.iterdir()
                        if (c.is_dir() or c.is_symlink()) and (c / "SKILL.md").exists()
                    ) if d.is_dir() else 0
                    kind = kind or "collection"
                items.append({
                    "id": d.name,
                    "name": meta.get("name", d.name),
                    "original_path": meta.get("original_path", ""),
                    "trashed_at": meta.get("trashed_at", ""),
                    "skill_count": skill_count,
                    "kind": kind,
                })
        self._json_response({"items": items, "count": len(items)})

    def _restore_trash(self, path):
        """Restore a trashed skill to its original location (or current target)."""
        trash_id = path.split("/api/trash/")[1].replace("/restore", "")
        if '..' in trash_id or '/' in trash_id or '\\' in trash_id:
            self._json_response({"error": "invalid trash id"}, status=400)
            return
        trash_dir = STATE_DIR.parent / "trash" / trash_id
        if not trash_dir.is_dir():
            self._json_response({"error": "not found"}, status=404)
            return
        # Read metadata for original path
        meta_path = trash_dir / ".trash-meta.json"
        try:
            meta = json.loads(meta_path.read_text("utf-8"))
            original = meta.get("original_path", "")
        except Exception:
            meta = {}
            original = ""
        # Determine restore destination
        if original and Path(original).parent.is_dir():
            dest = Path(original)
        else:
            # Fallback: current target
            dest = Path(self._current_target()) / meta.get("name", trash_id.split("_", 2)[-1])
        if dest.exists() or dest.is_symlink():
            self._json_response({"error": f"目标已存在: {dest}", "status": "conflict"}, status=409)
            return
        try:
            if meta.get("kind") == "symlink":
                payload = meta.get("payload", meta.get("name", ""))
                payload_path = trash_dir / payload
                if not payload_path.exists() and not payload_path.is_symlink():
                    self._json_response({"error": "trashed symlink payload missing"}, status=500)
                    return
                shutil.move(str(payload_path), str(dest))
                shutil.rmtree(trash_dir)
                _invalidate_runtime_caches()
                self._log_history("restore", paths=[str(dest)], count=1, source="trash_restore", status="ok", detail={"trash_id": trash_id, "kind": "symlink"})
                self._json_response({"ok": True, "restored_to": str(dest)})
                return
            # Remove meta file before moving
            if meta_path.exists():
                meta_path.unlink()
            shutil.move(str(trash_dir), str(dest))
            _invalidate_runtime_caches()
            self._log_history("restore", paths=[str(dest)], count=1, source="trash_restore", status="ok", detail={"trash_id": trash_id, "kind": "skill"})
            self._json_response({"ok": True, "restored_to": str(dest)})
        except Exception as e:
            self._json_response({"error": str(e)}, status=500)

    def _delete_trash(self, path):
        """Permanently delete a trashed skill."""
        trash_id = path.split("/api/trash/")[1]
        if '..' in trash_id or '/' in trash_id or '\\' in trash_id:
            self._json_response({"error": "invalid trash id"}, status=400)
            return
        trash_dir = STATE_DIR.parent / "trash" / trash_id
        if not trash_dir.is_dir():
            self._json_response({"error": "not found"}, status=404)
            return
        original = ""
        try:
            meta = json.loads((trash_dir / ".trash-meta.json").read_text("utf-8"))
            original = meta.get("original_path", "")
        except Exception:
            pass
        try:
            shutil.rmtree(trash_dir)
            self._log_history("delete", paths=[original or str(trash_dir)], count=1, source="trash_delete", status="ok", detail={"trash_id": trash_id, "permanent": True})
            self._json_response({"ok": True, "deleted": trash_id})
        except Exception as e:
            self._json_response({"error": str(e)}, status=500)

    def _empty_trash(self):
        """Permanently delete every item in the project trash."""
        trash_dir = STATE_DIR.parent / "trash"
        if not trash_dir.is_dir():
            self._json_response({"ok": True, "deleted": 0})
            return
        deleted, failed, details = 0, 0, []
        item_names = []
        for item in sorted(trash_dir.iterdir()):
            if not item.is_dir():
                continue
            item_names.append(item.name)
            try:
                shutil.rmtree(item)
                deleted += 1
            except Exception as e:
                failed += 1
                details.append({"id": item.name, "error": str(e)})
        self._log_history("empty_trash", paths=[str(trash_dir)], count=deleted, source="trash_empty", status="ok" if failed == 0 else "partial", detail={"failed": failed, "items": item_names[:50]})
        self._json_response({"ok": True, "deleted": deleted, "failed": failed, "details": details[:20]})

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

    def _quick_check(self):
        """Python-only structure check — instant, no bash."""
        target = self._current_target()
        result = python_quick_check(target)
        if result is None:
            self._json_response({"error": "target not found"}, status=400)
            return
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
        global _targets_cache, _targets_cache_ts
        now = time.time()
        query = parse_qs(urlparse(self.path).query)
        force_refresh = query.get("refresh", ["0"])[0].lower() in ("1", "true", "yes")
        if _targets_cache and not force_refresh and (now - _targets_cache_ts) < 60:
            # Refresh is_current flag against current target
            current = self._current_target()
            cached = json.loads(json.dumps(_targets_cache))  # deep copy
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
            targets.append({
                "path": str(skills_dir),
                "rel": rel,
                "name": agent,
                "scope": scope,
                "count": count,
                "type": "skills",
                "is_current": str(skills_dir) == current,
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

        # Sort groups: current target's group first, then by total skills desc
        current_agent = next((t["name"] for t in targets if t["is_current"]), "")
        groups = sorted(grouped.values(),
                        key=lambda g: (0 if g["agent"] == current_agent else 1, -g["total_skills"]))

        # Flat list for backward compat + grouped view
        result = {"targets": targets, "groups": groups}
        _targets_cache = result
        _targets_cache_ts = time.time()
        self._json_response(result)

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
        # 2) Legacy: latest-scan.json from dashboard
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

    def _trash_dir(self, skill_dir):
        """Move a skill directory to trash. Returns trash path."""
        trash = STATE_DIR.parent / "trash"
        trash.mkdir(parents=True, exist_ok=True)
        ts = time.strftime("%Y%m%d_%H%M%S")
        dest = trash / f"{ts}_{skill_dir.name}"
        # Avoid collision
        if dest.exists():
            for i in range(100):
                candidate = trash / f"{ts}_{skill_dir.name}_{i}"
                if not candidate.exists():
                    dest = candidate
                    break
        if skill_dir.is_symlink():
            dest.mkdir(parents=True, exist_ok=False)
            payload = dest / skill_dir.name
            shutil.move(str(skill_dir), str(payload))
            meta = {
                "original_path": str(skill_dir),
                "trashed_at": ts,
                "name": skill_dir.name,
                "kind": "symlink",
                "payload": skill_dir.name,
                "link_target": os.readlink(payload) if payload.is_symlink() else "",
            }
            (dest / ".trash-meta.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
            _invalidate_runtime_caches()
            return dest
        shutil.move(str(skill_dir), str(dest))
        # Save metadata for restore
        meta = {"original_path": str(skill_dir), "trashed_at": ts, "name": skill_dir.name, "kind": "skill"}
        (dest / ".trash-meta.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
        _invalidate_runtime_caches()
        return dest

    def _delete_skill(self, name, target=None):
        """Move a skill to trash. If target is given, delete from that dir."""
        if target:
            target_path = Path(target).expanduser().resolve()
            # Validate target is under home directory
            if not target_path.is_relative_to(Path.home()):
                self._json_response({"error": "target must be under home directory"}, status=400)
                return
            skill_dir = target_path / name
            if _is_skill_entry(skill_dir, include_broken=True):
                try:
                    dest = self._trash_dir(skill_dir)
                    self._log_history("move_to_trash", paths=[str(skill_dir)], count=1, source="delete_skill", status="ok", detail={"name": name, "target": target})
                    self._json_response({"ok": True, "name": name, "trashed": str(dest)})
                except Exception as e:
                    self._json_response({"error": str(e)}, status=500)
                return
            self._json_response({"error": f"Skill '{name}' not found in {target}"}, status=404)
            return
        # Default: resolve from scan data
        skill_dir = self._resolve_skill_dir(name)
        if not skill_dir:
            self._json_response({"error": f"Skill '{name}' not found"}, status=404)
            return
        try:
            dest = self._trash_dir(skill_dir)
            self._log_history("move_to_trash", paths=[str(skill_dir)], count=1, source="delete_skill", status="ok", detail={"name": name})
            self._json_response({"ok": True, "name": name, "trashed": str(dest)})
        except Exception as e:
            self._json_response({"error": str(e)}, status=500)

    def _batch_delete(self):
        """Batch-delete skills from specified directories.
        Body: {"items": [{"target": "/path/to/dir", "name": "skill-name"}, ...]}
        """
        try:
            length = int(self.headers.get('Content-Length', 0))
            raw = self.rfile.read(length).decode('utf-8') if length else '{}'
            body = json.loads(raw)
        except Exception:
            self._json_response({"error": "Invalid JSON"}, 400)
            return

        items = body.get("items", [])
        if not items:
            self._json_response({"error": "items is empty"}, 400)
            return

        ok, fail, details = 0, 0, []
        home = Path.home()
        for item in items[:500]:  # safety cap
            name = item.get("name", "")
            target = item.get("target", "")
            if not name or not target:
                fail += 1
                continue
            # Validate path safety
            target_path = Path(target).expanduser().resolve()
            if not target_path.is_relative_to(home):
                fail += 1
                details.append({"name": name, "error": "path outside home"})
                continue
            safe_name = self._validate_skill_name(name)
            if not safe_name:
                fail += 1
                details.append({"name": name, "error": "invalid skill name"})
                continue
            skill_dir = target_path / safe_name
            if not _is_skill_entry(skill_dir, include_broken=True):
                fail += 1
                details.append({"name": safe_name, "error": "not a skill directory"})
                continue
            try:
                dest = self._trash_dir(skill_dir)
                ok += 1
            except Exception as e:
                fail += 1
                details.append({"name": name, "error": str(e)})

        self._json_response({"ok": True, "deleted": ok, "failed": fail, "details": details})
        moved_paths = [str(Path(item.get("target", "")).expanduser().resolve() / item.get("name", "")) for item in items[:500] if item.get("target") and item.get("name")]
        self._log_history("move_to_trash", paths=moved_paths, count=ok, source="batch_delete", status="ok" if fail == 0 else ("failed" if ok == 0 else "partial"), detail={"failed": fail, "total": len(items)})

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
            if d.exists() or _is_skill_entry(d, include_broken=True):
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
            name = self._validate_skill_name(self._path_part(path, 3))
            if not name:
                self.send_error(400, "Invalid skill name")
            else:
                self._update_upstream(name)
        elif path.startswith("/api/skill/") and path.endswith("/fix"):
            name = self._validate_skill_name(self._path_part(path, 3))
            if not name:
                self.send_error(400, "Invalid skill name")
            else:
                action = data.get("action", "")
                self._fix_skill(name, action, data)
        else:
            self.send_error(404)

    def _rehash_skill(self, name):
        """Re-record content hash for a skill (confirm change)."""
        target = self._current_target()
        skill_dir = Path(target) / name
        if not skill_dir.is_dir():
            self._json_response({"error": "not found"}, status=404)
            return
        record_content_hash(skill_dir)
        self._json_response({"ok": True, "name": name})
        self._log_history(
            "rehash",
            paths=[str(skill_dir)],
            count=1,
            source="skill_detail",
            status="ok",
            detail={"name": name},
        )

    def _copy_skill(self):
        """Copy or link a skill from a local directory to the current target library.

        Default mode is 'symlink' to keep a single source of truth. Pass mode='copy'
        for an independent duplicate.
        """
        body = self._read_json()
        if not body:
            self._json_response({"ok": False, "error": "无效请求"}, 400)
            return
        src_path = body.get("src", "")
        target = body.get("target", "") or self._current_target()
        skill_name = body.get("name", "")
        skill_name = self._validate_skill_name(skill_name)
        mode = body.get("mode", "symlink")
        if mode not in ("symlink", "copy"):
            self._json_response({"ok": False, "error": "mode 必须是 symlink 或 copy"}, 400)
            return
        if not src_path or not skill_name:
            self._json_response({"ok": False, "error": "缺少 src 或 name"}, 400)
            return
        src_dir = Path(src_path).expanduser().resolve()
        if not src_dir.is_dir() or not (src_dir / "SKILL.md").exists():
            self._json_response({"ok": False, "error": f"源目录不存在: {src_path}"}, 400)
            return
        if not src_dir.is_relative_to(Path.home()):
            self._json_response({"ok": False, "error": "src must be under home directory"}, 400)
            return
        target_dir = Path(target).expanduser().resolve()
        if not target_dir.is_relative_to(Path.home()):
            self._json_response({"ok": False, "error": "target must be under home directory"}, 400)
            return
        dest = target_dir / skill_name

        # Prevent linking/copying a skill onto itself.
        if dest.resolve() == src_dir:
            self._json_response({"ok": False, "error": "不能复制/链接到自身"}, 400)
            return

        # Snapshot and remove existing entry (symlink, dir, or stray file).
        if dest.exists() or dest.is_symlink():
            create_snapshot(dest)
            if dest.is_symlink():
                dest.unlink()
            elif dest.is_dir():
                shutil.rmtree(dest)
            else:
                dest.unlink()

        output = ""
        try:
            if mode == "symlink":
                # Relative link so the relationship survives when parent dirs move together.
                rel = os.path.relpath(str(src_dir), str(dest.parent))
                os.symlink(rel, dest)
                output = f"Linked to {src_dir}"
            else:
                shutil.copytree(src_dir, dest)
                record_content_hash(dest)
                output = f"Copied to {dest}"
        except OSError as e:
            # Symlink may fail across devices/filesystems; fall back to a real copy.
            if mode == "symlink":
                try:
                    shutil.copytree(src_dir, dest)
                    record_content_hash(dest)
                    mode = "copy"
                    output = f"Symlink failed ({e}), copied to {dest}"
                except Exception as e2:
                    self._json_response({"ok": False, "error": f"创建失败: {e2}"}, 500)
                    return
            else:
                self._json_response({"ok": False, "error": f"复制失败: {e}"}, 500)
                return

        self._json_response({"ok": True, "name": skill_name, "mode": mode, "output": output})
        self._log_history(
            "copy",
            paths=[str(src_dir), str(dest)],
            count=1,
            source="copy_skill",
            status="ok",
            detail={"name": skill_name, "src": str(src_dir), "target": str(target_dir), "mode": mode},
        )

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

    def _update_upstream(self, name):
        """Update a skill from its upstream source — pure Python."""
        target = self._current_target()
        query = parse_qs(urlparse(self.path).query)
        if query.get("target", [""])[0]:
            target = query["target"][0]
        result = update_skill(name, target)
        self._json_response(result)
        status = "ok" if result.get("ok") or result.get("success") else "failed"
        self._log_history(
            "update",
            paths=[str(Path(target).expanduser().resolve() / name)],
            count=1 if status == "ok" else 0,
            source="update_upstream",
            status=status,
            detail={"name": name, "target": target, "error": result.get("error", "")},
        )

    def _fix_skill(self, name, action, body=None):
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
                self._log_history(
                    "fix",
                    paths=[str(skill_dir)],
                    count=1,
                    source="fix_skill",
                    status="ok",
                    detail={"name": name, "action": "add_frontmatter"},
                )
            else:
                self._json_response({"ok": False, "error": "already has frontmatter"})
            return
        elif action == "add_description":
            skill_dir = self._resolve_skill_dir(name)
            if not skill_dir:
                self._json_response({"error": "not found"}, status=404)
                return
            skill_md = skill_dir / "SKILL.md"
            if not skill_md.exists():
                self._json_response({"error": "no SKILL.md"}, status=400)
                return
            desc = body.get("description", "") if isinstance(body, dict) else ""
            if not desc:
                desc = f"{name} skill"
            content = skill_md.read_text("utf-8")
            if content.startswith("---"):
                # Replace or add description in frontmatter
                import re as _re
                # If description line exists but empty, replace it
                new_content = _re.sub(
                    r'description:\s*[\'"]?\s*[\'"]?\s*\n',
                    f'description: \'{desc}\'\n',
                    content
                )
                if new_content == content:
                    # No description line found — insert after name line
                    new_content = _re.sub(
                        r'(name:\s*.+\n)',
                        rf"\1description: '{desc}'\n",
                        content
                    )
                skill_md.write_text(new_content, encoding="utf-8")
            else:
                # No frontmatter at all — add both
                skill_md.write_text(f"---\nname: {name}\ndescription: '{desc}'\n---\n\n{content}", encoding="utf-8")
            self._json_response({"ok": True, "name": name, "fixed": "added description"})
            self._log_history(
                "fix",
                paths=[str(skill_dir)],
                count=1,
                source="fix_skill",
                status="ok",
                detail={"name": name, "action": "add_description", "description": desc},
            )
            return
        self._json_response({"error": f"unknown action: {action}"}, status=400)

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
