"""Explainable skill similarity helpers."""

from __future__ import annotations

import hashlib
import json
import math
import time
import re
from collections import Counter
from pathlib import Path
from typing import Callable, Iterable

from .classification import _classify_skill
from .discovery import _agent_from_path
from .paths import _cache_path
from .skill_parser import parse_frontmatter


SIGNATURE_SYNONYMS = {
    "小红书": ["xhs", "rednote", "xiaohongshu", "小红薯", "种草"],
    "飞书": ["lark", "feishu"],
    "文档": ["doc", "docs", "document", "markdown", "md", "readme"],
    "表格": ["sheet", "spreadsheet", "excel", "xlsx", "csv"],
    "幻灯片": ["ppt", "pptx", "slide", "presentation"],
    "浏览器": ["browser", "web", "chrome", "playwright"],
    "搜索": ["search", "lookup", "retrieval", "检索"],
    "抓取": ["scrape", "crawl", "fetch", "spider", "爬取"],
    "图片": ["image", "img", "picture", "photo", "pic"],
    "视频": ["video", "movie", "mp4", "vlog"],
    "翻译": ["translate", "translation", "i18n"],
    "代码": ["code", "coding", "program", "dev"],
    "测试": ["test", "testing", "qa"],
    "部署": ["deploy", "release", "publish", "发布", "上线"],
    "分析": ["analyze", "analysis", "analytics", "拆解"],
    "设计": ["design", "designer", "ui", "figma"],
    "邮件": ["email", "mail", "gmail"],
    "会议": ["meeting", "minutes", "纪要"],
    "记忆": ["memory", "mem", "knowledge", "知识库"],
}

SIGNATURE_STOP = {
    "the", "and", "for", "with", "from", "this", "that", "these", "those",
    "can", "any", "all", "use", "used", "using", "user", "users", "tool",
    "tools", "skill", "agent", "workflow", "task", "tasks", "file", "files",
    "content", "text", "system", "project", "support", "supports", "create",
    "creates", "generate", "generates", "make", "makes", "help", "helps",
    "guide", "guidance", "need", "needs", "when", "request", "requests",
    "include", "includes", "process", "processing", "handle", "handles",
    "claude", "codex", "openai",
    "first", "like", "mode", "hint", "invoke", "invokes", "detect", "detects",
    "behavior", "best-effort", "branch", "exposes", "fallback", "default",
    "run", "runs", "skills", "state", "via", "preamble", "reminder", "reminders",
    "使用", "用于", "适合", "用户", "需要", "可以", "自动", "这个", "那个",
    "进行", "工具", "技能", "功能", "场景", "命令", "文件", "内容", "数据",
    "信息", "结果", "或者", "按照", "根据", "然后", "基于", "提供", "我们",
    "所以", "因此", "以及", "相关", "能力", "任务", "流程", "原文", "描述",
}


def _synonym_index() -> dict[str, str]:
    index: dict[str, str] = {}
    for canonical, aliases in SIGNATURE_SYNONYMS.items():
        c = canonical.lower()
        index[c] = c
        for alias in aliases:
            index[str(alias).lower()] = c
    return index


SYNONYM_INDEX = _synonym_index()


def signature_tokenize(text: str) -> set[str]:
    """Token set for user-facing similarity: simple and explainable."""
    low = (text or "").lower()
    out: set[str] = set()
    for word in re.findall(r"[a-z][a-z0-9_-]{1,}", low):
        if len(word) < 3 or word in SIGNATURE_STOP:
            continue
        out.add(SYNONYM_INDEX.get(word, word))
    for span in re.findall(r"[\u4e00-\u9fff]+", low):
        for key, canonical in SYNONYM_INDEX.items():
            if re.search(r"[\u4e00-\u9fff]", key) and key in span:
                out.add(canonical)
        for size in (2, 3):
            if len(span) < size:
                continue
            for i in range(len(span) - size + 1):
                token = span[i:i + size]
                if token not in SIGNATURE_STOP:
                    out.add(SYNONYM_INDEX.get(token, token))
    return out


def jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0
    union = len(a | b)
    return len(a & b) / union if union else 0


def skill_signature_text(skill_dir: str | Path, name: str) -> str:
    """Read the light signature fields used by default similarity."""
    skill_dir = Path(skill_dir)
    skill_md = skill_dir / "SKILL.md"
    parts = [name]
    try:
        content = skill_md.read_text("utf-8", errors="ignore")
        frontmatter, body = parse_frontmatter(content)
        desc = str(frontmatter.get("description") or "").strip()
        if desc:
            parts.append(desc)
        for key in ("keywords", "tags", "category"):
            value = frontmatter.get(key)
            if isinstance(value, list):
                parts.extend(str(v) for v in value)
            elif value:
                parts.append(str(value))
        headings: list[str] = []
        for line in body.splitlines():
            match = re.match(r"^\s{0,3}#{1,3}\s+(.+?)\s*$", line)
            if match:
                headings.append(match.group(1).strip())
            if len(headings) >= 4:
                break
        parts.extend(headings)
    except Exception:
        pass
    return " ".join(parts)


