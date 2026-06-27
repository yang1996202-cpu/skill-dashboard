"""GitHub 上游业务:解析来源 URL、安装、更新、检查上游状态、GitHub API。

从 serve.py 提取的纯库代码:无 HTTP 依赖、不 import serve。serve.py 与
skilldash/routes/*.py 的 handler 经顶层 import 调用,避免循环依赖。

GitHub API 自带 TTL 缓存(_github_cache)与限流状态(_github_rate_limit_reset),
只服务 GitHub 调用本身;与 HTTP 运行态缓存(_targets_cache / _diag_*,
留在 serve.py)无关。
"""
from __future__ import annotations

import hashlib
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

from .content_hash import _hash_key, _load_content_hashes, record_content_hash
from .paths import BASE_DIR


# ── GitHub URL parsing ──
GITHUB_HTTPS_RE = re.compile(
    r"^https?://github\.com/(?P<owner>[^/]+)/(?P<repo>[^/]+?)(?:\.git)?"
    r"(?:/(?:tree|blob)/(?P<ref>[^/]+)(?:/(?P<subdir>.+))?)?"
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
        # blob 链接指向文件（如 .../blob/main/neat-freak/SKILL.md），
        # 子目录取其父目录（剥掉末尾文件名）；install 找不到 SKILL.md 时 rglob 兜底
        if "/blob/" in url and subdir:
            subdir = subdir.rsplit("/", 1)[0] if "/" in subdir else ""
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
def install_skill(source_url, target_path, preferred_name=None, names=None):
    """Install skill(s) from a GitHub URL. Pure Python, no dashboard.

    Behavior:
      - Single candidate (or subdir URL) → install directly (backward-compatible).
      - preferred_name given and matches → install that one (backward-compatible).
      - names given (list) → install all matching candidates (batch; clone once reused).
      - Multiple candidates without preferred_name/names → return {"ok":False, "multi":True,
        "candidates":[...], "repo":...} so the caller can prompt the user to pick.

    Returns single-install shape {"ok", "name", "output", "snapshot"} for the
    single path, batch shape {"ok", "results":[{"name","commit","snapshot","output","error"}...],
    "output"} for the batch path, and the multi-prompt shape above for the probe path.
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

        # Decide which candidates to install
        selected_dirs = []
        batch_mode = False
        if len(candidates) == 1:
            selected_dirs = [candidates[0]]
        elif names:
            # Batch: filter candidates by name
            wanted = {n for n in names if n}
            selected_dirs = [c for c in candidates if c.name in wanted]
            if not selected_dirs:
                cands = ", ".join(c.name for c in candidates[:10])
                shutil.rmtree(tmp_root, ignore_errors=True)
                return {"ok": False, "error": f"勾选的名字都不在仓库里。找到: {cands}"}
            batch_mode = True
        elif preferred_name:
            # Backward-compat: single selection by name
            for c in candidates:
                if c.name == preferred_name:
                    selected_dirs = [c]
                    break
            if not selected_dirs:
                cands = ", ".join(c.name for c in candidates[:10])
                shutil.rmtree(tmp_root, ignore_errors=True)
                return {"ok": False, "error": f"仓库里没找到指定的 skill ({preferred_name})。找到: {cands}"}
        else:
            # Probe mode: return candidate list so the caller can prompt
            payload = {
                "ok": False,
                "multi": True,
                "candidates": [c.name for c in candidates][:50],
                "repo": f"{owner}/{repo}",
                "error": f"仓库里有 {len(candidates)} 个 skill，请选择",
            }
            shutil.rmtree(tmp_root, ignore_errors=True)
            return payload

        target = Path(target_path)
        if not target.exists():
            target.mkdir(parents=True, exist_ok=True)

        results = []
        for selected_dir in selected_dirs:
            res = _install_one(
                selected_dir, clone_dir, target, owner, repo, ref, clean_url,
                preferred_name if (not batch_mode and preferred_name) else None,
            )
            results.append(res)

        if not batch_mode:
            # Single-install response (backward compatible)
            shutil.rmtree(tmp_root, ignore_errors=True)
            r = results[0]
            return {
                "ok": r["ok"],
                "name": r["name"],
                "output": r["output"],
                "snapshot": r["snapshot"],
                "error": r.get("error", ""),
            }

        # Batch response
        ok_count = sum(1 for r in results if r["ok"])
        summary = "、".join(
            f"{r['name']}({'成功' if r['ok'] else '失败'})" for r in results
        )
        output = f"已装 {ok_count}/{len(results)} 个: {summary}"
        shutil.rmtree(tmp_root, ignore_errors=True)
        return {
            "ok": ok_count > 0,
            "results": results,
            "output": output,
            "repo": f"{owner}/{repo}",
        }

    except Exception as e:
        shutil.rmtree(tmp_root, ignore_errors=True)
        return {"ok": False, "error": str(e)}


def _install_one(selected_dir, clone_dir, target, owner, repo, ref, clean_url, preferred_name=None):
    """Install a single already-cloned skill dir into target. Used by install_skill."""
    try:
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

        dest_dir = target / selected_name
        # Path safety: dest must stay inside target
        if not dest_dir.resolve().is_relative_to(target.resolve()):
            return {"ok": False, "name": selected_name, "error": "目标路径越界", "output": "", "snapshot": None, "commit": ""}

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

        output = f"安装到 {target}/{selected_name}\n来源: {owner}/{repo}@{ref}"
        if selected_rel:
            output += f"\n子目录: {selected_rel}"
        output += f"\n提交: {installed_commit[:7]}"
        if snapshot_path:
            output += f"\n快照: {snapshot_path}"

        return {
            "ok": True,
            "name": selected_name,
            "output": output,
            "snapshot": snapshot_path,
            "commit": installed_commit,
            "error": "",
        }
    except Exception as e:
        return {
            "ok": False,
            "name": selected_dir.name,
            "error": str(e),
            "output": "",
            "snapshot": None,
            "commit": "",
        }


# ── Check upstream status (pure Python, no gh CLI) ──
def check_upstream_status(skill_dir):
    """Check if a skill is behind its upstream GitHub source.

    包装层:在调用真实 GitHub 查询前,用 content_hash 短路——本地 SKILL.md
    内容 hash 自上次 upstream 检测后未变 → 24h 内复用上次结果,跳过重复
    GitHub API 查询(降低未认证 60 次/小时额度消耗)。hash 变化或缓存过期
    则走真实查询并回写缓存。

    返回结构与 _check_upstream_status_raw 完全一致。
    """
    skill_md = Path(skill_dir) / "SKILL.md"
    if skill_md.exists():
        key = _hash_key(skill_dir)
        try:
            current_hash = hashlib.sha256(
                skill_md.read_text("utf-8", errors="ignore").encode("utf-8")
            ).hexdigest()
        except Exception:
            current_hash = ""
        # 读 content_hash 存档判断"内容是否变了"
        stored = _load_content_hashes()
        stored_hash = (stored.get(key) or {}).get("hash", "")
        cached = _upstream_hash_cache.get(key)
        now = time.time()
        if (current_hash and stored_hash == current_hash
                and cached and (now - cached[0]) < _upstream_hash_cache_ttl):
            # 内容没变 + 缓存未过期 → 复用,标 cached
            result = dict(cached[1])
            result["upstream_cached"] = True
            return result
        # 否则走真实查询;查询后若内容相对存档没变,把结果回写缓存
        result = _check_upstream_status_raw(skill_dir)
        if current_hash and stored_hash == current_hash:
            _upstream_hash_cache[key] = (now, result)
        return result
    return _check_upstream_status_raw(skill_dir)


def _check_upstream_status_raw(skill_dir):
    """Check if a skill is behind its upstream GitHub source. (real GitHub query)

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

# content_hash 短路缓存:skill 的 SKILL.md 内容 hash 自上次 upstream 检测后未变 →
# 24h 内跳过重复 GitHub 查询。key 用 _hash_key(skill_dir) 保证跨 agent 不串。
# value: (checked_at_ts, last_result_dict)
_upstream_hash_cache = {}
_upstream_hash_cache_ttl = 86400  # 24h


def get_github_rate_limit():
    """返回当前 GitHub API 限流轮廓(只读),给扫描前计费提示用。

    返回 {"limited": bool, "reset_ts": int, "reset_in_sec": int, "token_configured": bool}。
    token_configured 来自 GITHUB_TOKEN 是否非空(决定 60 vs 5000 额度)。
    """
    reset_ts = _github_rate_limit_reset
    now = time.time()
    if reset_ts == 0 or now >= reset_ts:
        return {"limited": False, "reset_ts": 0, "reset_in_sec": 0,
                "token_configured": bool(GITHUB_TOKEN)}
    return {"limited": True, "reset_ts": int(reset_ts),
            "reset_in_sec": int(reset_ts - now),
            "token_configured": bool(GITHUB_TOKEN)}


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


# ── npx skills CLI wrapper ──
# 实测 (2026-06):
#   `npx -y skills add -l <pkg>` 列仓库内 skill:边框输出,skill 名独占一行,
#   锚点 "Available Skills",每名匹配 ^[a-z][a-z0-9-]*$。
#   `--skill A B` 空格分隔多值(官方例子 --skill pr-review commit);
#   `--agent claude-code|codex|cursor|...`(72 个,见 --help)。
#   `-y` 跳确认;`-g` global(user-level);不传 -g 走 cwd 项目级 .agents/skills/。
#   装点 ~/.agents/skills/(global) 或 <cwd>/.agents/skills/(project),
#   lock 在 ~/.agents/.skill-lock.json 或 <cwd>/.skill-lock.json。

_NPX_PKG_RE = re.compile(r"^[a-zA-Z0-9._-]+/[a-zA-Z0-9._-]+$")
_NPX_GHURL_RE = re.compile(r"^https://github\.com/[a-zA-Z0-9._-]+/[a-zA-Z0-9._-]+?(/.*)?$", re.IGNORECASE)
# skill 名特征:小写开头,小写字母/数字/短横线,长度 1-64
_NPX_SKILL_LINE_RE = re.compile(r"^[a-z][a-z0-9-]{0,63}$")
# ANSI 转义 + 边框装饰符,用于剥离 -l 输出
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]|\x1b\][^\x07]*\x07|\x1b[\(\)][AB012]")

