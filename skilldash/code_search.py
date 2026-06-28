"""GitHub Code Search 来源恢复通用层。

给 unknown skill 补来源(docs/source-recovery.md §4):按 SKILL.md 独有内容话术
召回候选仓库,再用 content hash 精确确认(256 位零误差)。对开源新用户不依赖
本机留痕。

纯库,不依赖 serve。HTTP 用 urllib.request(零依赖),不引入 pip 包。

认证要求:GitHub Code Search API(/search/code)强制认证,无 token 返回 401。
所以 search_candidates 在无 GITHUB_TOKEN 时直接降级返回 error 结构,不尝试
无证调用。GITHUB_TOKEN 只从 source_ops import 这个已加载好的常量(只读)。

rate limit:/search/code 认证用户限 10 次/分钟(很紧)。自建内存缓存(TTL
5 分钟)复用同 query 结果;多片段要节制——失败片段不无限重试,设上限
MAX_SNIPPETS=5。

多片段策略(§4 实测):
- 按名字搜撞同类失效(仓库名 ≠ skill 名,合集仓库顶不出来)
- 按独有内容话术(中文/英文短语)命中真实仓库
- 中文全文索引不稳,首次精确短语可能返回空 → 换片段重试
- 最终确认永远靠 content hash(搜索模糊,改一字 hash 就失效)
"""
from __future__ import annotations

import base64
import hashlib
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

# 只 import 这个已加载好的常量(只读)。Track A 在改 source_ops,这里不依赖它的
# _github_cache / _github_api_fetch,避免耦合;只用 token 值。
from skilldash.source_ops import GITHUB_TOKEN


def _cs_log(msg: str) -> None:
    """诊断日志:append 到 .data/codesearch.log(与 source.py::_cs_log 同文件),吞 IO 错误。

    纯库内部记召回 skip 原因(限流/err/空结果),让黑盒可观测。延迟 import CACHE_DIR
    避免顶层耦合。
    """
    try:
        from skilldash.paths import CACHE_DIR
        log_path = CACHE_DIR.parent / "codesearch.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} [lib] {msg}\n")
    except Exception:
        pass


# ── 内部状态 ──
# (url,) -> (timestamp, data_or_None, error_or_None)
# 自建轻量缓存,与 source_ops._github_cache 独立(它服务于上游检查,语义不同)。
_cs_cache = {}
_cs_cache_ttl = 300  # 5 分钟,与 source_ops 对齐

# /search/code 认证用户限 10 次/分钟;记录 reset 时间避免硬撞。
_cs_rate_limit_reset = 0

# 多片段召回上限:一次 skill 内容里取最多这么多片段去搜,失败的片段不无限重试。
MAX_SNIPPETS = 5
# 单次搜索返回的候选数上限(per_query)。
DEFAULT_PER_QUERY = 5
# 片段长度上限:GitHub Code Search q 过长(>256 字符实测不稳)会被截断或 422。
MAX_SNIPPET_LEN = 120


def _cs_rate_limited_now():
    """True 表示当前处在已知的 /search/code 限流窗口内。"""
    global _cs_rate_limit_reset
    if _cs_rate_limit_reset == 0:
        return False
    if time.time() >= _cs_rate_limit_reset:
        _cs_rate_limit_reset = 0
        return False
    return True


