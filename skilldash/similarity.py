"""Explainable skill similarity helpers."""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from pathlib import Path
from typing import Callable, Iterable

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