# 当前 target 路径 → skills CLI -a agent 名映射
# 实测 -a 接受 claude-code / codex / cursor / gemini-cli 等;映射不了的回退 -g
def agent_name_for_target(target_path):
    """Map a skill target dir path to a `skills add -a <agent>` value.

    Returns agent name string (e.g. "claude-code") or None if unmappable
    (caller falls back to -g global install).
    """
    if not target_path:
        return None
    p = str(target_path).replace("\\", "/").rstrip("/")
    low = p.lower()
    # 路径片段匹配(末尾段优先)
    if "/.claude/skills" in low or low.endswith("/.claude/skills"):
        return "claude-code"
    if "/.codex/skills" in low or low.endswith("/.codex/skills"):
        return "codex"
    if "/.cursor/skills" in low or low.endswith("/.cursor/skills"):
        return "cursor"
    if "/.gemini/skills" in low or low.endswith("/.gemini/skills") or "/.gemini-cli/skills" in low:
        return "gemini-cli"
    if "/.opencode/skills" in low:
        return "opencode"
    if "/.github copilot" in low or "/.github-copilot" in low:
        return "github-copilot"
    # 通用 ~/.agents/skills 或项目级 .agents/skills → CLI 自动检测,不传 -a
    if "/.agents/skills" in low:
        return None
    return None


