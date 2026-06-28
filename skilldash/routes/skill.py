"""skill 域路由 handler:单 skill 内容/预览、上游检查/更新、修复、删除、复制、rehash。

从 serve.py 拆出的 mixin。handler 逻辑原样搬出,self 引用不变。上游业务函数
(check_upstream_status/update_skill/create_snapshot)在 skilldash.source_ops;
hash 追踪在 skilldash.content_hash;经顶层 import,无循环依赖。
"""
from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path
from urllib.parse import urlparse, parse_qs

from skilldash.content_hash import record_content_hash
from skilldash.discovery import _agent_from_path, _is_skill_entry
from skilldash.source_ops import check_upstream_status, create_snapshot, update_skill


class SkillRoutes:

    def _preview_route(self):
        qs = parse_qs(urlparse(self.path).query)
        preview_dir = qs.get("dir", [""])[0]
        preview_name = qs.get("name", [""])[0]
        if preview_dir and preview_name:
            self._serve_preview(preview_dir, preview_name)
        else:
            self.send_error(400, "Missing dir or name")

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

    def _serve_preview(self, dir_path, name):
        """Preview SKILL.md from any directory (no target switch needed).
        Query param ?full=1 returns full content instead of 500-char preview."""
        resolved = Path(dir_path).resolve()
        is_app_builtin = resolved.is_relative_to(Path("/Applications")) and ".app/" in str(resolved)
        if not (resolved.is_relative_to(Path.home()) or is_app_builtin):
            self._json_response({"error": "dir must be under home directory or a discovered app bundle"}, status=403)
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

    def _delete_skill(self, name):
        """Move a skill to trash. If ?target= is given, delete from that dir."""
        query = parse_qs(urlparse(self.path).query)
        target = query.get("target", [""])[0] or None
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
                    self._patch_scan_cache_remove([(name, str(skill_dir.parent))])
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
            self._patch_scan_cache_remove([(name, str(skill_dir.parent))])
            self._log_history("move_to_trash", paths=[str(skill_dir)], count=1, source="delete_skill", status="ok", detail={"name": name})
            self._json_response({"ok": True, "name": name, "trashed": str(dest)})
        except Exception as e:
            self._json_response({"error": str(e)}, status=500)

    def _resolve_skill_dir(self, name):
        """Find skill directory on disk. Uses current target first."""
        # 1) Current target (always check first)
        target = self._current_target()
        candidates = [Path(target) / name]
        # 2) Fallback: ~/.claude/skills
        candidates.append(Path.home() / ".claude/skills" / name)
        for d in candidates:
            if d.exists() or _is_skill_entry(d, include_broken=True):
                return d
        return None

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

    def _fix_skill(self, name):
        """Fix a skill issue."""
        body = self._read_json() or {}
        action = body.get("action", "")
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
