"""tests/test_code_search.py — code_search 通用层回归测试。

只跑自己:`python3 -m unittest tests.test_code_search`
网络全部 mock 掉(patch skilldash.code_search._cs_fetch / GITHUB_TOKEN),
不真实联网。零依赖(stdlib unittest + unittest.mock)。
"""
import unittest
from unittest import mock
from pathlib import Path
import time

import skilldash.code_search as cs


def _make_search_item(repo, path, html_url=None):
    return {
        "repository": {"full_name": repo},
        "path": path,
        "html_url": html_url or f"https://github.com/{repo}/blob/main/{path}",
    }


class SearchCandidatesTests(unittest.TestCase):
    """聚合 / 去重 / matched_snippets 累积 / 限流短路 / 无 token 降级。"""

    def setUp(self):
        # 多数用例假定有 token;无 token 用例单独 patch。
        self._token_patcher = mock.patch.object(cs, "GITHUB_TOKEN", "fake-token")
        self._token_patcher.start()
        self.addCleanup(self._token_patcher.stop)
        # 清缓存 + 限流状态,避免用例间污染
        cs._cs_cache.clear()
        cs._cs_rate_limit_reset = 0

    def test_no_token_degrades_gracefully(self):
        """无 GITHUB_TOKEN 时直接返回 error,不发起任何请求。"""
        self._token_patcher.stop()
        with mock.patch.object(cs, "GITHUB_TOKEN", ""):
            with mock.patch.object(cs, "_cs_fetch") as m_fetch:
                result = cs.search_candidates(["some phrase"])
        self.assertNotIn("candidates", result)
        self.assertEqual(result["error"], "code_search_requires_token")
        self.assertIn("hint", result)
        m_fetch.assert_not_called()

    def test_aggregate_dedupe_and_matched_snippets_accumulation(self):
        """两片段命中同一仓库 → 聚合一条,matched_snippets 累积两条,hit_count=2;
        命中不同仓库 → 两条候选,按 hit_count 降序。"""
        responses = {
            "https://api.github.com/search/code?q=phrase-A&per_page=5": {
                "items": [
                    _make_search_item("KKKKhazix/khazix-skills", "skills/storage-analyzer/SKILL.md"),
                    _make_search_item("other/repo", "x/SKILL.md"),
                ]
            },
            "https://api.github.com/search/code?q=phrase-B&per_page=5": {
                "items": [
                    # 同一仓库再被第二片段命中 → 累积,不重复
                    _make_search_item("KKKKhazix/khazix-skills", "skills/storage-analyzer/SKILL.md"),
                ]
            },
        }

        def fake_fetch(url, *, is_search=False):
            return responses.get(url, {"items": []}), None

        with mock.patch.object(cs, "_cs_fetch", side_effect=fake_fetch):
            result = cs.search_candidates(["phrase-A", "phrase-B"])

        self.assertNotIn("error", result)
        cands = result["candidates"]
        # 两个不同仓库:khazix(hit 2) + other(hit 1),去重后共 2 条
        self.assertEqual(len(cands), 2)
        # hit_count 降序:khazix 在前
        self.assertEqual(cands[0]["repo"], "KKKKhazix/khazix-skills")
        self.assertEqual(cands[0]["hit_count"], 2)
        self.assertEqual(
            sorted(cands[0]["matched_snippets"]), ["phrase-A", "phrase-B"])
        self.assertEqual(cands[1]["repo"], "other/repo")
        self.assertEqual(cands[1]["hit_count"], 1)
        # searched 记录了两个实际有命中的片段
        self.assertEqual(sorted(result["searched_snippets"]),
                         ["phrase-A", "phrase-B"])
        self.assertEqual(result["skipped"], [])
        self.assertFalse(result["rate_limited"])

    def test_empty_result_skips_and_retries_next_snippet(self):
        """§4 实测:中文首次精确短语常返回空 → 换下个片段重试。
        第一个片段空结果 → 进 skipped;第二个片段命中 → 召回成功。"""
        responses = {
            "https://api.github.com/search/code?q=chinese-empty&per_page=5": {"items": []},
            "https://api.github.com/search/code?q=english-hit&per_page=5": {
                "items": [_make_search_item("foo/bar", "SKILL.md")]
            },
        }

        def fake_fetch(url, *, is_search=False):
            return responses.get(url, {"items": []}), None

        with mock.patch.object(cs, "_cs_fetch", side_effect=fake_fetch):
            result = cs.search_candidates(["chinese-empty", "english-hit"])

        self.assertEqual([c["repo"] for c in result["candidates"]], ["foo/bar"])
        self.assertIn("chinese-empty", result["skipped"])
        self.assertEqual(result["searched_snippets"], ["english-hit"])

    def test_rate_limit_short_circuits_remaining_snippets(self):
        """一旦进入限流窗口,剩余片段直接跳过,不无限重试。

        真实 _cs_fetch 在收到 403/429 时会设 _cs_rate_limit_reset,使后续
        search_candidates 循环开头的 _cs_rate_limited_now() 返回 True 短路。
        mock 掉 _cs_fetch 时要模拟这个副作用,否则短路逻辑不会被触发。
        """
        call_count = {"n": 0}

        def fake_fetch(url, *, is_search=False):
            call_count["n"] += 1
            # 模拟真实 _cs_fetch 收到限流响应后的副作用:设置限流窗口
            cs._cs_rate_limit_reset = int(time.time()) + 3600
            return None, "rate_limited"

        with mock.patch.object(cs, "_cs_fetch", side_effect=fake_fetch):
            result = cs.search_candidates(["a", "b", "c", "d", "e", "f"])

        self.assertTrue(result["rate_limited"])
        # 第一个片段真正发了请求并触发限流;之后进入窗口,剩余片段短路
        self.assertEqual(call_count["n"], 1)
        self.assertEqual(result["candidates"], [])
        # 输入 6 片段,MAX_SNIPPETS=5 截断后剩 5 个全部 skipped:
        # 第 1 个(限流响应) + 后 4 个(窗口内短路) = 5
        self.assertEqual(len(result["skipped"]), 5)

    def test_max_snippets_cap(self):
        """超过 MAX_SNIPPETS 的片段被截断,不无限搜索(限流友好)。"""
        called_urls = []

        def fake_fetch(url, *, is_search=False):
            called_urls.append(url)
            return {"items": []}, None

        with mock.patch.object(cs, "_cs_fetch", side_effect=fake_fetch):
            cs.search_candidates(["s1", "s2", "s3", "s4", "s5", "s6", "s7"])

        self.assertEqual(len(called_urls), cs.MAX_SNIPPETS)

    def test_snippet_sanitization(self):
        """引号/超长片段被清洗,不会原样进 query。"""
        captured = {}

        def fake_fetch(url, *, is_search=False):
            captured["url"] = url
            return {"items": []}, None

        long = 'a "quoted"   messy   ' + "x" * 200
        with mock.patch.object(cs, "_cs_fetch", side_effect=fake_fetch):
            cs.search_candidates([long])
        # 引号被剥、内部空白压缩、长度截断到 MAX_SNIPPET_LEN
        q = captured["url"].split("q=")[1].split("&")[0]
        from urllib.parse import unquote
        decoded = unquote(q)
        self.assertNotIn('"', decoded)
        self.assertLessEqual(len(decoded), cs.MAX_SNIPPET_LEN)


