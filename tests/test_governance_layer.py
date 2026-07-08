"""Regression tests for _classify_skill_dir_detail governance output.

This is the real backend→UI contract: serve.py attaches detail() to
directory metadata, and cleanup.py gates trash moves on its layer/policy.
The frontend sourceCapabilityBucket consumes runtime_state (host inspector)
first, then category, then layer.

We isolate plugin_context_for_dir (host-specific; reads live machine config
like codex/claude/buddy) so these tests assert the pure path-derived
governance on any machine. The host-inspector runtime_state (the UI's
primary signal) is covered separately in a later batch.

Run: python3 -m unittest discover -s tests -t .
"""

import unittest
from pathlib import Path
from unittest import mock

from skilldash.discovery import _classify_skill_dir_detail

HOME = str(Path.home())


class TestGovernanceLayer(unittest.TestCase):
    def setUp(self):
        # plugin_context_for_dir reads live host config. Neutralize it so
        # governance is determined purely by path signals on any machine.
        patcher = mock.patch(
            "skilldash.discovery.plugin_context_for_dir", return_value=None
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def _detail(self, rel_under_home):
        return _classify_skill_dir_detail(f"{HOME}/{rel_under_home}")

    def test_active_root_user_skills_are_manageable(self):
        d = self._detail(".zfakeagent/skills")
        self.assertEqual(d["layer"], "active-root")
        self.assertEqual(d["policy"], "manage")
        self.assertEqual(d["category"], "user")
        self.assertTrue(d["is_daily"])

    def test_backup_snapshot_is_reviewable_not_deletable(self):
        d = self._detail(".snapshots/2026-06-03/skills")
        self.assertEqual(d["layer"], "backup-snapshot")
        self.assertEqual(d["policy"], "review")
        self.assertEqual(d["category"], "cache")
        # review-layer backups must never look directly deletable
        self.assertFalse(d["is_deletable"])

    def test_marketplace_is_observe_only(self):
        d = self._detail(".zfakeagent/plugins/marketplace/skills")
        self.assertEqual(d["layer"], "plugin-marketplace")
        self.assertEqual(d["policy"], "observe")
        self.assertEqual(d["category"], "marketplace")

    def test_project_local_is_review(self):
        d = self._detail("projects/myapp/.zfakeagent/skills")
        self.assertEqual(d["layer"], "project-local")
        self.assertEqual(d["policy"], "review")
        self.assertEqual(d["category"], "project")

    def test_nested_agent_copy_is_imported_review(self):
        # agent skills nested inside another agent's dir → cross-copy → imported
        d = self._detail(".zfakeagent/sub/.zinner/skills")
        self.assertEqual(d["layer"], "imported-copy")
        self.assertEqual(d["policy"], "review")
        self.assertEqual(d["category"], "cross-copy")

    def test_openclaw_shared_link_is_observe_not_deletable(self):
        # ~/.openclaw/skills is a symlink layer to ~/.agents/skills; never deletable
        d = self._detail(".openclaw/skills")
        self.assertEqual(d["layer"], "shared-link")
        self.assertEqual(d["policy"], "observe")
        self.assertFalse(d["is_deletable"])

    def test_openclaw_workspace_is_manage(self):
        # ~/.openclaw/workspace/skills is ClawHub market installs → manage
        d = self._detail(".openclaw/workspace/skills")
        self.assertEqual(d["layer"], "agent-installed")
        self.assertEqual(d["policy"], "manage")


import os
import tempfile


class TestSymlinkFarm(unittest.TestCase):
    """is_symlink_farm:目录非隐藏条目全软链且≥2 → True(镜像农场,无独立能力)。
    严格口径,不误伤含真实目录的根(如 ~/.claude/skills)。"""

    def setUp(self):
        # plugin_context 可能读宿主配置;隔离掉,农场判定只依赖目录内容。
        patcher = mock.patch(
            "skilldash.discovery.plugin_context_for_dir", return_value=None
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        self.tmp = tempfile.mkdtemp(prefix="sd-farm-test-")
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, ignore_errors=True))

    def _dir(self, name):
        p = os.path.join(self.tmp, name)
        os.makedirs(p, exist_ok=True)
        return p

    def test_all_symlinks_is_farm(self):
        # ~/.bob/skills 模式:21 条全软链 → 农场
        d = self._dir("farm")
        for i in range(3):
            os.symlink("/nonexistent/target" + str(i), os.path.join(d, f"skill{i}"))
        detail = _classify_skill_dir_detail(d)
        self.assertTrue(detail.get("is_symlink_farm"), "全软链目录应判农场")

    def test_real_dirs_not_farm(self):
        # ~/.claude/skills 模式:含真实目录 → 不农场
        d = self._dir("real")
        os.makedirs(os.path.join(d, "realskill"))
        os.symlink("/nonexistent/x", os.path.join(d, "linkskill"))
        detail = _classify_skill_dir_detail(d)
        self.assertFalse(detail.get("is_symlink_farm"), "含真实目录不判农场")

    def test_too_few_entries_not_farm(self):
        # 单软链不判农场(≥2 阈值,防误伤)
        d = self._dir("single")
        os.symlink("/nonexistent/x", os.path.join(d, "onlylink"))
        detail = _classify_skill_dir_detail(d)
        self.assertFalse(detail.get("is_symlink_farm"), "单条目不判农场")

    def test_hidden_entries_ignored(self):
        # .snapshots 等隐藏条目不计;非隐藏全软链仍判农场
        d = self._dir("hidden")
        os.makedirs(os.path.join(d, ".snapshots"))
        os.symlink("/nonexistent/a", os.path.join(d, "a"))
        os.symlink("/nonexistent/b", os.path.join(d, "b"))
        detail = _classify_skill_dir_detail(d)
        self.assertTrue(detail.get("is_symlink_farm"), "隐藏条目忽略,非隐藏全软链判农场")


if __name__ == "__main__":
    unittest.main()
