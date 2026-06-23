"""Skill directory discovery and governance classification."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from .classification import _classify_skill, _read_skill_description
from .host_inspectors import known_host_app_skill_roots, plugin_context_for_dir
from .paths import CACHE_DIR, STATE_DIR


DEFAULT_CONFIG_PATH = Path.home() / ".config" / "skill-dashboard" / "config.json"


# ── Skill entry 判定(从 serve.py 移入:纯路径判定,跨域 handler 复用)──
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


# Discovery is I/O heavy; cache results for a short TTL to avoid rescanning the
# whole home directory on every API call. Callers like /api/targets already cache
# at the HTTP layer, but lower-level functions (cleanup plan, execution plan,
# overlap scan) need a shared in-memory cache to avoid duplicate work.
_DISCOVER_SKILL_DIRS_CACHE = None
_DISCOVER_SKILL_DIRS_CACHE_TS = 0
_DISCOVER_SKILL_DIRS_TTL = 60  # seconds


def _read_skill_dashboard_config():
    """Load user config from default path or legacy home-level JSON."""
    # 1. Default XDG-style config
    if DEFAULT_CONFIG_PATH.exists():
        try:
            return json.loads(DEFAULT_CONFIG_PATH.read_text("utf-8"))
        except Exception:
            pass
    # 2. Legacy home-level config
    legacy = Path.home() / ".skill-dashboard.json"
    if legacy.exists():
        try:
            return json.loads(legacy.read_text("utf-8"))
        except Exception:
            pass
    return {}


def _env_roots(var_name):
    """Parse colon-separated path list from environment variable."""
    value = os.environ.get(var_name, "")
    return [p.strip() for p in value.split(os.pathsep) if p.strip()]

def _agent_from_path(dir_path):
    """Infer agent name from directory path."""
    p = str(dir_path)
    if "WorkBuddy.app" in p or "/.workbuddy/" in p or p.endswith("/.workbuddy/skills"):
        return "WorkBuddy"
    if "CodeBuddy" in p or "/.codebuddy/" in p or p.endswith("/.codebuddy/skills"):
        return "CodeBuddy"
    agents = [
        (".claude", "Claude Code"), (".workbuddy", "WorkBuddy"), (".hermes", "Hermes"),
        (".agents", "通用 Agents"), (".codex", "Codex"), (".cursor", "Cursor"),
        (".alice", "Alice"), (".openclaw", "OpenClaw"), (".cc-switch", "CC-Switch"),
        (".qclaw", "QClaw"), (".cola", "Cola"), (".codebuddy", "CodeBuddy"),
    ]
    for prefix, name in agents:
        if prefix in p:
            return name
    parts = Path(p).parts
    # For .config/<agent>/skills, the real agent name is the child of .config
    for i, part in enumerate(parts):
        if part.startswith(".") and not part.startswith(".."):
            if part == ".config" and i + 1 < len(parts):
                return parts[i + 1]
            return part.lstrip(".")
    return Path(p).name

def _is_in_git_repo(dir_path):
    """Check if directory is inside a Git repository (not at agent root)."""
    path = Path(dir_path).resolve()
    home = Path.home().resolve()
    
    # Don't treat agent root dirs as project-level even if they're in git
    try:
        rel_parts = path.relative_to(home).parts
        # ~/.agent/skills is agent root, not project
        if len(rel_parts) == 2 and rel_parts[0].startswith(".") and rel_parts[1] == "skills":
            return False
    except Exception:
        pass
    
    # Walk up to find .git directory
    current = path
    while current != current.parent:
        if (current / ".git").exists():
            # Found git repo — verify it's not just home dir itself
            if current != home:
                return True
            break
        current = current.parent
    return False


def _is_user_level_skill(dir_path):
    """Check if directory is a user-level (global) skill directory.
    
    User-level means: located at ~/.xxx/skills/ (directly under home directory)
    
    Examples:
    - ~/.claude/skills/      → user-level ✅
    - ~/.kiro/skills/        → user-level ✅
    - ~/AI-Skills/           → NOT user-level ❌ (not under ~/.<agent>/)
    - ~/projects/app/.claude/skills/ → NOT user-level ❌ (not directly under home)
    """
    try:
        home = Path.home()
        path = Path(dir_path).resolve()
        
        # Must be relative to home
        rel_parts = path.relative_to(home).parts
        
        # User-level pattern: ~/.agent/skills/ (exactly 2 levels deep)
        # rel_parts[0] must be a dot-prefixed directory (agent name)
        # rel_parts[1] must be "skills"
        if len(rel_parts) == 2 and rel_parts[0].startswith(".") and rel_parts[1] == "skills":
            return True
            
    except Exception:
        pass
    
    return False


def _is_project_agent_skill(dir_path):
    """Check if directory is a project-level agent skill directory.
    
    Project-level agent skill means: .agent/skills/ within a project (not at home)
    
    Examples:
    - ~/projects/my-app/.claude/skills/    → project agent skill ✅
    - ~/projects/foo/.kiro/skills/         → project agent skill ✅
    - ~/projects/bar/src/.cursor/skills/   → project agent skill ✅
    - ~/.claude/skills/                    → NOT project (user-level) ❌
    - ~/AI-Skills/                         → NOT project agent skill ❌
    - ~/.antigravity/extensions/.../skills → NOT project (home agent extension) ❌
    """
    try:
        path = Path(dir_path).resolve()
        home = Path.home().resolve()
        
        # Must not be directly under home (that's user-level)
        try:
            rel_parts = path.relative_to(home).parts
            # If path is ~/.xxx/... (starts with dot-directory), check depth
            if len(rel_parts) > 0 and rel_parts[0].startswith("."):
                # This is under home's agent dir, not a project
                # Unless it's explicitly in a projects-like subdirectory
                # For now, treat all ~/.agent/... as non-project
                return False
        except ValueError:
            # Not under home at all → could be project
            pass
        
        # Check if path matches pattern: .../.agent/skills/
        # Path should have at least: /some/path/.agent/skills
        parts = path.parts
        if len(parts) >= 2:
            # Last part should be "skills", second-to-last should be dot-prefixed
            if parts[-1] == "skills" and parts[-2].startswith(".") and not parts[-2].startswith(".."):
                # Additional check: must be in a project-like location (not under ~/.agent/)
                # Verify by checking if it's NOT directly under home
                if path.parent.parent != home:
                    return True
    
    except Exception:
        pass
    
    return False


def _classify_skill_dir_detail(dir_path):
    """Return directory governance metadata used by the UI.

    Discovery is intentionally high-recall: if a directory contains */SKILL.md it
    should be visible somewhere.  Governance is stricter: only active/user roots
    should look directly manageable, while package caches and marketplace copies
    are audit evidence by default.
    """
    path = Path(dir_path).expanduser()
    p = str(path).replace("\\", "/").lower()
    padded_p = "/" + p.strip("/") + "/"
    home = Path.home()
    # Coarse path category — feeds the UI capability-bucket fallback and the
    # two layer branches below. Inlined from the former _classify_skill_dir so
    # the project has a single classification entry point.
    # NOTE: this initial value may be overridden by mark() calls further down.
    if any(sig in p for sig in (
        ".snapshots", "backup", "plugins-backup", "plugins/cache",
        "/cache/", "vendor_imports", ".tmp", ".temp",
        "bundled-marketplaces", "/install/cache/",
    )):
        category = "cache"
    elif _is_user_level_skill(path):
        category = "user"
    elif _is_project_agent_skill(path):
        category = "project"
    else:
        try:
            _cat_rel = path.relative_to(home).parts
        except ValueError:
            _cat_rel = ()
        category = "project"
        if _cat_rel and _cat_rel[0].startswith("."):
            for _i, _pt in enumerate(_cat_rel):
                if (_pt.startswith(".") and not _pt.startswith("..")
                        and _i + 1 < len(_cat_rel) and _cat_rel[_i + 1] == "skills"
                        and _i > 0):
                    category = "cross-copy"
                    break
        if category == "project":
            for sig in ("marketplace", "agent-plugins", "/plugins/", "\\plugins\\",
                        "extensions/", "\\extensions\\"):
                if sig in p:
                    if sig in ("/plugins/", "\\plugins\\"):
                        idx = p.find(sig)
                        after = p[idx + len(sig):]
                        if after and ("/skills" in after or "\\skills" in after):
                            category = "marketplace"
                            break
                    else:
                        category = "marketplace"
                        break
    layer = "user-installed"
    policy = "manage"
    confidence = "medium"
    evidence = []
    plugin_context = plugin_context_for_dir(path)

    def mark(new_layer, new_policy, reason, new_category=None, new_confidence="high"):
        nonlocal layer, policy, category, confidence
        layer = new_layer
        policy = new_policy
        confidence = new_confidence
        if new_category:
            category = new_category
        evidence.append(reason)

    try:
        rel_parts = path.resolve().relative_to(home.resolve()).parts
    except Exception:
        rel_parts = path.parts
    top = rel_parts[0].lower() if rel_parts else ""

    # Exact agent root at home level: ~/.claude/skills or ~/.agents/skills
    if len(rel_parts) == 2 and rel_parts[0].startswith(".") and rel_parts[1] == "skills":
        mark("active-root", "manage", "agent root skills directory", "user")

    # User-level non-agent collections fall through to "project" in the inlined category logic.
    # No special handling needed here

    # Project-local skills: anything not in ~/.xxx/skills/
    if category == "project":
        mark("project-local", "review", "project-level skills (not in home directory)", "project")

    # Cross-agent/imported copies are useful cleanup candidates, not automatic deletes.
    if category == "cross-copy":
        mark("imported-copy", "review", "nested agent skills copy", "cross-copy")

    # Backups and snapshots are reviewable evidence, not current runtime roots.
    if any(sig in p for sig in (".snapshots", "backup", "plugins-backup", "skill-backups", "/migration/", "/archive/")):
        mark("backup-snapshot", "review", "backup or snapshot directory", "cache")

    # Package-manager and plugin caches: visible in deep audit, hidden from daily work.
    if "/install/cache/" in padded_p or "/.bun/install/cache/" in padded_p:
        mark("package-cache", "hidden", "package manager install cache", "cache")
    elif any(sig in p for sig in ("plugins/cache", "/cache/", "vendor_imports", ".tmp", ".temp", "bundled-marketplaces")):
        mark("plugin-cache", "hidden", "plugin/cache/vendor artifact", "cache")

    # Marketplace catalogues are source material. They may contain hundreds of
    # skills, but they are not the user's active skill library.
    if any(sig in p for sig in ("marketplace", "agent-plugins", "skills-marketplace")):
        mark("plugin-marketplace", "observe", "marketplace catalogue or plugin store", "marketplace")
    elif "/plugins/" in p and "/skills" in p:
        mark("plugin-marketplace", "observe", "plugin-provided skills", "marketplace")

    # Vendor/system bundles may be mounted into a host, but should not be treated
    # as user-owned cleanup targets.
    if any(sig in p for sig in (
        "/.system",
        "hermes-agent",
        "optional-skills",
        "openai-bundled",
        "openai-curated",
        "openai-primary-runtime",
        "/builtin/",
        "/resources/skills/",
        "/connectors/skills/",
        "/extensions/",
    )):
        mark("vendor-bundled", "observe", "host/vendor bundled skills", category)

    if "/workspace/skills/" in padded_p or padded_p.endswith("/workspace/skills/"):
        mark("project-local", "review", "workspace-local skills", "project")

    # Test fixtures and examples are never daily management targets.
    if any(sig in padded_p for sig in ("/fixtures/", "/fixture/", "/examples/", "/test/", "/tests/")):
        mark("fixture-example", "hidden", "test fixture or example skills", "cache")

    # Downloaded/extracted skill packages (e.g. ~/Downloads/<template>/.claude/skills)
    # are reviewable cleanup candidates, not active project skills.
    if "/downloads/" in padded_p:
        mark("downloaded-package", "review", "downloaded/extracted skill package", "cache")

    # Known import buckets are reviewable cleanup candidates, not cache.
    if any(sig in p for sig in ("openclaw-imports", "/imports/")):
        mark("imported-copy", "review", "imported skills bucket", "cross-copy")

    if plugin_context:
        role = plugin_context.get("package_role")
        if role == "claude-plugin-cache":
            mark("plugin-package", "observe", "Claude plugin installed/cache package", "marketplace")
        elif role == "claude-plugin-marketplace":
            mark("plugin-marketplace", "observe", "Claude plugin marketplace catalogue", "marketplace")
        elif role == "codex-plugin-package":
            if plugin_context.get("runtime_state") == "cache":
                mark("plugin-cache", "observe", "Codex plugin package cache", "cache")
            else:
                mark("plugin-package", "observe", "Codex enabled plugin or connector package", "marketplace")
        elif role in ("workbuddy-user-root", "buddy-user-root"):
            mark("active-root", "manage", f"{plugin_context.get('host', 'Buddy')} user skills directory", "user")
        elif role in ("workbuddy-builtin", "workbuddy-builtin-plugin", "buddy-builtin", "buddy-builtin-plugin"):
            mark("vendor-bundled", "observe", f"{plugin_context.get('host', 'Buddy')} app builtin skills", "marketplace")
        elif role in ("workbuddy-connector", "buddy-connector"):
            mark("plugin-package", "observe", f"{plugin_context.get('host', 'Buddy')} connector skill package", "marketplace")
        elif role in ("workbuddy-marketplace", "workbuddy-connector-marketplace", "buddy-marketplace", "buddy-connector-marketplace"):
            mark("plugin-marketplace", "observe", f"{plugin_context.get('host', 'Buddy')} marketplace catalogue", "marketplace")
        elif role in ("workbuddy-cache", "workbuddy-artifact", "buddy-cache", "buddy-artifact"):
            mark("plugin-cache", "observe", f"{plugin_context.get('host', 'Buddy')} cache/artifact", "cache")
        elif role == "codex-system-skill":
            mark("vendor-bundled", "observe", "Codex 注入的系统技能", "marketplace")
        elif role == "codex-vendor-curated":
            mark("vendor-bundled", "observe", "vendor 导入精选技能", "marketplace")
        elif role == "codex-plugin-staging":
            mark("plugin-cache", "hidden", "Codex 插件市场暂存", "cache")
        elif role == "codex-bundled-marketplace":
            mark("plugin-marketplace", "observe", "Codex 打包市场货架", "marketplace")
        elif role == "codex-legacy-runtime":
            mark("plugin-cache", "hidden", "Codex 旧版运行时遗留", "cache")
        elif role == "codex-plugin-backup":
            mark("plugin-cache", "hidden", "Codex 插件备份", "cache")
        runtime_label = plugin_context.get("runtime_label")
        if runtime_label:
            evidence.append(runtime_label)

    # Suspicious paths: temp/download/trash/node_modules — high-risk cleanup candidates
    suspicious_signals = [".trash", "/downloads/", "/tmp/", "node_modules", "/.cache/"]
    is_suspicious = any(sig in p for sig in suspicious_signals)

    policy_labels = {
        "manage": "可管理",
        "review": "待复核",
        "observe": "只观察",
        "hidden": "默认隐藏",
    }
    layer_labels = {
        "active-root": "当前/Agent 根目录",
        "user-installed": "用户技能库",
        "project-local": "项目内技能",
        "imported-copy": "导入/跨 Agent 副本",
        "backup-snapshot": "备份/快照",
        "downloaded-package": "下载/解包目录",
        "package-cache": "包管理缓存",
        "plugin-cache": "插件缓存",
        "plugin-package": "已安装插件包",
        "plugin-marketplace": "插件市场/目录",
        "vendor-bundled": "宿主内置包",
        "fixture-example": "测试样例",
    }
    detail = {
        "category": category,
        "layer": layer,
        "layer_label": layer_labels.get(layer, layer),
        "policy": policy,
        "policy_label": policy_labels.get(policy, policy),
        "confidence": confidence,
        "evidence": evidence[:4],
        "is_deletable": policy == "manage" and not is_suspicious,
        "is_daily": policy in ("manage", "review"),
        "is_suspicious": is_suspicious,
    }
    if plugin_context:
        detail.update(plugin_context)
    return detail

def _sample_skill_names(skills_dir, limit=6):
    """Return a small stable sample of skill names in a skills directory."""
    names = []
    try:
        for d in sorted(Path(skills_dir).iterdir(), key=lambda x: x.name.lower()):
            if (d.is_dir() or d.is_symlink()) and (d / "SKILL.md").exists():
                names.append(d.name)
                if len(names) >= limit:
                    break
    except Exception:
        pass
    return names

def _discover_skill_dirs():
    """Discover all skill directories on the system.
    Returns a list of Path objects pointing to directories that contain SKILL.md entries.

    Discovery strategy (in order):
    1. ~/.xxx/skills/ — any dot-prefixed agent directory with a skills/ subdir
    2. ~/first-level/skills/ — non-hidden directories with a skills/ subdir
    3. ~/projects/*//skills/ — project-level skill directories
    4. .skill-dashboard.json config files (home-level + project-level)
    5. custom-sources.json (user-defined paths)
    """
    global _DISCOVER_SKILL_DIRS_CACHE, _DISCOVER_SKILL_DIRS_CACHE_TS
    now = time.time()
    if _DISCOVER_SKILL_DIRS_CACHE is not None and (now - _DISCOVER_SKILL_DIRS_CACHE_TS) < _DISCOVER_SKILL_DIRS_TTL:
        return list(_DISCOVER_SKILL_DIRS_CACHE)

    home = Path.home()
    candidates = []
    seen_paths = set()
    validated_paths = set()  # dirs already confirmed by _has_skill_md
    _resolved_inodes = set()  # (st_dev, st_ino) for samefile dedup (macOS case-insensitive FS)
    _has_skill_md_cache = {}

    def add_dir(d, _validated=False):
        d = d.resolve()
        if not d.is_dir() or str(d) in seen_paths:
            return
        # Dedup by inode — catches macOS case-insensitive aliases (projects/ vs Projects/)
        try:
            st = d.stat()
            inode_key = (st.st_dev, st.st_ino)
            if inode_key in _resolved_inodes:
                return
            _resolved_inodes.add(inode_key)
        except OSError:
            return
        seen_paths.add(str(d))
        candidates.append(d)
        if _validated:
            validated_paths.add(str(d))

    # 1. ~/.xxx/ — any dot-prefixed agent directory
    #    Only skip genuine system junk. Everything else: let SKILL.md validation decide.
    _SKIP_DEEP = {".git", ".Trash", "node_modules", "__pycache__", "venv", ".venv",
                  "env", "dist", "build", "logs", ".cache", ".npm", "Library",
                  ".snapshots", ".tmp", ".temp"}
    # Shallow skip: only dirs that are DEFINITELY not agents (system caches, build tools)
    _SHALLOW_SKIP = {".Trash", ".cache", ".git"}

    def _has_skill_md(d, cache=None):
        """Check if directory contains at least one */SKILL.md entry.

        Uses os.scandir to avoid pathlib overhead and an optional cache dict
        keyed by resolved path string to avoid rescanning the same directory.
        """
        key = None
        if cache is not None:
            try:
                key = str(Path(d).resolve())
            except Exception:
                key = None
            if key in cache:
                return cache[key]
        try:
            with os.scandir(d) as it:
                for entry in it:
                    if entry.is_dir(follow_symlinks=False) or entry.is_symlink():
                        try:
                            if os.path.exists(os.path.join(entry.path, "SKILL.md")):
                                if key is not None:
                                    cache[key] = True
                                return True
                        except OSError:
                            continue
            if key is not None:
                cache[key] = False
            return False
        except Exception:
            return False

    def _scan_agent_deep(root, max_depth=7, _depth=0):
        """Deep scan within agent dirs for marketplaces/backups/extensions/plugins.

        Only stops at container dirs (own SKILL.md + children with SKILL.md)
        and max depth. All other recursion proceeds normally.
        Noise filtering is handled in _list_targets post-processing.
        """
        if _depth >= max_depth:
            return
        try:
            for entry in root.iterdir():
                if not entry.is_dir() or entry.name in _SKIP_DEEP:
                    continue
                has_sub = _has_skill_md(entry, _has_skill_md_cache)
                has_own = os.path.exists(os.path.join(str(entry), "SKILL.md"))
                is_container = has_own and has_sub
                if has_sub:
                    # Container inside skills/ -> skip (parent already shows it)
                    if is_container and root.name == "skills":
                        continue
                    add_dir(entry, _validated=True)
                # Don't recurse into containers — sub-skills are internal
                if is_container:
                    continue
                _scan_agent_deep(entry, max_depth, _depth + 1)
        except (PermissionError, OSError):
            pass

    try:
        for entry in home.iterdir():
            if not entry.is_dir():
                continue
            name = entry.name
            if name in _SHALLOW_SKIP or (name.startswith(".") and name.startswith("..")):
                continue
            if name.startswith("."):
                skills_dir = entry / "skills"
                has_skills = skills_dir.is_dir()
                if has_skills:
                    # Standard: skills/ + its subdirs
                    add_dir(skills_dir)
                    try:
                        for sub in skills_dir.iterdir():
                            if not sub.is_dir():
                                continue
                            # Container check: if a dir has its own SKILL.md AND
                            # children with SKILL.md, it's a skill package (e.g.
                            # gstack/ with 53 sub-skills).  Skip it — the package
                            # is already visible as a skill within the parent
                            # target, and its sub-skills shouldn't be flattened.
                            has_own = os.path.exists(os.path.join(str(sub), "SKILL.md"))
                            if has_own and _has_skill_md(sub, _has_skill_md_cache):
                                continue
                            add_dir(sub)
                    except (PermissionError, OSError):
                        pass
                # Deep scan for ALL .xxx dirs (not just confirmed agents)
                # This catches: .config/opencode/skills/, .antigravity/extensions/,
                # .alice/backups/, .openclaw/workspace/, etc.
                _scan_agent_deep(entry, max_depth=7)
    except (PermissionError, OSError):
        pass

    # 2. ~/first-level/ — non-hidden directories
    #    Checks: skills/ subdir + dirs that directly contain SKILL.md entries
    try:
        for entry in home.iterdir():
            if not entry.is_dir() or entry.name.startswith("."):
                continue
            name = entry.name
            skills_dir = entry / "skills"
            if skills_dir.is_dir():
                add_dir(skills_dir)
            # Also check if the dir itself is a skills collection (e.g., ~/AI-Skills/)
            if name not in ("Downloads", "Documents", "Desktop", "Movies", "Music", "Pictures", "Public"):
                if _has_skill_md(entry, _has_skill_md_cache):
                    add_dir(entry, _validated=True)
    except (PermissionError, OSError):
        pass

    # 2b. ~/Downloads/ — scan subdirs for skill collections + .agent/skills (depth 3)
    downloads = home / "Downloads"
    if downloads.is_dir():
        try:
            for d in downloads.iterdir():
                if not d.is_dir():
                    continue
                if _has_skill_md(d, _has_skill_md_cache):
                    add_dir(d, _validated=True)
                _scan_agent_deep(d, max_depth=2, _depth=1)
                
                # Also check for .agent/skills within downloads subdirs
                # This catches: ~/Downloads/vault_template/.claude/skills
                for agent_name in [".claude", ".kiro", ".cursor", ".codex", ".alice"]:
                    agent_dir = d / agent_name
                    if agent_dir.is_dir():
                        skills_path = agent_dir / "skills"
                        if skills_path.is_dir():
                            add_dir(skills_path)
        except (PermissionError, OSError):
            pass

    # 3. ~/projects/*//skills/ — project-level skill directories
    #    Recursive scan using os.walk to find .agent/skills at any depth (like skill-discover)
    _project_roots_seen = set()  # inode dedup for project root dirs
    for entry in home.iterdir():
        if not entry.is_dir() or entry.name.startswith("."):
            continue
        # Only scan common project root names (case-insensitive match)
        if entry.name.lower() not in ("projects", "code", "workspace", "dev", "work", "src", "repos"):
            continue
        try:
            st = entry.stat()
            inode_key = (st.st_dev, st.st_ino)
            if inode_key in _project_roots_seen:
                continue
            _project_roots_seen.add(inode_key)
        except OSError:
            continue
        
        # Recursive scan for .agent/skills/ directories (inspired by skill-discover)
        try:
            for root, dirs, _files in os.walk(entry):
                # Calculate depth from project root
                depth = root.count(os.sep) - str(entry).count(os.sep)
                if depth >= 5:  # Max depth 5 to avoid going too deep
                    del dirs[:]
                    continue
                
                # Filter out system junk directories
                dirs[:] = [d for d in dirs if d not in _SKIP_DEEP]
                
                # Check if current directory is an agent config dir (.claude, .kiro, etc.)
                root_basename = os.path.basename(root)
                if root_basename.startswith(".") and not root_basename.startswith(".."):
                    # This is a .agent directory, check for skills/ subdirectory
                    skills_path = Path(root) / "skills"
                    if skills_path.is_dir():
                        add_dir(skills_path)
        except (PermissionError, OSError):
            pass

    # 4. Host app bundled skill roots found by host inspectors.
    for skill_root in known_host_app_skill_roots():
        add_dir(skill_root)

    # 5. Config files (XDG + legacy)
    cfg = _read_skill_dashboard_config()
    for p in cfg.get("skill_paths", []):
        add_dir(Path(p).expanduser())
    for p in cfg.get("paths", []):
        add_dir(Path(p).expanduser())

    # 5b. Environment variables
    for p in _env_roots("CLAUDE_SKILL_ROOTS"):
        add_dir(Path(p).expanduser())
    for entry in home.iterdir():
        if not entry.is_dir() or entry.name.startswith("."):
            continue
        if entry.name.lower() not in ("projects", "code", "workspace"):
            continue
        try:
            st = entry.stat()
            inode_key = (st.st_dev, st.st_ino)
            if inode_key in _project_roots_seen:
                continue
        except OSError:
            continue
        try:
            for proj in entry.iterdir():
                if not proj.is_dir():
                    continue
                cfg_file = proj / ".skill-dashboard.json"
                if cfg_file.exists():
                    try:
                        cfg = json.loads(cfg_file.read_text("utf-8"))
                        for p in cfg.get("paths", []):
                            add_dir(Path(p).expanduser())
                    except Exception:
                        pass
        except (PermissionError, OSError):
            pass

    # 6. Custom sources (legacy)
    try:
        cf = STATE_DIR / "custom-sources.json"
        if cf.exists():
            for p in json.loads(cf.read_text()):
                add_dir(Path(p).expanduser())
    except Exception:
        pass

    # Filter: must contain SKILL.md entries, exclude Trash
    # Skip re-check for dirs already validated by _has_skill_md during scan
    result = [d for d in candidates
              if ".Trash" not in str(d)
              and d.is_dir()
              and (str(d) in validated_paths
                   or _has_skill_md(d, _has_skill_md_cache))]
    _DISCOVER_SKILL_DIRS_CACHE = tuple(result)
    _DISCOVER_SKILL_DIRS_CACHE_TS = now
    return result

def _discover_command_dirs():
    """Discover all command directories on the system.

    Command directories contain .md files (not SKILL.md subdirs).
    Primary pattern: ~/.claude/commands/ and .claude/commands/ under projects.
    """
    home = Path.home()
    candidates = []
    seen = set()

    def add_dir(d):
        d = d.resolve()
        if not d.is_dir() or str(d) in seen:
            return
        seen.add(str(d))
        candidates.append(d)

    # Agent roots: ~/.xxx/commands/
    try:
        for entry in home.iterdir():
            if not entry.is_dir() or not entry.name.startswith("."):
                continue
            commands_dir = entry / "commands"
            if commands_dir.is_dir():
                add_dir(commands_dir)
    except (PermissionError, OSError):
        pass

    # Project-level: recursively scan common project roots for commands/ dirs.
    # This catches .claude/commands, src/commands, cli-anything-plugin/commands, etc.
    _CMD_SKIP = {"node_modules", ".git", "__pycache__", "venv", ".venv", "env",
                 "dist", "build", "logs", ".cache", ".npm", "Library",
                 ".snapshots", ".tmp", ".temp", ".Trash"}

    def _is_command_dir(d):
        """A directory is a command dir if it contains .md files and no SKILL.md subdirs."""
        if not d.is_dir():
            return False
        has_md = False
        try:
            for f in d.iterdir():
                if f.name in _CMD_SKIP:
                    continue
                if f.is_file() and f.suffix == ".md":
                    has_md = True
                if f.is_dir() and (f / "SKILL.md").exists():
                    return False
        except (PermissionError, OSError):
            return False
        return has_md

    def _scan_project_commands(root, max_depth=5, _depth=0):
        if _depth >= max_depth:
            return
        try:
            for entry in root.iterdir():
                if not entry.is_dir() or entry.name in _CMD_SKIP:
                    continue
                # Direct commands/ dir
                if entry.name == "commands" and _is_command_dir(entry):
                    add_dir(entry)
                # .xxx/commands/ (e.g. .claude/commands)
                if entry.name.startswith(".") and not entry.name.startswith(".."):
                    commands_dir = entry / "commands"
                    if commands_dir.is_dir() and _is_command_dir(commands_dir):
                        add_dir(commands_dir)
                    # .xxx/skills/ that only holds .md files is likely misnamed commands
                    skills_dir = entry / "skills"
                    if skills_dir.is_dir() and _is_command_dir(skills_dir):
                        add_dir(skills_dir)
                _scan_project_commands(entry, max_depth, _depth + 1)
        except (PermissionError, OSError):
            pass

    for root_name in ("projects", "code", "workspace", "dev", "work", "src", "repos"):
        root = home / root_name
        if root.is_dir():
            _scan_project_commands(root, max_depth=5)

    # Config files
    cfg = _read_skill_dashboard_config()
    for p in cfg.get("command_paths", []):
        add_dir(Path(p).expanduser())

    # Environment variables
    for p in _env_roots("CLAUDE_COMMAND_ROOTS"):
        add_dir(Path(p).expanduser())

    return [d for d in candidates if _is_command_dir(d)]


def _scan_commands(commands_dirs):
    """Return list of command dicts from command directories."""
    commands = []
    seen = set()
    for cmd_dir in commands_dirs:
        try:
            for f in sorted(cmd_dir.iterdir()):
                if not f.is_file() or f.suffix != ".md":
                    continue
                name = f.stem
                key = f"{cmd_dir}/{name}"
                if key in seen:
                    continue
                seen.add(key)
                commands.append({
                    "name": name,
                    "dir": str(cmd_dir),
                    "agent": _agent_from_path(str(cmd_dir)),
                    "path": str(f),
                })
        except (PermissionError, OSError):
            pass
    return commands


def _scan_global_categories():
    """Scan all skill dirs, classify unique skills, return distribution.
    Cached for 5 minutes. Uses _discover_skill_dirs for full coverage.
    """
    cache_file = CACHE_DIR / "global-categories.json"
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    # Check cache freshness
    if cache_file.exists():
        try:
            cached = json.loads(cache_file.read_text("utf-8"))
            age = time.time() - cached.get("_ts", 0)
            if age < 300:  # 5 min TTL
                return {k: v for k, v in cached.items() if not k.startswith("_")}
        except Exception:
            pass

    skill_dirs = _discover_skill_dirs()

    seen = {}       # name -> description (first seen wins)

    for tdir in skill_dirs:
        for d in sorted(tdir.iterdir()):
            if not d.is_dir():
                continue
            if not (d / "SKILL.md").exists():
                continue
            name = d.name
            if name not in seen:
                seen[name] = _read_skill_description(d)

    # Classify all unique skills
    cat_dist = {}
    for name, desc in seen.items():
        cat = _classify_skill(name, desc)
        cat_dist[cat] = cat_dist.get(cat, 0) + 1

    result = {
        "unique_skills": len(seen),
        "targets_scanned": len(skill_dirs),
        "category_distribution": cat_dist,
    }
    # Save cache with timestamp
    to_cache = dict(result)
    to_cache["_ts"] = time.time()
    cache_file.write_text(json.dumps(to_cache, ensure_ascii=False, indent=2), encoding="utf-8")
    return result
