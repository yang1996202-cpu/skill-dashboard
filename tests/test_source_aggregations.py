"""Regression tests for _build_owner_aggregations (owner→repo→skills 聚合纯函数).

喂「按作者/仓库聚合」只读视图的纯聚合逻辑。handler 层只做 IO + 缓存,
聚合规则在这里钉死。Run: python3 -m unittest discover -s tests -t .
"""
import unittest

from skilldash.source_ops import _build_owner_aggregations


class TestBuildOwnerAggregations(unittest.TestCase):
    def _e(self, name, repo, agent, source="steal-meta", **kw):
        base = {"name": name, "repo": repo, "source": source, "agent": agent,
                "dir": f"/x/{name}", "description": "", "kind": "skill"}
        base.update(kw)
        return base

    def test_owner_split(self):
        r = _build_owner_aggregations([self._e("a", "mattpocock/skills", "Claude Code")])
        self.assertEqual(r["owners"][0]["owner"], "mattpocock")
        self.assertEqual(r["owners"][0]["skill_count"], 1)
        self.assertEqual(r["total_owners"], 1)

    def test_unknown_filtered(self):
        r = _build_owner_aggregations([
            self._e("a", "mattpocock/skills", "Claude Code"),
            self._e("b", "", "Codex", source="unknown"),
        ])
        self.assertEqual(r["total_skills"], 1)
        self.assertEqual(len(r["owners"]), 1)

    def test_cross_agent_merge(self):
        # 同 (repo, name) 跨 agent 合并为 1 个 skill,agents 累积
        r = _build_owner_aggregations([
            self._e("a", "mattpocock/skills", "Claude Code"),
            self._e("a", "mattpocock/skills", "Codex"),
        ])
        self.assertEqual(r["total_skills"], 1)
        repo0 = r["owners"][0]["repos"][0]
        self.assertEqual(repo0["skill_count"], 1)
        self.assertEqual(set(repo0["skills"][0]["agents"]), {"Claude Code", "Codex"})
        self.assertEqual(set(r["owners"][0]["agents"]), {"Claude Code", "Codex"})
        self.assertEqual(r["owners"][0]["agent_count"], 2)

    def test_two_level_structure(self):
        r = _build_owner_aggregations([
            self._e("a", "mattpocock/repo1", "Claude Code"),
            self._e("b", "mattpocock/repo2", "Codex"),
        ])
        o = r["owners"][0]
        self.assertEqual(o["owner"], "mattpocock")
        self.assertEqual(o["repo_count"], 2)
        self.assertEqual(o["skill_count"], 2)
        self.assertEqual(len(o["repos"]), 2)
        self.assertEqual(r["total_repos"], 2)

    def test_empty_all_unknown(self):
        r = _build_owner_aggregations([
            self._e("a", "", "Claude Code", source="unknown"),
            self._e("b", "", "Codex", source="unknown"),
        ])
        self.assertEqual(r["owners"], [])
        self.assertEqual(r["total_skills"], 0)
        self.assertEqual(r["total_owners"], 0)

    def test_owner_sorted_by_count_desc(self):
        r = _build_owner_aggregations([
            self._e("a", "few/repo", "Claude Code"),
            self._e("b", "many/r1", "Codex"),
            self._e("c", "many/r2", "Codex"),
        ])
        # many(2 skill) 排在 few(1) 前
        self.assertEqual(r["owners"][0]["owner"], "many")
        self.assertEqual(r["owners"][0]["skill_count"], 2)
        self.assertEqual(r["owners"][1]["owner"], "few")

    def test_repo_without_owner_skipped(self):
        # repo 不含 '/' → 无法拆 owner → 跳过
        r = _build_owner_aggregations([self._e("a", "nodash", "Claude Code")])
        self.assertEqual(r["owners"], [])

    def test_same_repo_different_names_not_merged(self):
        # 同 repo 不同 name = 两个 skill,不合并
        r = _build_owner_aggregations([
            self._e("a", "mattpocock/skills", "Claude Code"),
            self._e("b", "mattpocock/skills", "Claude Code"),
        ])
        repo0 = r["owners"][0]["repos"][0]
        self.assertEqual(repo0["skill_count"], 2)
        self.assertEqual(len(repo0["skills"]), 2)

    def test_passthrough_fields_preserved(self):
        r = _build_owner_aggregations([
            self._e("a", "mattpocock/skills", "Claude Code", description="hello", kind="builtin"),
        ])
        s = r["owners"][0]["repos"][0]["skills"][0]
        self.assertEqual(s["description"], "hello")
        self.assertEqual(s["kind"], "builtin")


if __name__ == "__main__":
    unittest.main()
