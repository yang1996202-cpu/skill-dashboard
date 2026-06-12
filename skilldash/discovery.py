"""Skill directory discovery and governance classification."""

from __future__ import annotations

import json
import time
from pathlib import Path

from .classification import _classify_skill, _read_skill_description
from .paths import CACHE_DIR, STATE_DIR


def _agent_from_path(dir_path):
    """Infer agent name from directory path."""
    p = str(dir_path)
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

def _classify_skill_dir(dir_path):
    """Classify a skill directory by its nature based on path patterns.

    Returns one of:
    - 'user'       : User-created skills (main skills/ dir, no marketplace/cache/backup patterns)
    - 'marketplace': Ecosystem/plugin marketplace skills (marketplace, plugins, agent-plugins, extensions)
    - 'cache'      : Installation artifacts (snapshots, backups, cache, plugins-backup, vendor_imports)
    - 'cross-copy' : Cross-agent copies (e.g., gstack/.cursor/skills inside a skill dir)
    - 'project'    : Project-level skills (under ~/projects/)
    """
    p = str(dir_path).lower()
    home = str(Path.home()).lower()
    rel = p.replace(home + "/", "").replace(home + "\\", "")

    # Cache/backup: snapshots, backups, plugin caches, vendor imports
    cache_signals = [".snapshots", "backup", "plugins-backup", "plugins/cache",
                     "/cache/", "vendor_imports", ".tmp", ".temp",
                     "bundled-marketplaces", "/install/cache/"]
    for sig in cache_signals:
        if sig in p:
            return "cache"

    # Project-level: under ~/projects/ — check BEFORE cross-copy
    # because ~/projects/xz/.claude/skills is project-level, not cross-copy
    if "/projects/" in p or "\\projects\\" in p:
        return "project"

    # Cross-agent copy: .<agent>/skills at depth > 0 from home
    # Pattern: any .xxx/skills that is NOT the agent's own root skills dir.
    # Root: ~/.claude/skills (i=0 in rel_parts)
    # Cross-copy: ~/.skillslm/gstack/.cursor/skills (.cursor/skills at depth > 0)
    parts = Path(dir_path).parts
    home_parts = Path(Path.home()).parts
    rel_parts = parts[len(home_parts):]
    for i, pt in enumerate(rel_parts):
        if pt.startswith(".") and not pt.startswith("..") and i + 1 < len(rel_parts) and rel_parts[i + 1] == "skills":
            if i > 0:  # Not at agent root level (i=0 means ~/.agent/skills)
                return "cross-copy"

    # Marketplace: plugin stores, extension stores, agent-plugin repos
    market_signals = ["marketplace", "agent-plugins", "/plugins/", "\\plugins\\",
                      "extensions/", "\\extensions\\"]
    for sig in market_signals:
        if sig in p:
            if sig == "/plugins/" or sig == "\\plugins\\":
                idx = p.find(sig)
                after = p[idx + len(sig):]
                if after and ("/skills" in after or "\\skills" in after):
                    return "marketplace"
            else:
                return "marketplace"

    # Default: user-created
    return "user"

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
    category = _classify_skill_dir(path)
    layer = "user-installed"
    policy = "manage"
    confidence = "medium"
    evidence = []

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

    # Exact agent root, e.g. ~/.claude/skills or ~/.agents/skills.
    if len(rel_parts) == 2 and rel_parts[0].startswith(".") and rel_parts[1] == "skills":
        mark("active-root", "manage", "agent root skills directory", "user")

    # User-level non-hidden collections, e.g. ~/AI-Skills or ~/some/skills.
    elif category == "user":
        mark("user-installed", "manage", "user-level skills collection", "user", "medium")

    # Local app inventories and downloaded packs are useful to review, but are
    # not automatically connected to a runtime.
    if top in ("downloads", "desktop", "documents"):
        mark("downloaded-package", "review", "downloaded or manually unpacked skill package", category)
    if top in ("projects", "code", "workspace"):
        mark("project-local", "review", "project/workspace level skills", "project")
    if top == ".skillslm":
        mark("app-local-library", "review", "SkillsLM local library, not necessarily active in a host", category)

    # Project-local skills are often real, but deleting them can change a repo.
    if category == "project":
        mark("project-local", "review", "project-local skills should be reviewed before deleting", "project")

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

    # Known import buckets are reviewable cleanup candidates, not cache.
    if any(sig in p for sig in ("openclaw-imports", "/imports/")):
        mark("imported-copy", "review", "imported skills bucket", "cross-copy")

    policy_labels = {
        "manage": "可管理",
        "review": "待复核",
        "observe": "只观察",
        "hidden": "默认隐藏",
    }
    layer_labels = {
        "active-root": "当前/Agent 根目录",
        "user-installed": "用户技能库",
        "app-local-library": "App 本地库",
        "downloaded-package": "下载/解包目录",
        "project-local": "项目内技能",
        "imported-copy": "导入/跨 Agent 副本",
        "backup-snapshot": "备份/快照",
        "package-cache": "包管理缓存",
        "plugin-cache": "插件缓存",
        "plugin-marketplace": "插件市场/目录",
        "vendor-bundled": "宿主内置包",
        "fixture-example": "测试样例",
    }
    return {
        "category": category,
        "layer": layer,
        "layer_label": layer_labels.get(layer, layer),
        "policy": policy,
        "policy_label": policy_labels.get(policy, policy),
        "confidence": confidence,
        "evidence": evidence[:4],
        "is_deletable": policy == "manage",
        "is_daily": policy in ("manage", "review"),
    }

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
    home = Path.home()
    candidates = []
    seen_paths = set()
    validated_paths = set()  # dirs already confirmed by _has_skill_md
    _resolved_inodes = set()  # (st_dev, st_ino) for samefile dedup (macOS case-insensitive FS)

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

    def _has_skill_md(d):
        """Check if directory contains at least one */SKILL.md entry."""
        try:
            return any((c.is_dir() or c.is_symlink()) and (c / "SKILL.md").exists() for c in d.iterdir())
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
                is_container = (
                    (entry / "SKILL.md").exists() and _has_skill_md(entry)
                )
                if _has_skill_md(entry):
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
                            if ((sub / "SKILL.md").exists()
                                    and any(
                                        (c.is_dir() and (c / "SKILL.md").exists())
                                        for c in sub.iterdir()
                                    )):
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
                if _has_skill_md(entry):
                    add_dir(entry, _validated=True)
    except (PermissionError, OSError):
        pass

    # 2b. ~/Downloads/ — scan subdirs for skill collections (depth 3)
    downloads = home / "Downloads"
    if downloads.is_dir():
        try:
            for d in downloads.iterdir():
                if not d.is_dir():
                    continue
                if _has_skill_md(d):
                    add_dir(d, _validated=True)
                _scan_agent_deep(d, max_depth=2, _depth=1)
        except (PermissionError, OSError):
            pass

    # 3. ~/projects/*//skills/ — project-level skill directories
    #    Discover non-hidden project roots dynamically instead of hardcoding
    #    case variants (projects/Projects, code/Code) which cause duplicates
    #    on case-insensitive filesystems (macOS).
    _project_roots_seen = set()  # inode dedup for project root dirs
    for entry in home.iterdir():
        if not entry.is_dir() or entry.name.startswith("."):
            continue
        # Only scan common project root names (case-insensitive match)
        if entry.name.lower() not in ("projects", "code", "workspace"):
            continue
        try:
            st = entry.stat()
            inode_key = (st.st_dev, st.st_ino)
            if inode_key in _project_roots_seen:
                continue
            _project_roots_seen.add(inode_key)
        except OSError:
            continue
        try:
            for proj in entry.iterdir():
                if not proj.is_dir():
                    continue
                add_dir(proj / "skills")
                # Check any .xxx/skills/ inside the project
                for sub in proj.iterdir():
                    if sub.is_dir() and sub.name.startswith(".") and not sub.name.startswith(".."):
                        add_dir(sub / "skills")
        except (PermissionError, OSError):
            pass

    # 4. .skill-dashboard.json config files
    home_config = home / ".skill-dashboard.json"
    if home_config.exists():
        try:
            cfg = json.loads(home_config.read_text("utf-8"))
            for p in cfg.get("paths", []):
                add_dir(Path(p).expanduser())
        except Exception:
            pass
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

    # 5. Custom sources (legacy)
    try:
        cf = STATE_DIR / "custom-sources.json"
        if cf.exists():
            for p in json.loads(cf.read_text()):
                add_dir(Path(p).expanduser())
    except Exception:
        pass

    # Filter: must contain SKILL.md entries, exclude Trash
    # Skip re-check for dirs already validated by _has_skill_md during scan
    return [d for d in candidates
            if d.is_dir()
            and ".Trash" not in str(d)
            and (str(d) in validated_paths or any(
                (c.is_dir() or c.is_symlink()) and (c / "SKILL.md").exists()
                for c in d.iterdir()
            ))]

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