def _cs_fetch(url, *, is_search=False):
    """带内存缓存的 GET。返回 (data, error_str)。

    data 为解析后的 JSON(dict);error_str 非 None 表示失败(网络/HTTP/解析)。

    is_search=True 时,识别 /search/code 的限流响应头(403/429 + X-RateLimit-Reset)
    并记录 _cs_rate_limit_reset,后续调用直接短路返回限流错误。

    不抛异常:网络/HTTP 错误一律转成 error_str,避免拖垮调用方。
    """
    now = time.time()
    cached = _cs_cache.get(url)
    if cached and (now - cached[0]) < _cs_cache_ttl:
        return cached[1], cached[2]

    if is_search and _cs_rate_limited_now():
        err = "rate_limited"
        _cs_cache[url] = (now, None, err)
        return None, err

    global _cs_rate_limit_reset
    try:
        headers = {
            "User-Agent": "skill-dashboard",
            "Accept": "application/vnd.github+json",
        }
        if GITHUB_TOKEN:
            headers["Authorization"] = f"token {GITHUB_TOKEN}"
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read().decode("utf-8")
            if is_search:
                remaining = resp.headers.get("X-RateLimit-Remaining", "")
                reset_ts = resp.headers.get("X-RateLimit-Reset", "")
                if remaining == "0":
                    try:
                        _cs_rate_limit_reset = int(reset_ts) if reset_ts else int(now + 60)
                    except Exception:
                        _cs_rate_limit_reset = int(now + 60)
            data = json.loads(raw)
            _cs_cache[url] = (now, data, None)
            return data, None
    except urllib.error.HTTPError as e:
        # 401: 无 token / token 无效;403/429: 限流;422: query 不合法。
        if is_search and e.code in (403, 429):
            reset_ts = e.headers.get("X-RateLimit-Reset", "") if e.headers else ""
            try:
                _cs_rate_limit_reset = int(reset_ts) if reset_ts else int(now + 60)
            except Exception:
                _cs_rate_limit_reset = int(now + 60)
            err = "rate_limited"
        elif e.code == 401:
            err = "auth_failed"
        elif e.code == 422:
            err = "invalid_query"
        else:
            err = f"http_{e.code}"
        _cs_cache[url] = (now, None, err)
        return None, err
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as e:
        err = f"network:{type(e).__name__}"
        _cs_cache[url] = (now, None, err)
        return None, err


def _sanitize_snippet(snippet):
    """清洗单个片段:去首尾空白、压缩内部空白、截断到 MAX_SNIPPET_LEN。

    GitHub Code Search 的 q 参数对超长/含特殊语法的 query 会 422,所以这里
    做保守截断,且剥掉可能被当成搜索语法的引号(避免短语被当 exact-match 操作符)。
    """
    if not snippet:
        return ""
    s = " ".join(str(snippet).split())  # 压缩内部空白
    # 去掉可能触发搜索操作符的字符
    s = s.replace('"', " ").replace("'", " ")
    s = " ".join(s.split())
    if len(s) > MAX_SNIPPET_LEN:
        s = s[:MAX_SNIPPET_LEN]
    return s.strip()


def _parse_search_item(item):
    """从 /search/code 单条结果抽 {repo, path, html_url}。"""
    repo_obj = item.get("repository") or {}
    full_name = repo_obj.get("full_name") or ""
    return {
        "repo": full_name,            # owner/repo
        "path": item.get("path") or "",
        "html_url": item.get("html_url") or "",
    }