def similar_group_key(member_refs: Iterable[dict], source: str = "signature") -> str:
    raw = source + "|" + "|".join(
        sorted(f"{m.get('name', '')}@{m.get('dir', '')}" for m in member_refs)
    )
    return hashlib.sha1(raw.encode("utf-8", errors="ignore")).hexdigest()[:20]


def compute_signature_similarity(
    skill_refs: list[dict],
    *,
    threshold: float = 0.30,
    max_group: int = 5,
    ignored_keys: set[str] | None = None,
    classify_skill: Callable[[str, str], str] | None = None,
) -> list[dict]:
    """Compute explainable Jaccard similarity from name/description/keywords."""
    ignored_keys = ignored_keys or set()
    refs: list[dict] = []
    for ref in skill_refs:
        name = ref.get("name", "")
        dir_path = ref.get("dir", "")
        if not name or not dir_path:
            continue
        skill_dir = Path(dir_path) / name
        if not (skill_dir.is_dir() or skill_dir.is_symlink()):
            continue
        if not (skill_dir / "SKILL.md").exists():
            continue
        tokens = signature_tokenize(skill_signature_text(skill_dir, name))
        if tokens:
            refs.append({
                "name": name,
                "dir": str(Path(dir_path)),
                "agent": ref.get("agent") or "",
                "tokens": tokens,
            })
    if len(refs) <= 1:
        return []

    pairs: list[tuple[int, int, float, list[str]]] = []
    for i in range(len(refs)):
        for j in range(i + 1, len(refs)):
            a, b = refs[i], refs[j]
            if a["name"] == b["name"]:
                continue
            score = jaccard(a["tokens"], b["tokens"])
            if score >= threshold:
                pairs.append((i, j, score, sorted(a["tokens"] & b["tokens"])))
    if not pairs:
        return []

    parent = list(range(len(refs)))
    size = [1] * len(refs)

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb and size[ra] + size[rb] <= max_group:
            parent[ra] = rb
            size[rb] += size[ra]

    pairs.sort(key=lambda x: x[2], reverse=True)
    pair_scores = {}
    for i, j, score, shared in pairs:
        pair_scores[(i, j)] = (score, shared)
        union(i, j)

    clusters: dict[int, list[int]] = {}
    for idx in range(len(refs)):
        clusters.setdefault(find(idx), []).append(idx)

    groups: list[dict] = []
    for members in clusters.values():
        if len(members) < 2:
            continue
        member_refs = [refs[i] for i in members]
        group_key = similar_group_key(member_refs, "signature")
        if group_key in ignored_keys:
            continue
        scores: list[float] = []
        shared_sets: list[set[str]] = []
        best: tuple[str | None, str | None, float, list[str]] = (None, None, 0, [])
        for a_pos in range(len(members)):
            for b_pos in range(a_pos + 1, len(members)):
                i, j = sorted((members[a_pos], members[b_pos]))
                score, shared = pair_scores.get(
                    (i, j),
                    (jaccard(refs[i]["tokens"], refs[j]["tokens"]), sorted(refs[i]["tokens"] & refs[j]["tokens"])),
                )
                scores.append(score)
                shared_sets.append(set(shared))
                if score > best[2]:
                    best = (refs[i]["name"], refs[j]["name"], score, shared)
        avg = sum(scores) / len(scores) if scores else 0
        common = set.intersection(*shared_sets) if shared_sets else set()
        if not common:
            common = set(best[3])
        names = [refs[i]["name"] for i in members]
        meta = {refs[i]["name"]: {"agent": refs[i]["agent"], "dir": refs[i]["dir"]} for i in members}
        classify = classify_skill or (lambda _name, _desc="": "other")
        cats = Counter(classify(name, "") for name in names)
        groups.append({
            "id": group_key,
            "decision_key": group_key,
            "skills": sorted(names),
            "skills_meta": meta,
            "score": round(avg, 4),
            "avg_score": round(avg, 4),
            "category": cats.most_common(1)[0][0] if cats else "other",
            "source": "signature",
            "scope": "light_signature",
            "shared_terms": sorted(common)[:10],
            "strongest_pair": {"skills": [best[0], best[1]], "score": round(best[2], 4)} if best[0] else None,
            "reason": "轻量相似：基于名称、description、keywords 和标题的关键词重叠。",
        })

    groups.sort(key=lambda g: (g["score"], len(g["skills"])), reverse=True)
    return groups


