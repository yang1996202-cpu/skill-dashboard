"""GitHub 上游业务:解析来源 URL、安装、更新、检查上游状态、GitHub API。

从 serve.py 提取的纯库代码:无 HTTP 依赖、不 import serve。serve.py 与
skilldash/routes/*.py 的 handler 经顶层 import 调用,避免循环依赖。

GitHub API 自带 TTL 缓存(_github_cache)与限流状态(_github_rate_limit_reset),
只服务 GitHub 调用本身;与 HTTP 运行态缓存(_targets_cache / _diag_*,
留在 serve.py)无关。
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from .content_hash import record_content_hash
from .paths import BASE_DIR


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
    env_file = BASE_DIR / ".env"
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
