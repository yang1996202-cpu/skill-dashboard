"""Regression tests for the local duplicate-decision key.

The key decides whether a "mark as multi-agent deployment" action stays in
effect. If the algorithm drifts, previously-dismissed duplicate reminders
reappear (or the wrong entries get hidden). Keep it deterministic and stable.

Run: python3 -m unittest discover -s tests -t .
"""

import hashlib
import unittest

from skilldash.decisions import _duplicate_decision_key

DEFAULT = "multi_agent_deployment"


class TestDuplicateDecisionKey(unittest.TestCase):
    def test_matches_reference_sha1(self):
        # Independent recomputation guards against silent algorithm drift.
        raw = f"{DEFAULT}|foo|abc"
        expected = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:20]
        self.assertEqual(_duplicate_decision_key("foo", "abc"), expected)

    def test_deterministic(self):
        self.assertEqual(
            _duplicate_decision_key("foo", "abc"),
            _duplicate_decision_key("foo", "abc"),
        )

    def test_distinct_inputs_distinct_keys(self):
        self.assertNotEqual(
            _duplicate_decision_key("foo", "abc"),
            _duplicate_decision_key("foo", "abd"),
        )
        self.assertNotEqual(
            _duplicate_decision_key("foo", "abc"),
            _duplicate_decision_key("bar", "abc"),
        )

    def test_default_decision_matches_explicit(self):
        self.assertEqual(
            _duplicate_decision_key("foo", "abc"),
            _duplicate_decision_key("foo", "abc", decision=DEFAULT),
        )

    def test_length_is_twenty(self):
        self.assertEqual(len(_duplicate_decision_key("foo", "abc")), 20)


if __name__ == "__main__":
    unittest.main()