def search_candidates(snippets, per_query=DEFAULT_PER_QUERY):
    """对每个 snippet 调 GitHub Code Search,聚合候选仓库。

    docs/source-recovery.md §4 实测:按独有内容话术(中文/英文短语)能命中真实
    合集仓库,按名字搜撞同类失效。所以这里逐片段召回,聚合 + 去重,命中片段累积
    到 matched_snippets,供前端展示"哪个片段命中了哪个仓库"。

    Args:
        snippets: [str] SKILL.md 内容里的独有短语列表(已由调用方从正文挑出,
            优先 frontmatter description / 标题 / 罕见句子)。
        per_query: 单次搜索保留的候选条数上限。

    Returns:
        无 GITHUB_TOKEN 时(降级,不发起请求):
            {"error": "code_search_requires_token",
             "hint": "在项目根 .env 配 GITHUB_TOKEN 后可用"}

        正常:
            {"candidates": [
                {"repo": "owner/repo", "path": "skills/foo/SKILL.md",
                 "html_url": "...", "matched_snippets": ["片段A", "片段B"],
                 "hit_count": 2},
                ...
            ],
             "searched_snippets": [实际发起搜索的片段],
             "skipped": [因限流/空结果跳过的片段],
             "rate_limited": bool}

        候选按 hit_count 降序(hit_count 相同按 repo 字典序),便于前端把"多片段
        共同命中"的仓库排前面——多片段命中 = 更可能是真来源。
    """
    if not GITHUB_TOKEN:
        return {
            "error": "code_search_requires_token",
            "hint": "在项目根 .env 配 GITHUB_TOKEN 后可用",
        }

    # 清洗 + 截断 + 去空 + 去重(保序)
    cleaned = []
    seen = set()
    for sn in snippets or []:
        s = _sanitize_snippet(sn)
        if s and s not in seen:
            seen.add(s)
            cleaned.append(s)
    # 多片段节制:上限 MAX_SNIPPETS
    cleaned = cleaned[:MAX_SNIPPETS]

    if not cleaned:
        return {"candidates": [], "searched_snippets": [], "skipped": [],
                "rate_limited": False}

    # key: repo -> {path, html_url, matched_snippets:set, hit_count}
    agg = {}
    searched = []
    skipped = []
    rate_limited = False

    for snippet in cleaned:
        # 一旦进入限流窗口,剩余片段直接跳过,不无限重试。
        if _cs_rate_limited_now():
            rate_limited = True
            skipped.append(snippet)
            _cs_log(f"skip(rate_window) sn={snippet[:40]!r}")
            continue

        q = urllib.parse.quote(snippet)
        url = (
            "https://api.github.com/search/code"
            f"?q={q}&per_page={int(per_query)}"
        )
        data, err = _cs_fetch(url, is_search=True)

        if err == "rate_limited":
            rate_limited = True
            skipped.append(snippet)
            _cs_log(f"skip(rate_limited) sn={snippet[:40]!r}")
            continue
        if err or not data:
            # 网络/解析失败或空结果:换下个片段重试(§4:中文首次精确短语常空)。
            skipped.append(snippet)
            _cs_log(f"skip(err={err}) sn={snippet[:40]!r}")
            continue

        items = data.get("items") or []
        if not items:
            skipped.append(snippet)
            _cs_log(f"skip(empty_results) sn={snippet[:40]!r}")
            continue

        searched.append(snippet)
        for item in items[:per_query]:
            parsed = _parse_search_item(item)
            repo = parsed["repo"]
            if not repo:
                continue
            entry = agg.setdefault(repo, {
                "repo": repo,
                "path": parsed["path"],
                "html_url": parsed["html_url"],
                "matched_snippets": [],
                "hit_count": 0,
            })
            if snippet not in entry["matched_snippets"]:
                entry["matched_snippets"].append(snippet)
                entry["hit_count"] += 1

    candidates = sorted(
        agg.values(),
        key=lambda c: (-c["hit_count"], c["repo"]),
    )
    return {
        "candidates": candidates,
        "searched_snippets": searched,
        "skipped": skipped,
        "rate_limited": rate_limited,
    }


_github_login_cache = None


def get_github_login():
    """token 认证拿 GitHub login(缓存)。用于 user:<login> 限定搜用户自己仓库。"""
    global _github_login_cache
    if _github_login_cache:
        return _github_login_cache
    if not GITHUB_TOKEN:
        return None
    data, err = _cs_fetch("https://api.github.com/user")
    if data and data.get("login"):
        _github_login_cache = data["login"]
        return _github_login_cache
    return None


def search_repos_by_name(skill_name, per_query=10):
    """按 skill 名字搜 GitHub 仓库(/search/repositories,30/分宽裕)——来源恢复主路线。

    替换内容话术搜(/search/code)作主入口。策略:优先搜用户自己仓库(user:<login>,
    通用名也命中,如 stay-awake → yang1996202-cpu/stay-awake-skill),再全局 fallback。
    仓库名含 skill 名(含变体如 -skill 后缀)为候选。

    比 /search/code 省 API(1-2 次/skill vs 5 次)且命中率高(skill 名比内容话术独特)。
    内容话术搜保留作异步补充(docs/source-recovery.md),不删。

    实测(2026-06-28):user:yang1996202-cpu+stay-awake 命中 stay-awake-skill
    (全局搜被 Johnson468/Stay-Awake ★131 挤掉,user: 限定精准命中)。
    """
    if not GITHUB_TOKEN:
        return {"error": "code_search_requires_token",
                "hint": "在项目根 .env 配 GITHUB_TOKEN 后可用"}
    login = get_github_login()
    candidates = []
    seen = set()
    # (query, is_own_scope):自己仓库优先,命中就不全局搜(省 API)
    queries = []
    if login:
        queries.append((f"user:{login} {skill_name}", True))
    queries.append((skill_name, False))

    for q, is_own_scope in queries:
        url = (f"https://api.github.com/search/repositories"
               f"?q={urllib.parse.quote(q)}&per_page={int(per_query)}")
        data, err = _cs_fetch(url)
        if err or not data:
            continue
        for it in (data.get("items") or [])[:per_query]:
            repo = it.get("full_name") or ""
            if not repo or repo in seen:
                continue
            repo_name = repo.split("/")[-1]
            # 仓库名含 skill 名(变体 -skill 后缀也算)
            if skill_name.lower() not in repo_name.lower():
                continue
            seen.add(repo)
            candidates.append({
                "repo": repo,
                "description": it.get("description") or "",
                "stars": it.get("stargazers_count", 0),
                "url": it.get("html_url") or "",
                "is_own": bool(login) and repo.startswith(login + "/"),
            })
        if candidates and is_own_scope:
            break  # 用户仓库命中就不全局搜
    # 自己仓库优先,再 stars 降序
    candidates.sort(key=lambda c: (not c["is_own"], -c["stars"]))
    _cs_log(f"search_repos name={skill_name!r} login={login} hits={len(candidates)} "
            f"repos={[c['repo'] for c in candidates]}")
    return {"candidates": candidates, "login": login}


