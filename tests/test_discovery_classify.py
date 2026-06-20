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


class TestUserLevelSkill(unittest.TestCase):
    def test_agent_skills_under_home(self):
        self.assertTrue(_is_user_level_skill(f"{HOME}/.claude/skills"))
        self.assertTrue(_is_user_level_skill(f"{HOME}/.kiro/skills"))

    def test_non_dot_prefixed_is_not_user_level(self):
        self.assertFalse(_is_user_level_skill(f"{HOME}/AI-Skills"))

    def test_nested_under_agent_is_not_user_level(self):
        # ~/.claude/skills/mkt is depth 3, not the user-level root
        self.assertFalse(_is_user_level_skill(f"{HOME}/.claude/skills/mkt"))

    def test_outside_home_is_not_user_level(self):
        self.assertFalse(_is_user_level_skill("/tmp/skills"))


class TestProjectAgentSkill(unittest.TestCase):
    def test_project_nested_agent_skills(self):
        self.assertTrue(_is_project_agent_skill(f"{HOME}/projects/app/.claude/skills"))
        self.assertTrue(_is_project_agent_skill(f"{HOME}/projects/foo/src/.cursor/skills"))

    def test_home_agent_root_is_not_project(self):
        # ~/.claude/skills is user-level, not project-level
        self.assertFalse(_is_project_agent_skill(f"{HOME}/.claude/skills"))


if __name__ == "__main__":
    unittest.main()