class ConfirmCandidateTests(unittest.TestCase):
    """hash 比对:match / 不 match / 本地缺失 / 远程拉取失败。"""

    def setUp(self):
        self._token_patcher = mock.patch.object(cs, "GITHUB_TOKEN", "fake-token")
        self._token_patcher.start()
        self.addCleanup(self._token_patcher.stop)
        cs._cs_cache.clear()

    def test_match_when_hashes_equal(self):
        local_dir = Path(__file__).parent / "_tmp_skill_match"
        local_dir.mkdir(exist_ok=True)
        (local_dir / "SKILL.md").write_text("# identical body\n", encoding="utf-8")
        try:
            content = "# identical body\n"
            with mock.patch.object(cs, "_fetch_remote_skill_md",
                                   return_value=(content, None)):
                r = cs.confirm_candidate(
                    {"repo": "o/r", "path": "SKILL.md"}, local_dir)
            self.assertTrue(r["match"])
            self.assertEqual(r["hash_local"], r["hash_remote"])
            self.assertEqual(r["hash_local"],
                             cs._hash_text("# identical body\n"))
            self.assertIsNone(r["error"])
        finally:
            (local_dir / "SKILL.md").unlink(missing_ok=True)
            local_dir.rmdir()

    def test_no_match_when_content_differs(self):
        local_dir = Path(__file__).parent / "_tmp_skill_nomatch"
        local_dir.mkdir(exist_ok=True)
        (local_dir / "SKILL.md").write_text("local version", encoding="utf-8")
        try:
            with mock.patch.object(cs, "_fetch_remote_skill_md",
                                   return_value=("remote version", None)):
                r = cs.confirm_candidate(
                    {"repo": "o/r", "path": "SKILL.md"}, local_dir)
            self.assertFalse(r["match"])
            self.assertNotEqual(r["hash_local"], r["hash_remote"])
            self.assertIsNone(r["error"])
        finally:
            (local_dir / "SKILL.md").unlink(missing_ok=True)
            local_dir.rmdir()

    def test_local_skill_md_missing(self):
        local_dir = Path(__file__).parent / "_tmp_skill_missing"
        local_dir.mkdir(exist_ok=True)
        try:
            r = cs.confirm_candidate({"repo": "o/r", "path": "SKILL.md"}, local_dir)
            self.assertFalse(r["match"])
            self.assertEqual(r["error"], "local_skill_md_not_found")
            self.assertEqual(r["hash_local"], "")
        finally:
            local_dir.rmdir()

    def test_remote_fetch_failure(self):
        local_dir = Path(__file__).parent / "_tmp_skill_remotefail"
        local_dir.mkdir(exist_ok=True)
        (local_dir / "SKILL.md").write_text("x", encoding="utf-8")
        try:
            with mock.patch.object(cs, "_fetch_remote_skill_md",
                                   return_value=(None, "http_404")):
                r = cs.confirm_candidate(
                    {"repo": "o/r", "path": "SKILL.md"}, local_dir)
            self.assertFalse(r["match"])
            self.assertEqual(r["error"], "http_404")
            # 本地 hash 仍算出来,远程为空
            self.assertTrue(r["hash_local"])
            self.assertEqual(r["hash_remote"], "")
        finally:
            (local_dir / "SKILL.md").unlink(missing_ok=True)
            local_dir.rmdir()

    def test_hash_uses_sha256_utf8_same_as_content_hash(self):
        """confirm 用的 hash 口径必须和 content_hash.record_content_hash 一致
        (sha256 of utf-8 编码的全文),否则跨模块对不上。"""
        import hashlib
        text = "héllo 世界\n"
        self.assertEqual(cs._hash_text(text),
                         hashlib.sha256(text.encode("utf-8")).hexdigest())


if __name__ == "__main__":
    unittest.main()