def _hash_text(text):
    """SHA256 of UTF-8 text. 与 content_hash.record_content_hash 同口径。"""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _fetch_remote_skill_md(repo, path):
    """拉远程 SKILL.md 原文。优先 contents API(base64),回退 raw URL。

    Returns (content_str, error_str)。
    """
    if not repo or not path:
        return None, "missing_repo_or_path"

    owner_repo = repo.lstrip("/")
    # contents API 返回 base64 编码的 content,稳;raw URL 偶发 404(ref 不对)。
    api_url = (
        f"https://api.github.com/repos/{owner_repo}/contents/"
        f"{urllib.parse.quote(path, safe='/')}"
    )
    data, err = _cs_fetch(api_url)
    if data and isinstance(data, dict) and data.get("content"):
        try:
            raw_b64 = data["content"].replace("\n", "")
            content = base64.b64decode(raw_b64).decode("utf-8", errors="ignore")
            return content, None
        except Exception as e:
            return None, f"decode:{type(e).__name__}"
    if err:
        return None, err
    # 回退 raw URL(用默认 main ref)
    raw_url = f"https://raw.githubusercontent.com/{owner_repo}/main/{path}"
    try:
        headers = {"User-Agent": "skill-dashboard"}
        if GITHUB_TOKEN:
            headers["Authorization"] = f"token {GITHUB_TOKEN}"
        req = urllib.request.Request(raw_url, headers=headers)
        with urllib.request.urlopen(req, timeout=15) as resp:
            content = resp.read().decode("utf-8", errors="ignore")
            return content, None
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError) as e:
        return None, f"raw:{type(e).__name__}"


def confirm_candidate(candidate, local_skill_dir):
    """拉候选 SKILL.md 原文,算 content hash,和本地比对。

    Args:
        candidate: {repo, path, ...} 来自 search_candidates 的候选。
        local_skill_dir: 本地 skill 目录(含 SKILL.md)。

    Returns:
        {"match": bool, "hash_local": str, "hash_remote": str,
         "error": str|None}
        本地缺 SKILL.md 或远程拉取失败时 match=False 且填 error。
    """
    repo = (candidate or {}).get("repo") or ""
    path = (candidate or {}).get("path") or ""

    local_md = Path(local_skill_dir) / "SKILL.md"
    if not local_md.exists():
        return {
            "match": False,
            "hash_local": "",
            "hash_remote": "",
            "error": "local_skill_md_not_found",
        }
    try:
        local_content = local_md.read_text("utf-8", errors="ignore")
    except OSError as e:
        return {
            "match": False,
            "hash_local": "",
            "hash_remote": "",
            "error": f"local_read:{type(e).__name__}",
        }
    hash_local = _hash_text(local_content)

    remote_content, err = _fetch_remote_skill_md(repo, path)
    if err or remote_content is None:
        return {
            "match": False,
            "hash_local": hash_local,
            "hash_remote": "",
            "error": err or "remote_fetch_failed",
        }
    hash_remote = _hash_text(remote_content)

    return {
        "match": hash_local == hash_remote,
        "hash_local": hash_local,
        "hash_remote": hash_remote,
        "error": None,
    }
