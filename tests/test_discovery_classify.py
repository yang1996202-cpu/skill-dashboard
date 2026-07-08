"""Regression tests for path-judgment primitives in discovery.

These helpers (_agent_from_path / _is_user_level_skill /
_is_project_agent_skill) are the building blocks that
_classify_skill_dir_detail consumes to derive the layer/policy/category
fields the UI and cleanup actually use. They are pure path computations —
no filesystem fixtures needed.

Note: the UI does NOT render the old five-way classification as labels.
It renders *capability buckets* (app-core.js sourceCapabilityBucket) using
the priority runtime_state > category > layer, where runtime_state comes
from the host inspector. The governance-layer contract (layer/policy) is
covered in test_governance_layer.py.

Run: python3 -m unittest discover -s tests -t .
"""

import unittest
from pathlib import Path

from skilldash.discovery import (
    _agent_from_path,
    _is_project_agent_skill,
    _is_user_level_skill,
    _target_is_active,
)

HOME = str(Path.home())


class TestAgentFromPath(unittest.TestCase):
    def test_known_agent_roots(self):
        self.assertEqual(_agent_from_path(f"{HOME}/.claude/skills"), "Claude Code")
        self.assertEqual(_agent_from_path(f"{HOME}/.codex/skills"), "Codex")
        self.assertEqual(_agent_from_path(f"{HOME}/.cursor/skills/foo"), "Cursor")
        self.assertEqual(_agent_from_path(f"{HOME}/.alice/skills"), "Alice")

    def test_buddy_family_short_circuit(self):
        # WorkBuddy / CodeBuddy have explicit branches before the agent table.
        self.assertEqual(_agent_from_path(f"{HOME}/.workbuddy/skills"), "WorkBuddy")
        self.assertEqual(_agent_from_path(f"{HOME}/.codebuddy/skills"), "CodeBuddy")
        self.assertEqual(_agent_from_path("/Applications/WorkBuddy.app/Contents/x"), "WorkBuddy")

    def test_config_child_is_agent_name(self):
        # ~/.config/<agent>/skills → agent name is the child of .config
        self.assertEqual(_agent_from_path(f"{HOME}/.config/myagent/skills"), "myagent")

    def test_unknown_dot_dir_strips_leading_dots(self):
        self.assertEqual(_agent_from_path(f"{HOME}/.unknownagent/skills"), "unknownagent")

    def test_no_dot_segment_falls_back_to_basename(self):
        self.assertEqual(_agent_from_path("/some/random/path"), "path")

    def test_non_dot_skills_subdir_takes_parent(self):
        # ~/非隐藏/skills 末段是"skills"且上级非 dot → 取上级(hyperframes/skills → hyperframes),
        # 不把"skills"当 agent 名(2026-07-08 收紧)。
        self.assertEqual(_agent_from_path(f"{HOME}/hyperframes/skills"), "hyperframes")
        self.assertEqual(_agent_from_path(f"{HOME}/someproject/skills"), "someproject")

    def test_dot_dir_skills_still_uses_dot_dir_name(self):
        # dot 目录下的 skills 走 dot 分支,不受末段 fallback 影响。
        self.assertEqual(_agent_from_path(f"{HOME}/.bob/skills"), "bob")


class TestUserLevelSkill(unittest.TestCase):
    def test_agent_skills_under_home(self):
        self.assertTrue(_is_user_level_skill(f"{HOME}/.claude/skills"))
        self.assertTrue(_is_user_level_skill(f"{HOME}/.kiro/skills"))

    def test_non_dot_prefixed_is_not_user_level(self):
        self.assertFalse(_is_user_level_skill(f"{HOME}/AI-Skills"))

    def test_nested_under_agent_is_user_level(self):
        # ~/.<agent>/skills/<分类> 是 agent 用户根下的子分类目录,user-level
        # (如 ~/.hermes/skills/creative)。废弃旧约束"恰好两层才算",
        # 否则 hermes 这类把 SKILL.md 放在 skills/<分类>/ 下层的 agent 会被
        # 误判成 project-local。
        self.assertTrue(_is_user_level_skill(f"{HOME}/.claude/skills/mkt"))
        self.assertTrue(_is_user_level_skill(f"{HOME}/.hermes/skills/creative"))

    def test_nested_under_agent_hidden_subdir_not_user_level(self):
        # agent 根下的隐藏子目录(如 .cache/.snapshots)不算 user-level,
        # 这些是缓存/快照,走 cache 分支。
        self.assertFalse(_is_user_level_skill(f"{HOME}/.claude/skills/.cache"))

    def test_outside_home_is_not_user_level(self):
        self.assertFalse(_is_user_level_skill("/tmp/skills"))


class TestProjectAgentSkill(unittest.TestCase):
    def test_project_nested_agent_skills(self):
        self.assertTrue(_is_project_agent_skill(f"{HOME}/projects/app/.claude/skills"))
        self.assertTrue(_is_project_agent_skill(f"{HOME}/projects/foo/src/.cursor/skills"))

    def test_home_agent_root_is_not_project(self):
        # ~/.claude/skills is user-level, not project-level
        self.assertFalse(_is_project_agent_skill(f"{HOME}/.claude/skills"))


class TestTargetIsActive(unittest.TestCase):
    """_target_is_active mirrors the frontend sourceCapabilityBucket active
    buckets. _scan_global_categories uses it to keep global stats from being
    flooded by marketplace shelves / caches / installed-disabled."""

    def test_active_runtime_states(self):
        for state in ("user-root", "builtin", "enabled", "loaded", "connector"):
            with self.subTest(state=state):
                self.assertTrue(_target_is_active({"runtime_state": state}))

    def test_inventory_runtime_states(self):
        for state in ("installed", "catalog", "cache", "stale", "orphaned"):
            with self.subTest(state=state):
                self.assertFalse(_target_is_active({"runtime_state": state}))

    def test_user_category_fallback(self):
        # Path-only target (no plugin_context) with category=user is active.
        self.assertTrue(_target_is_active({"category": "user"}))

    def test_vendor_bundled_layer_fallback(self):
        self.assertTrue(_target_is_active({"layer": "vendor-bundled"}))

    def test_inventory_layers_not_active(self):
        for layer in ("plugin-marketplace", "package-cache", "plugin-cache",
                      "imported-copy", "backup-snapshot", "downloaded-package"):
            with self.subTest(layer=layer):
                self.assertFalse(_target_is_active({"layer": layer}))

    def test_empty_detail_not_active(self):
        self.assertFalse(_target_is_active({}))


if __name__ == "__main__":
    unittest.main()
