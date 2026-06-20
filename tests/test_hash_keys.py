"""Regression tests for directory-aware content-hash key derivation.

The hash key decides which stored baseline a skill's SKILL.md is compared
against. A wrong prefix makes ``check_content_changes`` either miss real
edits (stale prefix) or flag false changes (colliding prefix), and it breaks
the multi-agent-deployment decision key. These are pure path computations —
no filesystem fixtures needed.

Run: python3 -m unittest discover -s tests -t .
"""

import unittest
from pathlib import Path

from skilldash.content_hash import _hash_key, _hash_prefix_for_target

HOME = str(Path.home())


class TestHashKey(unittest.TestCase):
    def test_agent_prefix_plus_subpath(self):
        self.assertEqual(_hash_key(f"{HOME}/.claude/skills/foo"), ".claude/foo")
        self.assertEqual(_hash_key(f"{HOME}/.cursor/skills/foo"), ".cursor/foo")
        self.assertEqual(_hash_key(f"{HOME}/.codex/skills/baz"), ".codex/baz")

    def test_nested_subpath_preserved(self):
        # skills/mkt/bar keeps the mkt/ segment under the agent prefix
        self.assertEqual(_hash_key(f"{HOME}/.claude/skills/mkt/bar"), ".claude/mkt/bar")

    def test_no_skills_segment_falls_back(self):
        # Path without a 'skills' boundary uses parent/name — never returns ''
        key = _hash_key(f"{HOME}/plain/foo")
        self.assertTrue(key)


class TestHashPrefixForTarget(unittest.TestCase):
    def test_prefix_is_agent_before_skills(self):
        self.assertEqual(_hash_prefix_for_target(f"{HOME}/.claude/skills"), ".claude")
        self.assertEqual(_hash_prefix_for_target(f"{HOME}/.cursor/skills"), ".cursor")
        self.assertEqual(_hash_prefix_for_target(f"{HOME}/.codex/skills"), ".codex")


if __name__ == "__main__":
    unittest.main()