# -- Deep TF-IDF similarity retained for full audit mode --

_TFIDF_STOP = frozenset((
    # English
    "the","be","to","of","and","a","in","that","have","i","it","for","not","on","with",
    "he","as","you","do","at","this","but","his","by","from","they","we","say","her",
    "she","or","an","will","my","one","all","would","there","their","what","so","up",
    "out","if","about","who","get","which","go","me","when","make","can","like","time",
    "no","just","him","know","take","people","into","year","your","good","some","could",
    "them","see","other","than","then","now","look","only","come","its","over","think",
    "also","back","after","use","two","how","our","work","first","well","way","even",
    "new","want","because","any","these","give","day","most","us","is","are","was",
    "were","been","being","has","had","did","does","done","shall","should","may",
    "might","must","need","let","very","much","more","many","such","each","every",
    "own","same","both","few","too","here","where","why","while","during","before",
    "between","after","above","below","through","under","again","further","once",
    # Skill-specific noise
    "skill","claude","code","agent","tool","use","using","used","file","files","run",
    "running","command","commands","help","when","ask","this","that","will","task",
    "set","based","add","create","write","read","update","check","example","review",
    "test","build","autom","automat","perform","allows","provide","supports",
    "including","default","following","specific","via","user","users","project",
    "note","the","and","for","with","from","your","also","can","run","all","code",
    # Chinese
    "的","了","在","是","我","有","和","就","不","人","都","一","一个","上","也","很",
    "到","说","要","去","你","会","着","没有","看","好","自己","这","他","她","它","们",
    "那","些","什么","怎么","如果","因为","所以","但","但是","而且","或者","可以","已经",
    "这个","那个","还是","就是","不是","可能","需要","应该","使用","进行","可以","以及",
    "通过","根据","关于","对于","由于","之间","一些","这些","那些","然后","此外",
    "功能","工具","使用","提供","支持","运行","执行","操作","命令","文件","目录",
    "项目","设置","配置","自动","管理","分析","检查","处理","生成","创建","修改",
    "包括","默认","指定","相关","确保","建议","参考","文档","注意","代码",
))

def _tfidf_tokenize(text):
    """Mixed Chinese/English tokenizer for TF-IDF."""
    text = text.lower()
    # English words (2+ letters)
    en_tokens = re.findall(r'[a-z]{2,}', text)
    # Chinese segments (2+ chars) → bigram split for long segments
    cn_tokens = []
    for seg in re.findall(r'[一-鿿]{2,}', text):
        if len(seg) == 2:
            cn_tokens.append(seg)
        else:
            # sliding bigram
            for i in range(len(seg) - 1):
                cn_tokens.append(seg[i:i + 2])
    tokens = [t for t in en_tokens + cn_tokens if t not in _TFIDF_STOP]
    return tokens