def _validate_npx_package(package):
    """Validate package arg against owner/repo or GitHub URL whitelist.

    Returns (clean_package, error). clean_package is the value to pass
    to subprocess (never shell-escaped; always a single argv element).
    """
    pkg = (package or "").strip()
    if not pkg:
        return None, "package 不能为空"
    # owner/repo 形式
    if _NPX_PKG_RE.match(pkg):
        return pkg, None
    # https GitHub URL 形式(CLI 也接受)
    if _NPX_GHURL_RE.match(pkg):
        # 去掉末尾 / 和 .git
        clean = pkg.rstrip("/")
        if clean.endswith(".git"):
            clean = clean[:-4]
        return clean, None
    return None, f"package 格式非法(需 owner/repo 或 https://github.com/...): {pkg}"


def _parse_npx_list_output(text):
    """Extract candidate skill names from `skills add -l` output.

    Output has ANSI codes + box borders. Skill names appear on their own
    line under the "Available Skills" anchor. We strip ANSI, drop border
    decoration lines, and collect lines matching the skill-name pattern.
    """
    if not text:
        return []
    # 剥 ANSI
    clean = _ANSI_RE.sub("", text)
    lines = clean.splitlines()
    # 找 Available Skills 锚点(英文);部分版本可能用中文,兜底扫全文
    start = 0
    for i, ln in enumerate(lines):
        if "Available Skills" in ln or "available skills" in ln.lower():
            start = i + 1
            break
    candidates = []
    seen = set()
    for ln in lines[start:]:
        s = ln.strip()
        if not s:
            continue
        # 去掉边框残留符
        s = s.lstrip("│├└┌─◆◇●○►▹·•*").rstrip("│├└┌─")
        s = s.strip()
        if not s:
            continue
        # 跳过描述行(含空格/句号/逗号,长度通常 > 64)
        if " " in s or len(s) > 64:
            continue
        if _NPX_SKILL_LINE_RE.match(s) and s not in seen:
            seen.add(s)
            candidates.append(s)
    # 去掉 CLI 提示行(如 "Use --skill <name> ...")里被误识别的片段
    return candidates[:200]


