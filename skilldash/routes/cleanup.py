"""cleanup 域路由 handler:目录治理计划/执行、重复决策、垃圾站(列表/恢复/删除/清空)、批量删除。

从 serve.py 拆出的 mixin。handler 逻辑原样搬出,self 引用不变。治理与决策逻辑在
skilldash.cleanup / skilldash.decisions;_trash_dir 辅助也在此(被 skill 域
_delete_skill 经 MRO 跨域调用)。经顶层 import,无循环依赖。
"""
from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path
from urllib.parse import urlparse, parse_qs, unquote

from skilldash.cleanup import (
    _duplicate_skill_execute_allowed,
    _is_cleanup_execute_allowed,
    build_cleanup_execution_plan,
    build_cleanup_plan,
)
from skilldash.decisions import (
    _duplicate_decision_key,
    _load_duplicate_decisions,
    _save_duplicate_decisions,
)
from skilldash.discovery import _is_skill_entry
from skilldash.paths import DUPLICATE_DECISIONS_FILE, STATE_DIR


class CleanupRoutes:

    def _cleanup_plan(self):
        """Return a conservative dry-run cleanup plan for discovered skill dirs.

        支持 ?dir=<path> 多个 query 参数限定目录范围(透传 restrict_dirs);
        不带 ?dir 时维持原全量行为。scope 仍走 daily/deep 二档。
        """
        query = parse_qs(urlparse(self.path).query)
        scope = query.get("scope", ["daily"])[0]
        if scope not in ("daily", "deep"):
            scope = "daily"
        restrict_dirs = [d for d in query.get("dir", []) if d]
        self._json_response(build_cleanup_plan(self._current_target(), scope, restrict_dirs=restrict_dirs or None))

    def _cleanup_execution_plan(self):
        """Return executable-shaped cleanup actions without applying them.

        同 _cleanup_plan 支持 ?dir= 多个限定目录。
        """
        query = parse_qs(urlparse(self.path).query)
        scope = query.get("scope", ["daily"])[0]
        strategy = query.get("strategy", ["conservative"])[0]
        if scope not in ("daily", "deep"):
            scope = "daily"
        if strategy not in ("conservative", "declutter"):
            strategy = "conservative"
        restrict_dirs = [d for d in query.get("dir", []) if d]
        self._json_response(build_cleanup_execution_plan(self._current_target(), scope, strategy, restrict_dirs=restrict_dirs or None))

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
        """Execute selected cleanup candidate actions. Skills collected from the
        whole request; >=2 pack into one trash package (kind:package), 1 stays single."""
        body = self._read_json() or {}
        actions = body.get("actions", [])
        if not isinstance(actions, list) or not actions:
            self._json_response({"error": "actions is empty"}, status=400)
            return

        ok, fail, skipped = 0, 0, 0
        changed_paths = []
        details = []
        all_skill_dirs = []
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
                try:
                    skill_dirs = [d for d in sorted(skills_dir.iterdir(), key=lambda x: x.name.lower())
                                  if (d.is_dir() or d.is_symlink()) and (d / "SKILL.md").exists()]
                except Exception as e:
                    fail += 1
                    details.append({"path": str(skills_dir), "status": "failed", "reason": str(e)})
                    continue
                collected = 0
                cap_hit = False
                for skill_dir in skill_dirs:
                    if len(all_skill_dirs) >= max_skills:
                        cap_hit = True
                        skipped += 1
                        continue
                    all_skill_dirs.append(skill_dir)
                    collected += 1
                changed_paths.append(str(skills_dir))
                details.append({"path": str(skills_dir), "status": "collected", "collected": collected, "cap_hit": cap_hit})
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
            if len(all_skill_dirs) >= max_skills:
                skipped += 1
                details.append({"path": path, "name": skill_name, "status": "skipped", "reason": "safety cap reached"})
                continue
            skills_dir = Path(path).expanduser().resolve()
            all_skill_dirs.append(skills_dir / skill_name)
            changed_paths.append(str(skills_dir))
            details.append({"path": str(skills_dir), "name": skill_name, "status": "collected"})

        # Unified move: 1 skill -> single trash dir; >=2 -> one package
        package_id = ""
        if len(all_skill_dirs) == 1:
            try:
                self._trash_dir(all_skill_dirs[0])
                ok = 1
            except Exception as e:
                fail += 1
                details.append({"status": "failed", "reason": str(e)})
        elif len(all_skill_dirs) > 1:
            try:
                dest, moved, pkg_failed = self._trash_package(all_skill_dirs, "cleanup_execute")
                ok = moved
                fail += len(pkg_failed)
                package_id = dest.name
                if pkg_failed:
                    details.append({"package": package_id, "failed": pkg_failed[:10]})
            except Exception as e:
                fail += len(all_skill_dirs)
                details.append({"status": "failed", "reason": str(e)})

        self._json_response({
            "ok": True,
            "moved": ok,
            "failed": fail,
            "skipped": skipped,
            "package": package_id,
            "changed_paths": changed_paths,
            "details": details,
        })
        self._log_history(
            "move_to_trash",
            paths=changed_paths,
            count=ok,
            source="cleanup_execute",
            status="ok" if fail == 0 else ("failed" if ok == 0 else "partial"),
            detail={"failed": fail, "skipped": skipped, "actions": len(actions), "package": package_id},
        )

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
                skills_list = None
                if kind == "symlink":
                    payload = d / meta.get("payload", meta.get("name", ""))
                    skill_count = 1 if payload.exists() or payload.is_symlink() else 0
                elif kind == "package":
                    skills_meta = meta.get("skills", []) or []
                    skill_count = len(skills_meta)
                    skills_list = [
                        {"name": s.get("name", ""), "original_path": s.get("original_path", "")}
                        for s in skills_meta if isinstance(s, dict)
                    ]
                elif (d / "SKILL.md").exists():
                    skill_count = 1
                    kind = "skill"
                else:
                    skill_count = sum(
                        1 for c in d.iterdir()
                        if (c.is_dir() or c.is_symlink()) and (c / "SKILL.md").exists()
                    ) if d.is_dir() else 0
                    kind = kind or "collection"
                item = {
                    "id": d.name,
                    "name": meta.get("name", d.name),
                    "original_path": meta.get("original_path", ""),
                    "trashed_at": meta.get("trashed_at", ""),
                    "skill_count": skill_count,
                    "kind": kind,
                }
                if skills_list is not None:
                    item["skills"] = skills_list
                items.append(item)
        self._json_response({"items": items, "count": len(items)})

    def _trash_stats(self):
        """Aggregate deletion stats from full history.jsonl (not truncated by /api/history)."""
        hist_file = STATE_DIR / "history.jsonl"
        deleted_total = 0      # move_to_trash count: ever moved into trash
        permanent_total = 0    # empty_trash + delete count: permanently gone
        empty_count = 0
        move_ops = 0
        last_delete_ts = ""
        if hist_file.is_file():
            try:
                with open(hist_file, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            rec = json.loads(line)
                        except Exception:
                            continue
                        op = rec.get("op", "")
                        cnt = rec.get("count", 0) or 0
                        if op == "move_to_trash":
                            deleted_total += cnt
                            move_ops += 1
                        elif op == "empty_trash":
                            permanent_total += cnt
                            empty_count += 1
                            ts = rec.get("ts", "")
                            if ts:
                                last_delete_ts = ts
                        elif op == "delete":
                            permanent_total += cnt
                            ts = rec.get("ts", "")
                            if ts:
                                last_delete_ts = ts
            except Exception:
                pass
        self._json_response({
            "deleted_total": deleted_total,
            "permanent_total": permanent_total,
            "empty_count": empty_count,
            "move_ops": move_ops,
            "last_delete_ts": last_delete_ts,
        })

    def _restore_trash(self, path):
        """Restore a trashed skill to its original location (or current target)."""
        trash_id = unquote(path.split("/api/trash/")[1].replace("/restore", ""))
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
        # Package: restore each skill to its own original_path, collect per-skill failures
        if meta.get("kind") == "package":
            restored_to, failed_list = [], []
            current_target = self._current_target()
            for s in meta.get("skills", []) or []:
                if not isinstance(s, dict):
                    continue
                sname = s.get("name", "")
                orig = s.get("original_path", "")
                sub = s.get("sub", sname)
                if orig and Path(orig).parent.is_dir():
                    sdest = Path(orig)
                else:
                    sdest = Path(current_target) / sname
                if sdest.exists() or sdest.is_symlink():
                    failed_list.append(f"{sname}: 目标已存在")
                    continue
                src = trash_dir / sub
                if not src.exists() and not src.is_symlink():
                    failed_list.append(f"{sname}: 包内缺失")
                    continue
                try:
                    shutil.move(str(src), str(sdest))
                    restored_to.append(str(sdest))
                except Exception as e:
                    failed_list.append(f"{sname}: {e}")
            # Clean up package shell (meta + empty dir)
            try:
                if meta_path.exists():
                    meta_path.unlink()
                if trash_dir.exists() and not any(trash_dir.iterdir()):
                    shutil.rmtree(trash_dir)
            except Exception:
                pass
            self._invalidate_runtime_caches()
            self._log_history(
                "restore", paths=restored_to, count=len(restored_to),
                source="trash_restore",
                status="ok" if not failed_list else "partial",
                detail={"trash_id": trash_id, "kind": "package", "failed": failed_list},
            )
            self._json_response({
                "ok": True, "kind": "package",
                "restored_to": restored_to, "failed": failed_list,
            })
            return
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
                self._invalidate_runtime_caches()
                self._log_history("restore", paths=[str(dest)], count=1, source="trash_restore", status="ok", detail={"trash_id": trash_id, "kind": "symlink"})
                self._json_response({"ok": True, "restored_to": str(dest)})
                return
            # Remove meta file before moving
            if meta_path.exists():
                meta_path.unlink()
            shutil.move(str(trash_dir), str(dest))
            self._invalidate_runtime_caches()
            self._log_history("restore", paths=[str(dest)], count=1, source="trash_restore", status="ok", detail={"trash_id": trash_id, "kind": "skill"})
            self._json_response({"ok": True, "restored_to": str(dest)})
        except Exception as e:
            self._json_response({"error": str(e)}, status=500)

    def _delete_trash(self, path):
        """Permanently delete a trashed skill."""
        trash_id = unquote(path.split("/api/trash/")[1])
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
            self._invalidate_runtime_caches()
            return dest
        shutil.move(str(skill_dir), str(dest))
        # Save metadata for restore
        meta = {"original_path": str(skill_dir), "trashed_at": ts, "name": skill_dir.name, "kind": "skill"}
        (dest / ".trash-meta.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
        self._invalidate_runtime_caches()
        return dest

    def _trash_package(self, skill_dirs, source_op=""):
        """Move multiple skill dirs into one trash package (one operation = one package).

        Same-name skills (e.g. multi-version cache) disambiguated via sub (name__<i>).
        Returns (dest_path, moved_count, failed_list)."""
        trash = STATE_DIR.parent / "trash"
        trash.mkdir(parents=True, exist_ok=True)
        ts = time.strftime("%Y%m%d_%H%M%S")
        first = skill_dirs[0].name if skill_dirs else "skill"
        pkg_name = first if len(skill_dirs) <= 1 else f"{first}等{len(skill_dirs)}项"
        dest = trash / f"{ts}_{pkg_name}"
        if dest.exists():
            for i in range(100):
                candidate = trash / f"{ts}_{pkg_name}_{i}"
                if not candidate.exists():
                    dest = candidate
                    break
        dest.mkdir(parents=True, exist_ok=False)
        skills_meta = []
        failed = []
        used_subs = set()
        for skill_dir in skill_dirs:
            try:
                base = skill_dir.name
                sub = base
                i = 0
                while sub in used_subs:
                    i += 1
                    sub = f"{base}__{i}"
                used_subs.add(sub)
                target_sub = dest / sub
                if skill_dir.is_symlink():
                    target_sub.mkdir(parents=True, exist_ok=False)
                    payload = target_sub / base
                    shutil.move(str(skill_dir), str(payload))
                    skills_meta.append({
                        "name": base, "original_path": str(skill_dir),
                        "sub": sub, "kind": "symlink",
                        "link_target": os.readlink(payload) if payload.is_symlink() else "",
                    })
                else:
                    shutil.move(str(skill_dir), str(target_sub))
                    skills_meta.append({
                        "name": base, "original_path": str(skill_dir),
                        "sub": sub, "kind": "skill",
                    })
            except Exception as e:
                failed.append(f"{skill_dir.name}: {e}")
        meta = {
            "kind": "package", "name": pkg_name, "trashed_at": ts,
            "source_op": source_op, "skills": skills_meta, "failed": failed,
        }
        (dest / ".trash-meta.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
        self._invalidate_runtime_caches()
        return dest, len(skills_meta), failed
