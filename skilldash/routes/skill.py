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
from skilldash.paths import CACHE_DIR
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
        # Validate name (block ../) and contain final path under resolved dir
        safe_name = self._validate_skill_name(name)
        if not safe_name:
            self._json_response({"error": "invalid skill name"}, status=400)
            return
        skill_md = (resolved / safe_name / "SKILL.md").resolve()
        if not skill_md.is_relative_to(resolved):
            self._json_response({"error": "invalid skill name"}, status=400)
            return
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
        reason = query.get("reason", [""])[0] or ""  # 删除原因:broken/same-name/identical/changed/空=未分类(治理成效按此聚合)
        if target:
            raw = Path(target).expanduser()
            # 安全校验:规范化 .. 后必须在 home 下;不跟随 symlink(broken symlink 的断目标
            # 可能不在 home,但我们移的是 symlink 本体,本体路径在 home 下即安全)
            norm = Path(os.path.normpath(str(raw)))
            if not norm.is_relative_to(Path.home()):
                self._json_response({"error": "target must be under home directory"}, status=400)
                return
            # target 语义两可:同名/同内容传父目录(要拼 name);损坏链接传 skill 完整路径
            # (broken symlink 本体,已含 name)。先认 target 本身是不是 skill entry,是就直接用。
            if _is_skill_entry(raw, include_broken=True):
                skill_dir = raw
            else:
                skill_dir = raw / name
            if _is_skill_entry(skill_dir, include_broken=True):
                try:
                    dest = self._trash_dir(skill_dir)
                    self._patch_scan_cache_remove([(name, str(skill_dir.parent))])
                    self._log_history("move_to_trash", paths=[str(skill_dir)], count=1, source="delete_skill", status="ok", detail={"name": name, "target": target, "reason": reason})
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
            self._log_history("move_to_trash", paths=[str(skill_dir)], count=1, source="delete_skill", status="ok", detail={"name": name, "reason": reason})
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
        # 更新成功后 patch scan-result.json 缓存里的 upstream_sources 这条,
        # 否则刷新页面读旧缓存,skill 仍显示「过时」(明明已更新到最新)。
        # 复用 check_upstream_status 拿真实最新状态(installed/latest/status),
        # 不手动拼字段(避免和真实检测对不上)。复用 attach 的 patch 模式但改 upstream 字段。
        if status == "ok":
            self._patch_scan_cache_update(name, target)

    def _patch_scan_cache_update(self, name, target):
        """update 成功后 patch scan-result.json 的 upstream_sources 这条,
        否则刷新页面读旧缓存的 installed_commit/latest_commit/status,
        skill 仍显示「过时」明明已更新到最新(死循环表象)。

        复用 check_upstream_status 拿真实最新状态(installed/latest/status/ahead_by),
        不手动拼字段。与 _patch_scan_cache_attach 对称:attach 加来源时 patch,
        update 改版本时也 patch。scan-result.json 不存在则跳过(下次扫描自然产出)。
        """
        try:
            cf = CACHE_DIR / "scan-result.json"
            if not cf.exists():
                return
            skill_dir = Path(target).expanduser().resolve() / name
            up = check_upstream_status(skill_dir)
            data = json.loads(cf.read_text("utf-8"))
            parent = str(skill_dir.parent)
            changed = False
            for u in data.get("upstream_sources", []):
                if u.get("name") == name and u.get("dir") == parent:
                    u["status"] = up.get("status", "unknown")
                    u["installed_commit"] = up.get("installed_commit", "")
                    u["latest_commit"] = up.get("latest_commit", "")
                    u["ahead_by"] = up.get("ahead_by", 0)
                    u["source"] = up.get("source", u.get("source", "steal-meta"))
                    u["repo"] = up.get("repo", u.get("repo", ""))
                    u["is_symlink"] = up.get("is_symlink", False)
                    u["canonical_dir"] = up.get("canonical_dir", str(skill_dir))
                    changed = True
                    break
            if changed:
                cf.write_text(json.dumps(data, ensure_ascii=False), "utf-8")
        except Exception:
            pass

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

    # ── export / import ──

    def _export_skill(self, name):
        """Zip a skill directory and return as download."""
        import io
        import zipfile

        skill_dir = self._resolve_skill_dir(name)
        if not skill_dir or not skill_dir.is_dir():
            self._json_response({"error": f"Skill '{name}' not found"}, status=404)
            return

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
            for f in sorted(skill_dir.rglob('*')):
                if f.is_file() and '.snapshots' not in f.parts and '.trash' not in f.parts:
                    arcname = str(f.relative_to(skill_dir.parent))
                    zf.write(f, arcname)

        buf.seek(0)
        data = buf.read()
        self.send_response(200)
        self.send_header('Content-Type', 'application/zip')
        self.send_header('Content-Disposition', f'attachment; filename="{name}.zip"')
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-cache')
        self.end_headers()
        self.wfile.write(data)

    def _export_batch(self):
        """Zip multiple skills and return as single download."""
        import io
        import zipfile

        body = self._read_json()
        if not body:
            self._json_response({"ok": False, "error": "无效请求"}, 400)
            return

        names = body.get("names", [])
        if not names:
            self._json_response({"ok": False, "error": "缺少 names"}, 400)
            return

        target = self._current_target()
        target_path = Path(target)
        found = []
        for name in names:
            d = target_path / name
            if d.is_dir() and _is_skill_entry(d):
                found.append((name, d))

        if not found:
            self._json_response({"ok": False, "error": "没找到可导出的 skill"}, 400)
            return

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
            for name, d in found:
                for f in sorted(d.rglob('*')):
                    if f.is_file() and '.snapshots' not in f.parts and '.trash' not in f.parts:
                        arcname = str(f.relative_to(target_path))
                        zf.write(f, arcname)

        buf.seek(0)
        data = buf.read()
        name_label = f"skills-{len(found)}.zip"
        self.send_response(200)
        self.send_header('Content-Type', 'application/zip')
        self.send_header('Content-Disposition', f'attachment; filename="{name_label}"')
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-cache')
        self.end_headers()
        self.wfile.write(data)

    def _import_skill_zip(self):
        """Accept a base64-encoded zip and extract skill directories to current target."""
        import base64
        import io
        import zipfile
        import tempfile

        body = self._read_json()
        if not body:
            self._json_response({"ok": False, "error": "无效请求"}, 400)
            return

        b64 = body.get("data", "")
        if not b64:
            self._json_response({"ok": False, "error": "缺少 data (base64 zip)"}, 400)
            return

        try:
            raw = base64.b64decode(b64)
        except Exception:
            self._json_response({"ok": False, "error": "base64 解码失败"}, 400)
            return

        target = self._current_target()
        target_path = Path(target).expanduser().resolve()
        if not target_path.is_relative_to(Path.home()):
            self._json_response({"ok": False, "error": "目标目录必须在 home 下"}, 400)
            return

        try:
            with zipfile.ZipFile(io.BytesIO(raw)) as zf:
                # Detect top-level structure: single dir or multiple?
                entries = zf.namelist()
                top_dirs = set()
                for e in entries:
                    parts = e.split('/')
                    if parts[0]:
                        top_dirs.add(parts[0])

                installed = []
                skipped = []
                errors = []

                for member in zf.infolist():
                    if member.is_dir():
                        continue
                    # Extract to target, preserving structure
                    dest = target_path / member.filename
                    # Security: ensure dest is under target
                    if not dest.resolve().is_relative_to(target_path):
                        errors.append(f"路径越界拒绝: {member.filename}")
                        continue
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    with zf.open(member) as src:
                        dest.write_bytes(src.read())

                # Verify which have valid SKILL.md
                for td in top_dirs:
                    d = target_path / td
                    if _is_skill_entry(d):
                        installed.append(td)

                self._json_response({
                    "ok": True,
                    "installed": installed,
                    "top_dirs": list(top_dirs),
                    "skipped": skipped,
                    "errors": errors,
                })
                self._log_history(
                    "install",
                    paths=[str(target_path / d) for d in installed],
                    count=len(installed),
                    source="import_zip",
                    status="ok",
                )
        except zipfile.BadZipFile:
            self._json_response({"ok": False, "error": "无效的 zip 文件"}, 400)
            return