def install_skill_npx(package, agent=None, skill_names=None):
    """Install skill(s) via `npx -y skills add` (Vercel skills CLI).

    Pure function — no serve dependency.

    Probe mode (skill_names is None):
        runs `skills add -l <pkg>` → returns
        {"ok": False, "multi": True, "candidates": [...], "package": pkg}

    Install mode (skill_names given, non-empty list):
        runs `skills add -y -s <n1> <n2> ... [-a <agent> | -g] <pkg>`
        → returns {"ok": bool, "results": [{"name": n, "ok": True}...],
                   "output": str, "package": pkg}

    Errors return {"ok": False, "error": str}.

    Security: package validated against owner/repo + https URL whitelist.
    All args passed as a subprocess list — never shell=True, never string
    concatenation of user input.
    """
    clean_pkg, err = _validate_npx_package(package)
    if err:
        return {"ok": False, "error": err}

    # npx 必须在 PATH
    npx = shutil.which("npx")
    if not npx:
        return {"ok": False, "error": "未找到 npx,需先装 Node"}

    # Probe mode
    if not skill_names:
        cmd = [npx, "-y", "skills", "add", "-l", clean_pkg]
        try:
            res = subprocess.run(
                cmd, capture_output=True, text=True, timeout=120,
            )
        except subprocess.TimeoutExpired:
            return {"ok": False, "error": "skills CLI 探测超时(120s)"}
        except FileNotFoundError:
            return {"ok": False, "error": "未找到 npx,需先装 Node"}
        out = (res.stdout or "") + "\n" + (res.stderr or "")
        # CLI 失败(仓库不存在/网络)时 stderr/stdout 带错误信息
        if res.returncode != 0:
            # 仍然尝试解析:某些版本 exit code 非零但已列出 skills
            cands = _parse_npx_list_output(out)
            if cands:
                return {"ok": False, "multi": True, "candidates": cands, "package": clean_pkg}
            msg = (res.stderr or res.stdout or "").strip()[-300:]
            return {"ok": False, "error": f"skills CLI 探测失败: {msg or '未知错误'}"}
        cands = _parse_npx_list_output(out)
        if not cands:
            return {"ok": False, "error": "仓库里没有找到 skill(或输出格式无法解析)"}
        return {"ok": False, "multi": True, "candidates": cands, "package": clean_pkg}

    # Install mode
    # 校验 skill_names:只允许小写字母/数字/短横线,防注入
    clean_names = []
    name_re = re.compile(r"^[a-zA-Z0-9._-]{1,64}$")
    for n in skill_names:
        n = str(n).strip()
        if not n or not name_re.match(n):
            return {"ok": False, "error": f"skill 名非法: {n!r}"}
        clean_names.append(n)

    # package 作为 source 位置参数紧跟 add 之后(skills CLI 标准用法 add <pkg> [options])。
    # 不能放末尾——-s / -a 都是多值选项,会把末尾的 package 当成 skill/agent 名吃掉,
    # 导致 skills add 报 "Missing required argument: source"。
    # dashboard 的 target 都是用户级(~/.xxx),skills add 必须加 -g(用户级)才装进
    # ~/.agents/skills 真身 + symlink 到 agent 目录;不加 -g 会装到 cwd 项目级
    # (.claude/skills),dashboard 的用户级 target 看不到。-a 限制只给指定 agent 建 symlink。
    cmd = [npx, "-y", "skills", "add", clean_pkg, "-y", "-s"] + clean_names + ["-g"]
    if agent:
        # agent 名也校验(字母/数字/短横线)
        if not re.match(r"^[a-zA-Z0-9._-]{1,32}$", agent):
            return {"ok": False, "error": f"agent 名非法: {agent!r}"}
        cmd += ["-a", agent]

    try:
        res = subprocess.run(
            cmd, capture_output=True, text=True, timeout=120,
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "skills CLI 安装超时(120s)"}
    except FileNotFoundError:
        return {"ok": False, "error": "未找到 npx,需先装 Node"}

    out = (res.stdout or "") + "\n" + (res.stderr or "")
    clean_out = _ANSI_RE.sub("", out)

    # 成功判定:CLI 装成功时 exit 0 且输出含 "Installation Summary" 或 skill 路径
    installed = []
    if res.returncode == 0:
        # 从输出里抓实际装上的 skill 名(Installation Summary 段或 .agents/skills/<name> 路径)
        for n in clean_names:
            # 路径形如 ~/.../.agents/skills/<name> 或 universal/symlink 段
            if re.search(r"(?:\.agents/skills/|skills[/\\])" + re.escape(n) + r"(?:[/\s]|$)", clean_out) \
               or re.search(r"\b" + re.escape(n) + r"\b", clean_out):
                installed.append({"name": n, "ok": True})
        # 兜底:如果输出含 Installation Summary 但没匹配到具体名,认为全部成功
        if not installed and ("Installation Summary" in clean_out or "Installing" in clean_out):
            installed = [{"name": n, "ok": True} for n in clean_names]

    if installed:
        ok_count = sum(1 for r in installed if r["ok"])
        summary = "、".join(r["name"] for r in installed if r["ok"])
        return {
            "ok": True,
            "results": installed,
            "output": f"已通过 skills CLI 装 {ok_count} 个: {summary}",
            "package": clean_pkg,
            "raw_output": clean_out[-800:],
        }

    # 失败:返回错误 + 原始输出片段
    msg = clean_out.strip()[-400:] if clean_out.strip() else "未知错误"
    return {
        "ok": False,
        "error": f"skills CLI 安装失败: {msg}",
        "package": clean_pkg,
        "raw_output": clean_out[-800:],
    }