def compute_tfidf_similarity(target_path, skills_data, docs=None, agent_name=None):
    """Compute pairwise TF-IDF cosine similarity between skills.
    Returns list of overlap groups with scores.
    If docs is provided, use it instead of reading from disk.
    If agent_name is provided, use it instead of inferring from target_path.
    """
    target_dir = Path(target_path)
    target_agent = agent_name or _agent_from_path(target_path)
    if len(skills_data) <= 1:
        return []
    # No cap — TF-IDF on a few hundred items is cheap enough

    # Check cache only when reading from disk (docs=None)
    tfidf_cache = None
    if docs is None:
        cache_key = _cache_path(target_path)
        tfidf_cache = cache_key.parent / f"tfidf-{cache_key.name}"
        if tfidf_cache.exists():
            try:
                cached = json.loads(tfidf_cache.read_text("utf-8"))
                if time.time() - cached.get("_ts", 0) < 300:
                    return cached.get("groups", [])
            except Exception:
                pass

    # Step 1: Read full SKILL.md content for each skill
    if docs is None:
        docs = {}
        for s in skills_data:
            name = s["name"]
            skill_md = target_dir / name / "SKILL.md"
            if skill_md.exists():
                try:
                    content = skill_md.read_text("utf-8", errors="ignore")
                    docs[name] = content
                except Exception:
                    docs[name] = ""
            else:
                docs[name] = ""

    if len(docs) <= 1:
        return []

    # Step 2: Tokenize
    tokenized = {name: _tfidf_tokenize(text) for name, text in docs.items()}
    # Filter out docs with no tokens
    tokenized = {k: v for k, v in tokenized.items() if v}
    if len(tokenized) <= 1:
        return []

    names = list(tokenized.keys())
    N = len(names)

    # Step 3: Build TF-IDF vectors
    # Document frequency
    df = Counter()
    for tokens in tokenized.values():
        unique_tokens = set(tokens)
        for t in unique_tokens:
            df[t] += 1

    # IDF
    idf = {t: math.log(N / (cnt + 1)) for t, cnt in df.items()}

    # TF-IDF vectors (sparse: dict of token -> weight)
    vectors = {}
    for name in names:
        tf = Counter(tokenized[name])
        total_terms = sum(tf.values())
        if total_terms == 0:
            vectors[name] = {}
            continue
        vec = {}
        for t, cnt in tf.items():
            tfidf = (cnt / total_terms) * idf.get(t, 0)
            if tfidf > 0:
                vec[t] = tfidf
        # Normalize to unit length
        norm = math.sqrt(sum(v * v for v in vec.values()))
        if norm > 0:
            vec = {t: v / norm for t, v in vec.items()}
        vectors[name] = vec

    # Step 4: Pairwise cosine similarity (dot product of unit vectors)
    THRESHOLD = 0.50
    # Build similar pairs only (no transitive grouping — prevents chain inflation)
    pairs = []
    for i in range(N):
        vi = vectors[names[i]]
        if not vi:
            continue
        for j in range(i + 1, N):
            vj = vectors[names[j]]
            if not vj:
                continue
            common_keys = vi.keys() & vj.keys()
            sim = sum(vi[k] * vj[k] for k in common_keys)
            if sim >= THRESHOLD:
                pairs.append((names[i], names[j], sim))

    # Step 5: Group pairs that share a skill (compact groups, no long chains)
    # Use Union-Find but with a max-group-size cap
    parent = {n: n for n in names}
    group_size = {n: 1 for n in names}
    MAX_GROUP = 6

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb and group_size.get(ra, 1) + group_size.get(rb, 1) <= MAX_GROUP:
            parent[ra] = rb
            group_size[rb] = group_size.get(ra, 1) + group_size.get(rb, 1)

    # Sort pairs by score descending so strongest pairs form the core groups
    pairs.sort(key=lambda p: p[2], reverse=True)
    pair_scores = {}
    for a, b, sim in pairs:
        pair_scores[(a, b)] = sim
        union(a, b)

    groups_map = {}
    for n in names:
        root = find(n)
        groups_map.setdefault(root, []).append(n)

    # Step 6: Assemble result with scores
    result = []
    for root, members in groups_map.items():
        if len(members) < 2:
            continue
        scores = []
        for i in range(len(members)):
            for j in range(i + 1, len(members)):
                key = (members[i], members[j])
                rev_key = (members[j], members[i])
                s = pair_scores.get(key, pair_scores.get(rev_key, 0))
                scores.append(s)
        avg_score = sum(scores) / len(scores) if scores else 0
        shared_terms = []
        try:
            shared = set(tokenized.get(members[0], []))
            for member in members[1:]:
                shared &= set(tokenized.get(member, []))
            weighted = sorted(
                shared,
                key=lambda t: idf.get(t, 0) * sum(Counter(tokenized.get(m, [])).get(t, 0) for m in members),
                reverse=True,
            )
            shared_terms = weighted[:10]
        except Exception:
            shared_terms = []
        strongest_pair = None
        if members and scores:
            best = (None, None, 0)
            for i in range(len(members)):
                for j in range(i + 1, len(members)):
                    key = (members[i], members[j])
                    rev_key = (members[j], members[i])
                    s = pair_scores.get(key, pair_scores.get(rev_key, 0))
                    if s > best[2]:
                        best = (members[i], members[j], s)
            if best[0]:
                strongest_pair = {"skills": [best[0], best[1]], "score": round(best[2], 4)}
        # Category: use _classify_skill for primary category
        cats = Counter(_classify_skill(m, "") for m in members)
        primary_cat = cats.most_common(1)[0][0] if cats else "other"
        result.append({
            "skills": sorted(members),
            "skills_meta": {m: {"agent": target_agent, "dir": str(target_dir)} for m in members},
            "score": round(avg_score, 4),
            "avg_score": round(avg_score, 4),
            "category": primary_cat,
            "source": "tfidf",
            "scope": "current_dir",
            "shared_terms": shared_terms,
            "strongest_pair": strongest_pair,
        })

    # Sort by score descending
    result.sort(key=lambda g: g["score"], reverse=True)

    # Save cache
    try:
        tfidf_cache.write_text(
            json.dumps({"_ts": time.time(), "groups": result}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception:
        pass

    return result
