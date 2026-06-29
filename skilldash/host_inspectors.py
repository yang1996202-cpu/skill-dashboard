"""Host-specific runtime inspectors.

The generic dashboard scanner answers "where are SKILL.md files?".  Host
inspectors answer the narrower runtime question: "does this host load this
package, or is it only a local artifact?"  Keep this module dependency-free so
the dashboard still runs on a stock Python install.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

try:
    import tomllib  # Python 3.11+ stdlib; <3.11 → Codex MCP 降级为空
except ModuleNotFoundError:
    tomllib = None


_CODEX_PLUGIN_STATE = None
_CODEX_PLUGIN_STATE_SIG = None
_CLAUDE_PLUGIN_STATE = None
_CLAUDE_PLUGIN_STATE_SIG = None
_BUDDY_STATE_CACHE: dict[str, dict] = {}
_BUDDY_STATE_SIGS: dict[str, tuple] = {}

BUDDY_FAMILY_SPECS = (
    {
        "key": "workbuddy",
        "host": "WorkBuddy",
        "dotdir": ".workbuddy",
        "app_resource_dirs": (
            Path("/Applications/WorkBuddy.app/Contents/Resources/app.asar.unpacked/resources"),
        ),
    },
    {
        "key": "codebuddy",
        "host": "CodeBuddy",
        "dotdir": ".codebuddy",
        "app_resource_dirs": (
            Path("/Applications/CodeBuddy CN.app/Contents/Resources/app/resources"),
            Path("/Applications/CodeBuddy.app/Contents/Resources/app/resources"),
        ),
    },
)


def _read_json_file(path: Path, default):
    try:
        return json.loads(path.read_text("utf-8"))
    except Exception:
        return default


def _norm_path(value):
    if not value:
        return ""
    try:
        return str(Path(value).expanduser()).replace("\\", "/").rstrip("/")
    except Exception:
        return str(value).replace("\\", "/").rstrip("/")


def _path_relative_parts(path: Path, root: Path) -> tuple[str, ...]:
    try:
        return path.resolve().relative_to(root.resolve()).parts
    except Exception:
        return ()


def _part_after(parts: tuple[str, ...], marker: str) -> str:
    try:
        idx = parts.index(marker)
    except ValueError:
        return ""
    if idx + 1 < len(parts):
        return parts[idx + 1]
    return ""


def _count_skill_entries(path: Path) -> int:
    if not path.is_dir():
        return 0
    total = 0
    try:
        for child in path.iterdir():
            if (child.is_dir() or child.is_symlink()) and (child / "SKILL.md").exists():
                total += 1
    except (PermissionError, OSError):
        pass
    return total


def _source_root(kind: str, path: Path, evidence: str) -> dict:
    return {
        "kind": kind,
        "path": str(path),
        "exists": path.exists(),
        "skill_count": _count_skill_entries(path),
        "evidence": evidence,
    }


def load_claude_plugin_state(home: Path | None = None) -> dict:
    """Read non-sensitive Claude plugin runtime state.

    settings.json may contain env secrets, so this helper only returns
    enabledPlugins and installed plugin paths.
    """
    global _CLAUDE_PLUGIN_STATE, _CLAUDE_PLUGIN_STATE_SIG
    home = home or Path.home()
    watched_files = (
        home / ".claude" / "settings.json",
        home / ".claude" / "plugins" / "installed_plugins.json",
        home / ".claude" / "plugins" / "known_marketplaces.json",
    )
    sig = []
    for file_path in watched_files:
        try:
            sig.append((str(file_path), file_path.stat().st_mtime_ns))
        except OSError:
            sig.append((str(file_path), 0))
    sig = tuple(sig)
    if _CLAUDE_PLUGIN_STATE is not None and _CLAUDE_PLUGIN_STATE_SIG == sig:
        return _CLAUDE_PLUGIN_STATE

    settings = _read_json_file(home / ".claude" / "settings.json", {})
    installed_file = _read_json_file(home / ".claude" / "plugins" / "installed_plugins.json", {})
    marketplaces = _read_json_file(home / ".claude" / "plugins" / "known_marketplaces.json", {})

    enabled = {
        key for key, value in (settings.get("enabledPlugins") or {}).items()
        if value
    }
    installed = {}
    installed_by_path = []
    for plugin_id, records in (installed_file.get("plugins") or {}).items():
        if not isinstance(records, list):
            continue
        installed[plugin_id] = []
        for record in records:
            if not isinstance(record, dict):
                continue
            install_path = _norm_path(record.get("installPath"))
            item = {
                "plugin_id": plugin_id,
                "install_path": install_path,
                "version": record.get("version") or "",
                "scope": record.get("scope") or "",
                "git_commit": record.get("gitCommitSha") or "",
            }
            installed[plugin_id].append(item)
            if install_path:
                installed_by_path.append(item)

    _CLAUDE_PLUGIN_STATE = {
        "enabled": enabled,
        "installed": installed,
        "installed_by_path": installed_by_path,
        "marketplaces": marketplaces if isinstance(marketplaces, dict) else {},
    }
    _CLAUDE_PLUGIN_STATE_SIG = sig
    return _CLAUDE_PLUGIN_STATE


def claude_plugin_context(dir_path) -> dict:
    """Return package/runtime metadata for Claude plugin skill directories."""
    path = Path(dir_path).expanduser()
    home = Path.home()
    try:
        rel_parts = path.relative_to(home).parts
    except Exception:
        return {}
    if len(rel_parts) < 4 or rel_parts[0] != ".claude" or rel_parts[1] != "plugins":
        return {}

    section = rel_parts[2]
    state = load_claude_plugin_state(home)
    enabled = state["enabled"]
    installed = state["installed"]
    installed_by_path = state["installed_by_path"]
    norm = _norm_path(path)

    def installed_record_for_path():
        for record in installed_by_path:
            install_path = record.get("install_path", "")
            if install_path and (norm == install_path or norm.startswith(install_path + "/")):
                return record
        return None

    if section == "cache" and len(rel_parts) >= 6:
        marketplace = rel_parts[3]
        plugin = rel_parts[4]
        version = rel_parts[5]
        plugin_id = f"{plugin}@{marketplace}"
        package_root = home.joinpath(*rel_parts[:6])
        record = installed_record_for_path()
        same_plugin_installed = bool(installed.get(plugin_id))
        is_enabled = plugin_id in enabled
        is_orphaned = (package_root / ".orphaned_at").exists()

        if is_orphaned:
            runtime_state = "orphaned"
            runtime_label = "旧包缓存"
            runtime_reason = "同一插件的旧安装包，当前不会作为上下文加载。"
        elif record and is_enabled:
            runtime_state = "loaded"
            runtime_label = "已启用插件"
            runtime_reason = "settings.json enabledPlugins 已启用，且路径匹配 installed_plugins。"
        elif record:
            runtime_state = "installed"
            runtime_label = "已安装未启用"
            runtime_reason = "installed_plugins 记录了该安装包，但 enabledPlugins 未启用。"
        elif same_plugin_installed:
            runtime_state = "stale"
            runtime_label = "非当前安装包"
            runtime_reason = "同名插件另有 installed_plugins 记录，此目录不是当前安装路径。"
        else:
            runtime_state = "cache"
            runtime_label = "插件包缓存"
            runtime_reason = "位于 Claude 插件缓存目录，但未匹配到当前安装记录。"

        return {
            "package_role": "claude-plugin-cache",
            "runtime_state": runtime_state,
            "runtime_label": runtime_label,
            "runtime_reason": runtime_reason,
            "plugin_id": plugin_id,
            "plugin_name": plugin,
            "plugin_marketplace": marketplace,
            "plugin_version": record.get("version") if record else version,
            "plugin_scope": record.get("scope") if record else "",
            "package_root": str(package_root),
            "loaded_elsewhere": False,
            "enabled_by_host": runtime_state == "loaded",
            "host": "Claude Code",
            "host_config_path": str(home / ".claude" / "settings.json"),
        }

    if section == "marketplaces" and len(rel_parts) >= 4:
        marketplace = rel_parts[3]
        plugin = ""
        package_root = home.joinpath(*rel_parts[:4])
        if len(rel_parts) >= 6 and rel_parts[4] in ("plugins", "external_plugins"):
            plugin = rel_parts[5]
            package_root = home.joinpath(*rel_parts[:6])
        elif len(rel_parts) >= 5 and rel_parts[4] != "skills":
            plugin = rel_parts[4]
            package_root = home.joinpath(*rel_parts[:5])
        plugin_id = f"{plugin}@{marketplace}" if plugin else marketplace
        loaded_elsewhere = plugin_id in enabled if plugin else False
        return {
            "package_role": "claude-plugin-marketplace",
            "runtime_state": "catalog",
            "runtime_label": "市场目录",
            "runtime_reason": "本地 marketplace 货架目录，不等于当前会话已加载。"
                              + (" 同名插件已在安装包中启用。" if loaded_elsewhere else ""),
            "plugin_id": plugin_id,
            "plugin_name": plugin,
            "plugin_marketplace": marketplace,
            "plugin_version": "",
            "plugin_scope": "",
            "package_root": str(package_root),
            "loaded_elsewhere": loaded_elsewhere,
            "enabled_by_host": False,
            "host": "Claude Code",
            "host_config_path": str(home / ".claude" / "settings.json"),
        }

    return {}


def _parse_codex_config(path: Path) -> dict:
    """Parse the small subset of Codex TOML needed for plugin state.

    We intentionally avoid a toml dependency.  Codex plugin state is expressed
    as simple sections:

        [plugins."github@openai-curated"]
        enabled = true
    """
    enabled_plugins = set()
    marketplaces: dict[str, dict[str, str]] = {}
    current_plugin = ""
    current_marketplace = ""
    if not path.exists():
        return {"enabled_plugins": enabled_plugins, "marketplaces": marketplaces}

    section_re = re.compile(r"^\s*\[([^\]]+)\]\s*$")
    kv_re = re.compile(r'^\s*([A-Za-z0-9_.-]+)\s*=\s*(.+?)\s*(?:#.*)?$')
    plugin_re = re.compile(r'^plugins\."([^"]+)"$')
    marketplace_re = re.compile(r"^marketplaces\.([A-Za-z0-9_.@+-]+)$")

    try:
        for raw_line in path.read_text("utf-8", errors="ignore").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            section = section_re.match(line)
            if section:
                name = section.group(1)
                plugin_match = plugin_re.match(name)
                marketplace_match = marketplace_re.match(name)
                current_plugin = plugin_match.group(1) if plugin_match else ""
                current_marketplace = marketplace_match.group(1) if marketplace_match else ""
                if current_marketplace:
                    marketplaces.setdefault(current_marketplace, {})
                continue

            kv = kv_re.match(line)
            if not kv:
                continue
            key, value = kv.group(1), kv.group(2).strip()
            if current_plugin and key == "enabled" and value.lower() == "true":
                enabled_plugins.add(current_plugin)
            elif current_marketplace:
                marketplaces[current_marketplace][key] = value.strip().strip('"').strip("'")
    except Exception:
        pass

    return {"enabled_plugins": enabled_plugins, "marketplaces": marketplaces}


def _read_codex_plugin_manifest(package_root: Path) -> dict:
    manifest = _read_json_file(package_root / ".codex-plugin" / "plugin.json", {})
    if not isinstance(manifest, dict):
        return {}
    author = manifest.get("author") or manifest.get("publisher") or {}
    if not isinstance(author, dict):
        author = {"name": str(author)}
    interface = manifest.get("interface") or {}
    if not isinstance(interface, dict):
        interface = {}
    return {
        "name": manifest.get("name") or manifest.get("id") or package_root.name,
        "version": manifest.get("version") or "",
        "description": manifest.get("description") or interface.get("shortDescription") or "",
        "author_name": author.get("name") or "",
        "display_name": interface.get("displayName") or manifest.get("displayName") or "",
        "has_apps": bool(manifest.get("apps") or (package_root / ".app.json").exists()),
        "homepage": manifest.get("homepage") or "",
        "repository": manifest.get("repository") or "",
    }


def _read_codex_app_tool_connectors(cache_dir: Path) -> dict[str, dict]:
    """Return connector names seen in Codex app tool cache.

    This is not a complete source of truth for all app skills, but it is strong
    evidence that a connector has exposed runtime tools at least once.
    """
    connectors: dict[str, dict] = {}
    tools_dir = cache_dir / "codex_apps_tools"
    if not tools_dir.is_dir():
        return connectors
    for file_path in tools_dir.glob("*.json"):
        data = _read_json_file(file_path, {})
        tools = data.get("tools") if isinstance(data, dict) else []
        if not isinstance(tools, list):
            continue
        for item in tools:
            if not isinstance(item, dict):
                continue
            name = item.get("connector_name") or ""
            if not name:
                meta = ((item.get("tool") or {}).get("_meta") or {})
                name = meta.get("connector_name") or ""
            if not name:
                continue
            key = name.strip().lower()
            rec = connectors.setdefault(key, {"name": name, "tool_count": 0})
            rec["tool_count"] += 1
    return connectors


def load_codex_plugin_state(home: Path | None = None) -> dict:
    """Read non-sensitive Codex plugin/app runtime state."""
    global _CODEX_PLUGIN_STATE, _CODEX_PLUGIN_STATE_SIG
    home = home or Path.home()
    codex = home / ".codex"
    config = codex / "config.toml"
    cache_dir = codex / "cache"
    watched = [config]
    watched.extend((cache_dir / "codex_apps_tools").glob("*.json") if (cache_dir / "codex_apps_tools").is_dir() else [])
    sig = []
    for file_path in watched:
        try:
            sig.append((str(file_path), file_path.stat().st_mtime_ns))
        except OSError:
            sig.append((str(file_path), 0))
    sig = tuple(sig)
    if _CODEX_PLUGIN_STATE is not None and _CODEX_PLUGIN_STATE_SIG == sig:
        return _CODEX_PLUGIN_STATE

    config_state = _parse_codex_config(config)
    connectors = _read_codex_app_tool_connectors(cache_dir)
    _CODEX_PLUGIN_STATE = {
        "enabled_plugins": config_state["enabled_plugins"],
        "marketplaces": config_state["marketplaces"],
        "connectors": connectors,
    }
    _CODEX_PLUGIN_STATE_SIG = sig
    return _CODEX_PLUGIN_STATE


def codex_plugin_context(dir_path) -> dict:
    """Return package/runtime metadata for Codex plugin skill directories."""
    path = Path(dir_path).expanduser()
    home = Path.home()
    try:
        rel_parts = path.relative_to(home).parts
    except Exception:
        return {}
    if len(rel_parts) < 2 or rel_parts[0] != ".codex":
        return {}
    codex_root = home / ".codex"

    def _pkg(role, state, label, reason, **extra):
        return {
            "package_role": role,
            "runtime_state": state,
            "runtime_label": label,
            "runtime_reason": reason,
            "plugin_id": extra.get("plugin_id", ""),
            "plugin_name": extra.get("plugin_name", ""),
            "plugin_marketplace": extra.get("plugin_marketplace", ""),
            "plugin_version": "",
            "plugin_scope": "codex",
            "package_root": extra.get("package_root", str(path)),
            "loaded_elsewhere": False,
            "enabled_by_host": state in ("enabled", "connector", "builtin"),
            "host": "Codex",
            "host_config_path": str(codex_root / "config.toml"),
        }

    # ~/.codex/skills/.system/* — Codex 注入的系统/元技能(skill-creator 等),非用户自建
    if len(rel_parts) >= 3 and rel_parts[1] == "skills" and rel_parts[2] == ".system":
        skill = rel_parts[3] if len(rel_parts) > 3 else ""
        return _pkg("codex-system-skill", "builtin", "Codex 系统技能",
                    "~/.codex/skills/.system 是 Codex 注入的系统/元技能(如 skill-creator),非用户自建;删除会影响 Codex 技能创建功能。",
                    plugin_name=skill,
                    package_root=str(codex_root / "skills" / ".system" / skill) if skill else str(codex_root / "skills" / ".system"))

    # ~/.codex/vendor_imports/skills/**/.curated/* — vendor 导入的精选技能包
    if rel_parts[1] == "vendor_imports" and "skills" in rel_parts:
        skill = rel_parts[-1] if rel_parts[-1] != "skills" else ""
        return _pkg("codex-vendor-curated", "catalog", "vendor 精选目录",
                    "~/.codex/vendor_imports/skills 是 Codex 从上游 vendor 导入的精选技能目录,非用户安装,作为来源库存参考,不等于当前已启用。",
                    plugin_name=skill, package_root=str(path))

    # ~/.codex/.tmp/plugins/plugins/*/skills/* — 插件市场全量暂存镜像(非用户技能库)
    if len(rel_parts) >= 3 and rel_parts[1] == ".tmp" and rel_parts[2] == "plugins":
        plugin = rel_parts[4] if len(rel_parts) > 4 and rel_parts[3] == "plugins" else ""
        return _pkg("codex-plugin-staging", "cache", "插件市场暂存",
                    "~/.codex/.tmp/plugins 是 Codex 拉取的插件市场全量暂存镜像,非用户安装的技能库,默认隐藏。",
                    plugin_name=plugin, package_root=str(path))

    # ~/.codex/.tmp/bundled-marketplaces/<marketplace>/plugins/*/skills/* — 打包市场货架
    if len(rel_parts) >= 3 and rel_parts[1] == ".tmp" and rel_parts[2] == "bundled-marketplaces":
        marketplace = rel_parts[3] if len(rel_parts) > 3 else ""
        plugin = rel_parts[5] if len(rel_parts) > 5 and rel_parts[4] == "plugins" else ""
        return _pkg("codex-bundled-marketplace", "catalog", "打包市场货架",
                    f"~/.codex/.tmp/bundled-marketplaces 是 Codex 打包内置的市场货架({marketplace or '未知'}),与 config.toml [marketplaces.{marketplace}] 对应,不等于当前已加载。",
                    plugin_id=f"{plugin}@{marketplace}" if plugin else marketplace,
                    plugin_name=plugin, plugin_marketplace=marketplace, package_root=str(path))

    # ~/.codex/.tmp/legacy-primary-runtime-skills/*-<hash> — 旧版运行时历史遗留
    if len(rel_parts) >= 3 and rel_parts[1] == ".tmp" and rel_parts[2] == "legacy-primary-runtime-skills":
        return _pkg("codex-legacy-runtime", "cache", "旧版运行时遗留",
                    "~/.codex/.tmp/legacy-primary-runtime-skills 是 Codex 旧版 primary runtime 的历史遗留(带时间戳 hash),已被新版替代。",
                    plugin_name=rel_parts[-1] if rel_parts[-1] != "legacy-primary-runtime-skills" else "",
                    package_root=str(path))

    # ~/.codex/.tmp/plugins-backup-* — 插件备份(随机后缀,自动生成)
    if len(rel_parts) >= 3 and rel_parts[1] == ".tmp" and rel_parts[2].startswith("plugins-backup"):
        return _pkg("codex-plugin-backup", "cache", "插件备份",
                    "~/.codex/.tmp/plugins-backup-* 是 Codex 自动生成的插件备份(随机后缀),历史快照,默认隐藏。",
                    plugin_name=rel_parts[2], package_root=str(path))

    if len(rel_parts) < 7 or rel_parts[1] != "plugins" or rel_parts[2] != "cache":
        return {}

    marketplace = rel_parts[3]
    plugin = rel_parts[4]
    version = rel_parts[5]
    package_root = home.joinpath(*rel_parts[:6])
    plugin_id = f"{plugin}@{marketplace}"
    state = load_codex_plugin_state(home)
    enabled_plugins = state["enabled_plugins"]
    connectors = state["connectors"]
    manifest = _read_codex_plugin_manifest(package_root)
    display_name = manifest.get("display_name") or manifest.get("name") or plugin
    connector_key = display_name.lower()
    connector_tool_state = connectors.get(connector_key) or connectors.get(plugin.lower())

    same_plugin_enabled = any(item.split("@", 1)[0] == plugin for item in enabled_plugins)
    is_enabled = plugin_id in enabled_plugins
    is_remote_connector = marketplace.endswith("-remote")

    if is_enabled:
        runtime_state = "enabled"
        runtime_label = "已启用插件"
        runtime_reason = "Codex config.toml 中该 plugin enabled=true。"
        loaded_elsewhere = False
    elif connector_tool_state:
        runtime_state = "connector"
        runtime_label = "连接器工具已暴露"
        runtime_reason = f"Codex app 工具缓存中发现 {connector_tool_state.get('tool_count', 0)} 个 {connector_tool_state.get('name', display_name)} 工具。"
        loaded_elsewhere = bool(same_plugin_enabled)
    elif is_remote_connector:
        runtime_state = "connector"
        runtime_label = "远程连接器包"
        runtime_reason = "openai-curated-remote/app 插件包，通常由 Codex 连接器运行时按需暴露。"
        loaded_elsewhere = bool(same_plugin_enabled)
    elif same_plugin_enabled:
        runtime_state = "stale"
        runtime_label = "同名插件另处启用"
        runtime_reason = "同名插件在 Codex config.toml 中启用，但不是这个 marketplace/version 目录。"
        loaded_elsewhere = True
    else:
        runtime_state = "cache"
        runtime_label = "仅缓存"
        runtime_reason = "位于 Codex 插件缓存目录，但未在 config.toml 或 app 工具缓存中找到启用证据。"
        loaded_elsewhere = False

    return {
        "package_role": "codex-plugin-package",
        "runtime_state": runtime_state,
        "runtime_label": runtime_label,
        "runtime_reason": runtime_reason,
        "plugin_id": plugin_id,
        "plugin_name": plugin,
        "plugin_marketplace": marketplace,
        "plugin_version": manifest.get("version") or version,
        "plugin_scope": "codex",
        "plugin_display_name": display_name,
        "plugin_author": manifest.get("author_name") or "",
        "plugin_description": manifest.get("description") or "",
        "package_root": str(package_root),
        "loaded_elsewhere": loaded_elsewhere,
        "enabled_by_host": runtime_state in ("enabled", "connector"),
        "host": "Codex",
        "host_config_path": str(home / ".codex" / "config.toml"),
    }


def _connector_key(name: str) -> str:
    key = (name or "").strip().lower()
    if key.startswith("connector:"):
        key = key.split(":", 1)[1]
    if key.startswith("connector-"):
        key = key[len("connector-"):]
    return key


def load_buddy_family_state(dotdir: str, home: Path | None = None) -> dict:
    """Read non-sensitive Buddy-family connector state.

    WorkBuddy/CodeBuddy connector configs may contain auth headers, env values,
    or cookies.  This loader deliberately keeps only connector names and
    enabled/disabled booleans.
    """
    home = home or Path.home()
    root = home / dotdir
    state_files = list((root / "connectors").glob("*/connector-states.json")) if (root / "connectors").is_dir() else []
    sig = []
    for file_path in state_files:
        try:
            sig.append((str(file_path), file_path.stat().st_mtime_ns))
        except OSError:
            sig.append((str(file_path), 0))
    sig = tuple(sig)
    if dotdir in _BUDDY_STATE_CACHE and _BUDDY_STATE_SIGS.get(dotdir) == sig:
        return _BUDDY_STATE_CACHE[dotdir]

    enabled = set()
    connected = set()
    disabled = set()
    for file_path in state_files:
        data = _read_json_file(file_path, {})
        if not isinstance(data, dict):
            continue
        for value in data.get("enabled") or []:
            enabled.add(_connector_key(str(value)))
        for value in data.get("everConnected") or []:
            connected.add(_connector_key(str(value)))
        for value in data.get("userDisabled") or []:
            disabled.add(_connector_key(str(value)))

    state = {
        "enabled_connectors": enabled,
        "connected_connectors": connected,
        "disabled_connectors": disabled,
    }
    _BUDDY_STATE_CACHE[dotdir] = state
    _BUDDY_STATE_SIGS[dotdir] = sig
    return state


def load_workbuddy_state(home: Path | None = None) -> dict:
    """Backward-compatible WorkBuddy state reader."""
    return load_buddy_family_state(".workbuddy", home)


def _buddy_connector_name(rel_parts: tuple[str, ...]) -> str:
    if len(rel_parts) >= 3 and rel_parts[0] == "connectors" and rel_parts[1] == "skills":
        return rel_parts[2]
    if rel_parts and rel_parts[0] == "connectors-marketplace":
        return _part_after(rel_parts, "connectors") or (rel_parts[-2] if rel_parts[-1] == "skills" and len(rel_parts) >= 2 else rel_parts[-1])
    return ""


def _buddy_marketplace_skill_name(rel_parts: tuple[str, ...]) -> str:
    if rel_parts and rel_parts[0] == "skills-marketplace":
        return _part_after(rel_parts, "skills") or rel_parts[-1]
    return rel_parts[-1] if rel_parts else ""


def _buddy_base(spec: dict, root: Path, path: Path, role, runtime_state, runtime_label, runtime_reason, **extra) -> dict:
    host = spec["host"]
    scope = spec["key"]
    return {
        "package_role": role,
        "runtime_state": runtime_state,
        "runtime_label": runtime_label,
        "runtime_reason": runtime_reason,
        "plugin_id": extra.get("plugin_id", ""),
        "plugin_name": extra.get("plugin_name", ""),
        "plugin_marketplace": extra.get("plugin_marketplace", ""),
        "plugin_version": extra.get("plugin_version", ""),
        "plugin_scope": scope,
        "package_root": extra.get("package_root", str(path)),
        "loaded_elsewhere": False,
        "enabled_by_host": runtime_state in ("user-root", "builtin", "connector"),
        "host": host,
        "host_family": "buddy-family",
        "host_config_path": str(root),
        **{k: v for k, v in extra.items() if k not in {
            "plugin_id", "plugin_name", "plugin_marketplace", "plugin_version", "package_root"
        }},
    }


def _is_buddy_download_snapshot(marketplace: str) -> bool:
    """CodeBuddy/WorkBuddy 从 codebuddy.cn 下载的市场快照,目录名形如
    download_<source>_plugin-marketplace_<canonical>-<UUID>.<时间戳>-<hash>,
    是 canonical marketplace 的下载缓存副本(内容重复)。识别它以归 cache,
    避免污染 catalog 的 marketplace 分组。"""
    if not marketplace.startswith("download_"):
        return False
    return bool(re.search(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", marketplace))


def _buddy_snapshot_marketplace_alias(marketplace: str) -> str:
    """从快照名抽 canonical marketplace,返回可读别名 '<canonical>（下载快照）'。
    抽不到就返回通用 '下载快照'。"""
    m = re.search(r"plugin-marketplace_(.+?)-[0-9a-f]{8}-[0-9a-f]{4}", marketplace)
    return f"{m.group(1)}（下载快照）" if m else "下载快照"


def buddy_family_skill_context(dir_path) -> dict:
    """Return runtime/source metadata for WorkBuddy/CodeBuddy skill directories."""
    path = Path(dir_path).expanduser()
    home = Path.home()
    norm = _norm_path(path)

    for spec in BUDDY_FAMILY_SPECS:
        root = home / spec["dotdir"]
        rel_parts = _path_relative_parts(path, root)

        if rel_parts:
            if len(rel_parts) == 1 and rel_parts[0] == "skills":
                return _buddy_base(
                    spec,
                    root,
                    path,
                    "buddy-user-root",
                    "user-root",
                    "用户自建 Skill",
                    f"~/{spec['dotdir']}/skills 是 {spec['host']} 用户自建技能根目录。",
                    package_root=str(root / "skills"),
                )

            if len(rel_parts) >= 2 and rel_parts[0] == "connectors" and rel_parts[1] == "skills":
                connector = _buddy_connector_name(rel_parts)
                key = _connector_key(connector)
                state = load_buddy_family_state(spec["dotdir"], home)
                disabled = key in state["disabled_connectors"]
                connected = key in state["connected_connectors"] or key in state["enabled_connectors"]
                if disabled:
                    label = "已禁用 Connector"
                    reason = f"{spec['host']} connector 状态显示该连接器被用户禁用；目录保留为 connector skill 包。"
                elif connected:
                    label = "曾连接 Connector"
                    reason = f"{spec['host']} connector 状态显示该连接器曾连接，可作为运行时 connector 能力解释。"
                else:
                    label = "Connector Skill"
                    reason = f"~/{spec['dotdir']}/connectors/skills 下的 connector skill 包。"
                return _buddy_base(
                    spec,
                    root,
                    path,
                    "buddy-connector",
                    "connector",
                    label,
                    reason,
                    plugin_id=connector or f"{spec['key']}-connectors",
                    plugin_name=connector,
                    package_root=str(root / "connectors" / "skills" / connector) if connector else str(root / "connectors" / "skills"),
                    buddy_connector_state="disabled" if disabled else "connected" if connected else "available",
                )

            if rel_parts[0] == "connectors-marketplace":
                connector = _buddy_connector_name(rel_parts) or "connectors-marketplace"
                package_root = root / "connectors-marketplace" / "connectors" / connector if connector != "connectors-marketplace" else root / "connectors-marketplace"
                # 聚合根(marketplace 本身或 connectors 容器)→ 保留显示:它带着未单独列出的 connector 总数,藏掉会丢总数
                if connector == "connectors-marketplace":
                    return _buddy_base(
                        spec, root, path, "buddy-connector-marketplace", "catalog", "Connector 市场汇总",
                        f"~/{spec['dotdir']}/connectors-marketplace 汇总(connector 总数,部分未单独列出)。",
                        plugin_id=connector, plugin_name=connector, package_root=str(package_root),
                    )
                return _buddy_base(
                    spec,
                    root,
                    path,
                    "buddy-connector-marketplace",
                    "catalog",
                    "Connector 市场",
                    f"~/{spec['dotdir']}/connectors-marketplace 是 {spec['host']} connector 货架目录，不等于当前已加载。",
                    plugin_id=f"{connector}@{spec['key']}-connectors-marketplace",
                    plugin_name=connector,
                    plugin_marketplace=f"{spec['key']}-connectors-marketplace",
                    package_root=str(package_root),
                )

            if rel_parts[0] == "skills-marketplace":
                if "logs" in rel_parts or "review-cache" in rel_parts:
                    return _buddy_base(
                        spec,
                        root,
                        path,
                        "buddy-cache",
                        "cache",
                        "市场缓存",
                        f"{spec['host']} skills-marketplace 日志或 review cache，只作为缓存证据。",
                        package_root=str(root / "skills-marketplace"),
                    )
                skill = _buddy_marketplace_skill_name(rel_parts) or "skills-marketplace"
                package_root = root / "skills-marketplace" / "skills" / skill if skill != "skills-marketplace" else root / "skills-marketplace"
                # 聚合根(skills-marketplace 本身或 skills 容器)→ 保留显示:它带着未单独列出的技能总数(268 等),藏掉会丢总数
                if len(rel_parts) < 3:
                    return _buddy_base(
                        spec, root, path, "buddy-skill-marketplace", "catalog", "Skill 市场汇总",
                        f"~/{spec['dotdir']}/skills-marketplace 汇总(技能总数,部分未单独列出)。",
                        plugin_id=skill, plugin_name=skill, package_root=str(package_root),
                    )
                return _buddy_base(
                    spec,
                    root,
                    path,
                    "buddy-skill-marketplace",
                    "catalog",
                    "Skill 市场",
                    f"~/{spec['dotdir']}/skills-marketplace 是 {spec['host']} skill 货架目录，不等于当前上下文已加载。",
                    plugin_id=f"{skill}@{spec['key']}-skills-marketplace",
                    plugin_name=skill,
                    plugin_marketplace=f"{spec['key']}-skills-marketplace",
                    package_root=str(package_root),
                )

            if len(rel_parts) >= 3 and rel_parts[0] == "plugins" and rel_parts[1] == "marketplaces":
                marketplace = rel_parts[2]
                # 下载快照(codebuddy.cn 下载的 canonical 市场副本,UUID+时间戳缓存名)→ 归 cache,不污染 catalog 分组
                if _is_buddy_download_snapshot(marketplace):
                    alias = _buddy_snapshot_marketplace_alias(marketplace)
                    plugin = rel_parts[4] if len(rel_parts) >= 5 and rel_parts[3] in ("plugins", "external_plugins") else ""
                    # 快照的容器/根(external_plugins/、plugins/,plugin 空)→ 隐藏,内容已被单个插件代表
                    if not plugin:
                        return _buddy_base(
                            spec, root, path, "buddy-cache", "cache", "下载快照容器",
                            f"~/{spec['dotdir']}/plugins/marketplaces/{marketplace} 下载快照的组织目录,隐藏。",
                            plugin_id=alias, plugin_name="", plugin_marketplace=alias,
                            package_root=str(root / "plugins" / "marketplaces" / marketplace),
                            _buddy_hidden=True,
                        )
                    return _buddy_base(
                        spec,
                        root,
                        path,
                        "buddy-cache",
                        "cache",
                        "下载快照(缓存)",
                        f"~/{spec['dotdir']}/plugins/marketplaces/{marketplace} 是从 codebuddy.cn 下载的市场快照副本(UUID+时间戳缓存名),内容与 canonical 市场重复,归缓存。",
                        plugin_id=f"{plugin}@{alias}",
                        plugin_name=plugin,
                        plugin_marketplace=alias,
                        package_root=str(root / "plugins" / "marketplaces" / marketplace),
                    )
                plugin = ""
                if len(rel_parts) >= 5 and rel_parts[3] in ("plugins", "external_plugins"):
                    plugin = rel_parts[4]
                # 容器/根目录(marketplace 根、external_plugins/、plugins/ 子文件夹,plugin 空)→ 隐藏,内容已被单个插件代表
                if not plugin:
                    return _buddy_base(
                        spec, root, path, "buddy-plugin-marketplace", "catalog", "插件市场容器",
                        f"~/{spec['dotdir']}/plugins/marketplaces/{marketplace} 组织目录(external_plugins/plugins),内容已被单个插件代表,隐藏。",
                        plugin_id=marketplace, plugin_name="", plugin_marketplace=marketplace,
                        package_root=str(root / "plugins" / "marketplaces" / marketplace),
                        _buddy_hidden=True,
                    )
                plugin_id = f"{plugin}@{marketplace}"
                return _buddy_base(
                    spec,
                    root,
                    path,
                    "buddy-plugin-marketplace",
                    "catalog",
                    "插件市场 Skill",
                    f"~/{spec['dotdir']}/plugins/marketplaces 下的 {spec['host']} 插件市场目录，不等于当前已加载。",
                    plugin_id=plugin_id,
                    plugin_name=plugin,
                    plugin_marketplace=marketplace,
                    package_root=str(root / "plugins" / "marketplaces" / marketplace),
                )

            if rel_parts[0] == "logs":
                return _buddy_base(
                    spec,
                    root,
                    path,
                    "buddy-cache",
                    "cache",
                    "运行日志 Skill",
                    f"~/{spec['dotdir']}/logs 下的 skill 片段只作为运行日志证据。",
                    package_root=str(root / "logs"),
                )

        for app_resources in spec.get("app_resource_dirs", ()):
            app_parts = _path_relative_parts(path, app_resources)
            if not app_parts:
                continue
            if app_parts[0] == "builtin-skills":
                return _buddy_base(
                    spec,
                    root,
                    path,
                    "buddy-builtin",
                    "builtin",
                    f"{spec['host']} 内置 Skill",
                    f"{spec['host']} app 自带 builtin-skills，属于宿主内置能力。",
                    package_root=str(app_resources / "builtin-skills"),
                )
            if len(app_parts) >= 3 and app_parts[0] == "builtin-plugins" and app_parts[2] == "skills":
                plugin = app_parts[1]
                return _buddy_base(
                    spec,
                    root,
                    path,
                    "buddy-builtin-plugin",
                    "builtin",
                    f"{spec['host']} 内置插件",
                    f"{spec['host']} app 自带 builtin-plugins 技能包，属于宿主内置能力。",
                    plugin_id=f"{plugin}@{spec['key']}-builtin",
                    plugin_name=plugin,
                    package_root=str(app_resources / "builtin-plugins" / plugin),
                )

        if spec["dotdir"] in norm:
            return _buddy_base(
                spec,
                root,
                path,
                "buddy-artifact",
                "cache",
                f"{spec['host']} 制品",
                f"位于 {spec['host']} 数据目录，但未匹配到已知运行态入口。",
                package_root=str(path),
            )

    return {}


def workbuddy_skill_context(dir_path) -> dict:
    """Backward-compatible WorkBuddy-only context reader."""
    ctx = buddy_family_skill_context(dir_path)
    return ctx if ctx.get("host") == "WorkBuddy" else {}


def _mcp_servers_from_data(data) -> dict:
    if not isinstance(data, dict):
        return {}
    servers = data.get("mcpServers")
    if isinstance(servers, dict):
        return servers
    servers = data.get("servers")
    if isinstance(servers, dict):
        return servers
    return {}


def _mcp_transport(config: dict) -> str:
    if not isinstance(config, dict):
        return "unknown"
    explicit = config.get("type") or config.get("transport") or config.get("transportType")
    if explicit:
        return str(explicit)
    if config.get("url"):
        return "http"
    if config.get("command"):
        return "stdio"
    return "unknown"


def _mcp_summary(path: Path, scope: str) -> dict | None:
    """Read an MCP config without exposing commands, URLs, env, or headers."""
    if not path.is_file():
        return None
    data = _read_json_file(path, {})
    servers = _mcp_servers_from_data(data)
    if not servers:
        return {
            "path": str(path),
            "scope": scope,
            "server_count": 0,
            "enabled_count": 0,
            "disabled_count": 0,
            "transports": {},
            "servers": [],
        }
    transport_counts: dict[str, int] = {}
    safe_servers = []
    enabled_count = 0
    disabled_count = 0
    for name, config in sorted(servers.items(), key=lambda item: str(item[0]).lower()):
        cfg = config if isinstance(config, dict) else {}
        disabled = bool(cfg.get("disabled"))
        transport = _mcp_transport(cfg)
        transport_counts[transport] = transport_counts.get(transport, 0) + 1
        if disabled:
            disabled_count += 1
        else:
            enabled_count += 1
        if len(safe_servers) < 30:
            safe_servers.append({
                "name": str(name),
                "transport": transport,
                "disabled": disabled,
            })
    return {
        "path": str(path),
        "scope": scope,
        "server_count": len(servers),
        "enabled_count": enabled_count,
        "disabled_count": disabled_count,
        "transports": transport_counts,
        "servers": safe_servers,
    }


def _mcp_summaries_for_root(root: Path) -> list[dict]:
    paths: list[tuple[Path, str]] = []
    candidates = [
        (root / "mcp.json", "runtime"),
    ]
    if (root / "connectors").is_dir():
        candidates.extend((p, "runtime") for p in (root / "connectors").glob("*/mcp.json"))
    if (root / "connectors-marketplace").is_dir():
        candidates.extend((p, "catalog") for p in (root / "connectors-marketplace").glob("connectors/*/mcp.json"))
    if (root / "skills-marketplace").is_dir():
        candidates.extend((p, "catalog") for p in (root / "skills-marketplace").glob("skills/*/mcp.json"))

    seen = set()
    for path, scope in candidates:
        try:
            key = str(path.resolve())
        except Exception:
            key = str(path)
        if key in seen:
            continue
        seen.add(key)
        paths.append((path, scope))

    summaries = []
    for path, scope in paths:
        summary = _mcp_summary(path, scope)
        if summary:
            summaries.append(summary)
    return summaries


def _profile_from_sources(agent: str, family: str, root: Path, source_roots: list[dict], mcp: list[dict], confidence="medium", app_bundles=None) -> dict:
    existing_roots = [item for item in source_roots if item.get("exists")]
    runtime_mcp = [item for item in mcp if item.get("scope") == "runtime"]
    catalog_mcp = [item for item in mcp if item.get("scope") == "catalog"]
    return {
        "agent": agent,
        "family": family,
        "root": str(root),
        "app_bundles": [str(p) for p in (app_bundles or []) if Path(p).exists()],
        "confidence": confidence,
        "source_roots": source_roots,
        "source_root_count": len(existing_roots),
        "source_kinds": sorted({item["kind"] for item in existing_roots}),
        "mcp": mcp,
        "mcp_config_count": len(mcp),
        "mcp_server_count": sum(item.get("server_count", 0) for item in mcp),
        "mcp_runtime_server_count": sum(item.get("server_count", 0) for item in runtime_mcp),
        "mcp_catalog_server_count": sum(item.get("server_count", 0) for item in catalog_mcp),
        "mcp_enabled_count": sum(item.get("enabled_count", 0) for item in mcp),
        "mcp_disabled_count": sum(item.get("disabled_count", 0) for item in mcp),
    }


def _buddy_profile(spec: dict, home: Path) -> dict | None:
    root = home / spec["dotdir"]
    app_resource_dirs = [p for p in spec.get("app_resource_dirs", ()) if p.exists()]
    has_root = root.exists()
    if not has_root and not app_resource_dirs:
        return None
    source_roots = [
        _source_root("user-skills", root / "skills", f"{spec['host']} 用户自建技能根"),
        _source_root("connectors", root / "connectors" / "skills", f"{spec['host']} connector skill 运行包"),
        _source_root("connector-marketplace", root / "connectors-marketplace" / "connectors", f"{spec['host']} connector 市场货架"),
        _source_root("skill-marketplace", root / "skills-marketplace" / "skills", f"{spec['host']} skill 市场货架"),
        _source_root("plugin-marketplace", root / "plugins" / "marketplaces", f"{spec['host']} 插件市场货架"),
        _source_root("commands", root / "commands", f"{spec['host']} 命令目录"),
    ]
    for app_resources in app_resource_dirs:
        source_roots.extend([
            _source_root("app-builtin-skills", app_resources / "builtin-skills", f"{spec['host']} app 内置技能"),
            _source_root("app-builtin-plugins", app_resources / "builtin-plugins", f"{spec['host']} app 内置插件"),
        ])
    app_bundles = []
    for app_resources in app_resource_dirs:
        # Keep the bundle pointer shallow and readable.
        parts = app_resources.parts
        if "Contents" in parts:
            idx = parts.index("Contents")
            app_bundles.append(Path(*parts[:idx]))
    return _profile_from_sources(
        spec["host"],
        "buddy-family",
        root,
        source_roots,
        _mcp_summaries_for_root(root),
        confidence="high" if has_root else "medium",
        app_bundles=app_bundles,
    )


def _claude_profile(home: Path) -> dict | None:
    root = home / ".claude"
    if not root.exists():
        return None
    source_roots = [
        _source_root("user-skills", root / "skills", "Claude Code 用户技能根"),
        _source_root("commands", root / "commands", "Claude Code slash command 根"),
        _source_root("plugin-cache", root / "plugins" / "cache", "Claude 插件安装包缓存"),
        _source_root("plugin-marketplace", root / "plugins" / "marketplaces", "Claude 插件市场货架"),
    ]
    return _profile_from_sources("Claude Code", "claude-code", root, source_roots, _mcp_summaries_for_root(root), "high")


def _codex_profile(home: Path) -> dict | None:
    root = home / ".codex"
    if not root.exists():
        return None
    source_roots = [
        _source_root("user-skills", root / "skills", "Codex 用户技能根"),
        _source_root("plugin-cache", root / "plugins" / "cache", "Codex 插件安装包缓存"),
        _source_root("app-tool-cache", root / "cache" / "codex_apps_tools", "Codex app/connector 工具缓存"),
    ]
    return _profile_from_sources("Codex", "codex", root, source_roots, _mcp_summaries_for_root(root), "high")


def _openclaw_profile(home: Path) -> dict | None:
    """OpenClaw host profile: exposes the three OpenClaw source roots.

    OpenClaw skills come from three physical sources:
      - bundled:  npm package (node_modules/openclaw/skills) — vendor shipped
      - shared:   ~/.openclaw/skills — symlink layer to ~/.agents/skills
      - workspace:~/.openclaw/workspace/skills — ClawHub market installs
    The bundled root lives outside the home dotdir, so the generic dotdir scanner
    cannot see it; this factory surfaces it explicitly.
    """
    root = home / ".openclaw"
    bundled_roots = _openclaw_bundled_skill_roots()
    if not root.exists() and not bundled_roots:
        return None
    source_roots = [
        _source_root("shared-skills", root / "skills", "OpenClaw shared 链接层(软链到 ~/.agents/skills)"),
        _source_root("workspace-skills", root / "workspace" / "skills", "OpenClaw workspace 市场装技能"),
    ]
    for br in bundled_roots:
        source_roots.append(_source_root("bundled-skills", br, "OpenClaw npm bundled 内置技能"))
    return _profile_from_sources(
        "OpenClaw", "openclaw", root, source_roots,
        _mcp_summaries_for_root(root), "medium",
    )


def _generic_dotdir_profile(dotdir: Path, known_names: set[str]) -> dict | None:
    if dotdir.name in known_names or not dotdir.is_dir():
        return None
    roots = [
        _source_root("user-skills", dotdir / "skills", "通用 Agent skills 根"),
        _source_root("commands", dotdir / "commands", "通用 Agent commands 根"),
        _source_root("plugin-cache", dotdir / "plugins" / "cache", "通用插件缓存"),
        _source_root("plugin-marketplace", dotdir / "plugins" / "marketplaces", "通用插件市场货架"),
        _source_root("connectors", dotdir / "connectors" / "skills", "通用 connector skill 包"),
        _source_root("connector-marketplace", dotdir / "connectors-marketplace" / "connectors", "通用 connector 市场货架"),
        _source_root("skill-marketplace", dotdir / "skills-marketplace" / "skills", "通用 skill 市场货架"),
    ]
    mcp = _mcp_summaries_for_root(dotdir)
    if not any(item["exists"] for item in roots) and not mcp:
        return None
    agent_map = {
        ".cursor": "Cursor",
        ".agents": "通用 Agents",
        ".hermes": "Hermes",
        ".qclaw": "QClaw",
        ".cola": "Cola",
        ".alice": "Alice",
    }
    agent = agent_map.get(dotdir.name, dotdir.name.lstrip("."))
    return _profile_from_sources(agent, "generic-dotdir", dotdir, roots, mcp, "low")


def discover_host_profiles(home: Path | None = None) -> list[dict]:
    """Discover non-secret host profiles for scanner planning and UI context."""
    home = home or Path.home()
    profiles = []
    known_dotdirs = {spec["dotdir"] for spec in BUDDY_FAMILY_SPECS} | {".claude", ".codex", ".openclaw"}

    for factory in (_claude_profile, _codex_profile, _openclaw_profile):
        profile = factory(home)
        if profile:
            profiles.append(profile)
    for spec in BUDDY_FAMILY_SPECS:
        profile = _buddy_profile(spec, home)
        if profile:
            profiles.append(profile)

    try:
        for entry in sorted(home.iterdir(), key=lambda p: p.name.lower()):
            if not entry.name.startswith(".") or entry.name.startswith("..") or entry.name == ".Trash":
                continue
            profile = _generic_dotdir_profile(entry, known_dotdirs)
            if profile:
                profiles.append(profile)
    except (PermissionError, OSError):
        pass

    return profiles


def host_profile_summaries_by_agent(home: Path | None = None) -> dict[str, dict]:
    """Return compact profiles suitable for attaching to /api/targets groups."""
    summaries = {}
    for profile in discover_host_profiles(home):
        summaries[profile["agent"]] = {
            "agent": profile["agent"],
            "family": profile["family"],
            "confidence": profile["confidence"],
            "source_root_count": profile["source_root_count"],
            "source_kinds": profile["source_kinds"],
            "mcp_config_count": profile["mcp_config_count"],
            "mcp_server_count": profile["mcp_server_count"],
            "mcp_runtime_server_count": profile["mcp_runtime_server_count"],
            "mcp_catalog_server_count": profile["mcp_catalog_server_count"],
            "mcp_enabled_count": profile["mcp_enabled_count"],
            "mcp_disabled_count": profile["mcp_disabled_count"],
        }
    return summaries


# ── MCP inventory (cross-agent, non-sensitive) ──────────────────────────────
# Claude(~/.claude.json)/ Codex(config.toml)/ Cursor(mcp.json)/ 项目级(.mcp.json)
# 只返回 server 名/transport/disabled;不返回 url/command/args/env/headers。


def _mcp_server_list(servers_dict):
    """{name: cfg} → [{name, transport, disabled}],strip sensitive fields。"""
    if not isinstance(servers_dict, dict):
        return []
    out = []
    for name, cfg in sorted(servers_dict.items(), key=lambda x: str(x[0]).lower()):
        c = cfg if isinstance(cfg, dict) else {}
        out.append({
            "name": str(name),
            "transport": _mcp_transport(c),
            "disabled": bool(c.get("disabled")),
        })
    return out


def _claude_mcp_sources(home):
    """~/.claude.json:顶层 mcpServers(user)+ projects[path].mcpServers(local)。"""
    path = home / ".claude.json"
    if not path.is_file():
        return []
    data = _read_json_file(path, {})
    sources = []
    user = data.get("mcpServers") or {}
    if isinstance(user, dict) and user:
        sources.append({"scope": "user", "scope_label": "全局(user)",
                        "path": str(path), "servers": _mcp_server_list(user)})
    projects = data.get("projects") or {}
    if isinstance(projects, dict):
        for proj, pcfg in projects.items():
            if not isinstance(pcfg, dict):
                continue
            ps = pcfg.get("mcpServers") or {}
            if isinstance(ps, dict) and ps:
                sources.append({"scope": "local", "scope_label": "项目私有(local)",
                                "path": proj, "servers": _mcp_server_list(ps)})
    return sources


def _codex_mcp_sources(home):
    """~/.codex/config.toml [mcp_servers.*] via tomllib。
    子表(.env/.http_headers)tomllib 嵌成子 dict,不误判为独立 server。"""
    if tomllib is None:
        return []
    path = home / ".codex" / "config.toml"
    if not path.is_file():
        return []
    try:
        with open(path, "rb") as f:
            data = tomllib.load(f)
    except Exception:
        return []
    servers = _mcp_server_list(data.get("mcp_servers") or {})
    if not servers:
        return []
    return [{"scope": "global", "scope_label": "全局(config.toml)",
             "path": str(path), "servers": servers}]


def _cursor_mcp_sources(home):
    """~/.cursor/mcp.json(复用 _mcp_summary 的字段裁剪)。"""
    summary = _mcp_summary(home / ".cursor" / "mcp.json", "global")
    if not summary or not summary.get("servers"):
        return []
    return [{"scope": "global", "scope_label": "全局(mcp.json)",
             "path": summary["path"], "servers": summary["servers"]}]


def _project_mcp_sources(home):
    """~/projects/*/.mcp.json(team scope)。"""
    sources = []
    root = home / "projects"
    if not root.is_dir():
        return sources
    for proj in sorted(root.iterdir()):
        if not proj.is_dir():
            continue
        summary = _mcp_summary(proj / ".mcp.json", "project")
        if summary and summary.get("servers"):
            sources.append({"scope": "project", "scope_label": "团队共享(.mcp.json)",
                            "path": summary["path"], "servers": summary["servers"]})
    return sources


_MCP_INVENTORY = None
_MCP_INVENTORY_SIG = None


def load_mcp_inventory(home=None):
    """Aggregate MCP servers across Claude/Codex/Cursor/project configs.

    照抄 load_claude_plugin_state 的 mtime 签名缓存。返回非敏感清单:
    server 名/transport/disabled per agent;不含 url/command/args/env/headers。
    """
    global _MCP_INVENTORY, _MCP_INVENTORY_SIG
    home = home or Path.home()
    watched = [
        home / ".claude.json",
        home / ".codex" / "config.toml",
        home / ".cursor" / "mcp.json",
    ]
    proot = home / "projects"
    if proot.is_dir():
        for proj in proot.iterdir():
            if proj.is_dir():
                watched.append(proj / ".mcp.json")
    # per-file try:单个文件缺失/broken symlink 只给自己置 0,不影响其他文件的
    # mtime 追踪。生成器+外层 try 会因任一失败把全部置 0、签名恒定、缓存锁死。
    sig = []
    for p in watched:
        try:
            sig.append((str(p), p.stat().st_mtime_ns))
        except OSError:
            sig.append((str(p), 0))
    sig = tuple(sig)
    if _MCP_INVENTORY is not None and _MCP_INVENTORY_SIG == sig:
        return _MCP_INVENTORY

    blocks = []
    for agent, family, fn in (
        ("Claude Code", "claude-code", _claude_mcp_sources),
        ("Codex", "codex", _codex_mcp_sources),
        ("Cursor", "cursor", _cursor_mcp_sources),
    ):
        sources = [s for s in fn(home) if s.get("servers")]
        if sources:
            blocks.append({"agent": agent, "family": family, "sources": sources})
    proj_sources = [s for s in _project_mcp_sources(home) if s.get("servers")]
    if proj_sources:
        blocks.append({"agent": "项目级(.mcp.json)", "family": "project", "sources": proj_sources})

    total = sum(len(s["servers"]) for b in blocks for s in b["sources"])
    _MCP_INVENTORY = {"agents": blocks, "totals": {"agents": len(blocks), "servers": total}}
    _MCP_INVENTORY_SIG = sig
    return _MCP_INVENTORY


# macOS app-embedded agents that install standard SKILL.md skills under
# ~/Library/Application Support/<app>/. discovery.py skips ~/Library in its main
# scan (to avoid traversing huge app data dirs like Chrome), so these are picked
# up here by app name + dynamic skills/ lookup + SKILL.md check.
# Add new app-embedded agents to this tuple.
_APP_EMBEDDED_AGENTS = ("CherryStudio", "kimi-desktop")


def _app_embedded_skill_roots(home: Path) -> list[Path]:
    """Find standard SKILL.md roots inside known macOS app-embedded agents.

    Scans ~/Library/Application Support/<app>/ to depth 3 for any `skills/`
    subdir containing SKILL.md entries. App names are pinned (stable); the
    skills/ path is discovered dynamically to tolerate layout changes between
    versions (e.g. kimi-desktop moved skills under daimon-share/daimon/).
    """
    app_support = home / "Library" / "Application Support"
    if not app_support.is_dir():
        return []
    roots: list[Path] = []
    for app_name in _APP_EMBEDDED_AGENTS:
        app_dir = app_support / app_name
        if not app_dir.is_dir():
            continue
        base = len(app_dir.parts)
        try:
            for root, dirs, _files in os.walk(app_dir):
                if len(Path(root).parts) - base >= 3:
                    dirs[:] = []
                    continue
                # 大小写不敏感:CherryStudio 用 "Skills"(大写 S),Kimi 用 "skills"
                skills_name = next((d for d in dirs if d.lower() == "skills"), None)
                if skills_name:
                    sd = Path(root) / skills_name
                    if _count_skill_entries(sd) > 0:
                        roots.append(sd)
        except (PermissionError, OSError):
            pass
    return roots


def known_host_app_skill_roots() -> list[Path]:
    """Return known host app skill roots discovered from host-specific profiles."""
    home = Path.home()
    roots: list[Path] = []
    for spec in BUDDY_FAMILY_SPECS:
        for app_resources in spec.get("app_resource_dirs", ()):
            builtin = app_resources / "builtin-skills"
            if builtin.is_dir():
                roots.append(builtin)
            builtin_plugins = app_resources / "builtin-plugins"
            if not builtin_plugins.is_dir():
                continue
            try:
                for plugin_dir in builtin_plugins.iterdir():
                    skills_dir = plugin_dir / "skills"
                    if skills_dir.is_dir():
                        roots.append(skills_dir)
            except (PermissionError, OSError):
                pass
    roots.extend(_app_embedded_skill_roots(home))
    roots.extend(_openclaw_bundled_skill_roots())
    return roots


def _openclaw_bundled_skill_roots() -> list[Path]:
    """Discover OpenClaw npm-bundled skill roots.

    OpenClaw ships ~57 skills inside its npm package (node_modules/openclaw/skills).
    The package lives in npm-global prefix (commonly ~/.npm-global/lib/node_modules)
    or wherever the user installed it. We probe a few candidate locations and fall
    back to `npm root -g`. Returns the discovered skills/ directory per package.
    """
    roots: list[Path] = []
    seen: set[tuple] = set()
    home = Path.home()
    # Candidate npm-global prefixes (most common layouts).
    candidates = [
        home / ".npm-global" / "lib" / "node_modules" / "openclaw" / "skills",
        home / ".nvm" / "versions" / "node",  # nvm: handled via npm root -g below
    ]
    for cand in candidates:
        try:
            if cand.is_dir() and _count_skill_entries(cand) > 0:
                st = cand.stat()
                key = (st.st_dev, st.st_ino)
                if key not in seen:
                    seen.add(key)
                    roots.append(cand)
        except OSError:
            continue
    # Fallback: ask npm where its global root is, then append openclaw/skills.
    try:
        import subprocess
        result = subprocess.run(
            ["npm", "root", "-g"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            gpath = result.stdout.strip()
            if gpath:
                bundled = Path(gpath) / "openclaw" / "skills"
                try:
                    if bundled.is_dir() and _count_skill_entries(bundled) > 0:
                        st = bundled.stat()
                        key = (st.st_dev, st.st_ino)
                        if key not in seen:
                            seen.add(key)
                            roots.append(bundled)
                except OSError:
                    pass
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        pass
    return roots


def plugin_context_for_dir(dir_path) -> dict:
    """Return normalized host runtime metadata for a skill directory.

    Each host adapter keeps its private parsing rules, but the returned shape is
    shared by the dashboard UI and cleanup governance code.
    """
    return claude_plugin_context(dir_path) or codex_plugin_context(dir_path) or buddy_family_skill_context(dir_path)
