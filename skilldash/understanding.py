"""Portable, rule-based skill understanding.

The first version is intentionally deterministic: it works offline and does not
require model credentials.  A later optional AI enhancer can write into the same
schema without changing the UI contract.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from .skill_parser import ParsedSkill, parse_skill_dir
from .taxonomy import CAPABILITY_RULES, RISK_RULES, SCENARIO_RULES, TARGET_RULES, match_rules


SCHEMA_VERSION = 12


SUMMARY_TEMPLATES = (
    (("strategy",), "用于产品想法判断和头脑风暴，适合在写代码前澄清需求、用户和最小切入点。"),
    (("browser",), "用于让 Agent 打开、操作或检查网页，适合浏览器自动化、网页验证和资料抓取。"),
    (("web-search",), "用于联网搜索和资料检索，适合快速查找网页、项目或外部资料。"),
    (("github",), "用于处理 GitHub 仓库、Issue、PR 或提交记录，适合代码协作和项目维护。"),
    (("memory",), "用于读取、保存或整理长期记忆/知识库，适合跨会话保留上下文。"),
    (("code",), "用于代码开发、排查、评审或测试，适合工程实现和质量检查。"),
    (("docs",), "用于创建、读取或整理文档材料，适合 README、报告、PPT、PDF 等场景。"),
    (("image",), "用于图像、截图或视觉素材处理，适合生成、识别或审查图片内容。"),
    (("video-audio",), "用于视频、音频或字幕处理，适合媒体剪辑、转码和内容加工。"),
    (("data",), "用于数据查询、分析或表格处理，适合 CSV、数据库和指标分析。"),
    (("comms",), "用于邮件、飞书、Slack 等协作沟通，适合消息收发和工作流通知。"),
    (("deploy",), "用于发布、部署或上线检查，适合交付前后的工程流程。"),
    (("security",), "用于安全检查、密钥防护或风险审计，适合敏感操作前的把关。"),
)


def _cache_file(cache_root: str | Path, skill: ParsedSkill) -> Path:
    safe_name = re.sub(r"[^a-zA-Z0-9_.@+-]+", "_", skill.name)[:80] or "skill"
    return Path(cache_root) / "understanding" / f"{safe_name}-{skill.content_hash[:16]}.json"


def _clean_text(text: str, limit: int = 180) -> str:
    text = re.sub(r"\s+", " ", text or "").strip().strip("-:：。")
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _is_mostly_chinese(text: str) -> bool:
    if not text:
        return False
    zh = len(re.findall(r"[\u4e00-\u9fff]", text))
    return zh >= 6 and zh >= len(text) * 0.15


def _first_meaningful_sentence(text: str) -> str:
    text = _clean_text(text, 260)
    parts = re.split(r"(?<=[。！？.!?])\s+", text)
    for part in parts:
        part = _clean_text(part, 120)
        if len(part) >= 12:
            return part
    return _clean_text(text, 120)


def _english_hint_to_zh(skill: ParsedSkill, capabilities: list[dict[str, str]]) -> str:
    cap_keys = {c["key"] for c in capabilities}
    for required, summary in SUMMARY_TEMPLATES:
        if all(key in cap_keys for key in required):
            return summary
    label = skill.name.replace("-", " ").replace("_", " ")
    if skill.description:
        return f"用于 {label} 相关工作；原始描述偏英文，建议查看详情确认具体触发条件。"
    if skill.headings:
        return f"用于 {skill.headings[0]} 相关流程，建议展开原文确认具体用法。"
    return f"用于 {label} 相关任务，当前只能从名称和少量结构推断用途。"


def _build_summary(skill: ParsedSkill, capabilities: list[dict[str, str]]) -> str:
    if skill.description:
        if _is_mostly_chinese(skill.description):
            return _first_meaningful_sentence(skill.description)
        return f"原文描述：{_first_meaningful_sentence(skill.description)}"
    if skill.headings:
        heading = skill.headings[0]
        if _is_mostly_chinese(heading):
            return f"围绕“{_clean_text(heading, 60)}”提供操作指引。"
    return _english_hint_to_zh(skill, capabilities)


def _evidence(skill: ParsedSkill) -> list[str]:
    snippets: list[str] = []
    if skill.description:
        snippets.append(_clean_text(skill.description, 160))
    for heading in _intent_headings(skill.body)[:4]:
        cleaned = _clean_text(heading, 100)
        if cleaned and cleaned not in snippets:
            snippets.append(cleaned)
        if len(snippets) >= 3:
            break
    if not snippets and skill.body:
        intent_body = re.split(r"\n##\s+Preamble\b", skill.body, maxsplit=1, flags=re.I)[0]
        for line in intent_body.splitlines():
            cleaned = _clean_text(line, 140)
            if len(cleaned) >= 20:
                snippets.append(cleaned)
                break
    return snippets[:4]


def _confidence(skill: ParsedSkill, capabilities: list[dict[str, str]], scenarios: list[dict[str, str]]) -> str:
    if skill.description and _is_mostly_chinese(skill.description) and capabilities and scenarios:
        return "high"
    if skill.description or capabilities or scenarios:
        return "medium"
    return "low"


def _body_without_code(body: str) -> str:
    body = re.sub(r"```.*?```", " ", body, flags=re.S)
    body = re.sub(r"`[^`]+`", " ", body)
    return body


def _body_before_preamble(body: str) -> str:
    return re.split(r"\n##\s+Preamble\b", body, maxsplit=1, flags=re.I)[0]


def _intent_headings(body: str) -> list[str]:
    headings: list[str] = []
    for line in _body_before_preamble(body).splitlines():
        match = re.match(r"^\s{0,3}#{1,3}\s+(.+?)\s*$", line)
        if match:
            heading = re.sub(r"\s+#*$", "", match.group(1)).strip()
            if heading:
                headings.append(heading)
    return headings


def _intent_text(skill: ParsedSkill) -> str:
    """Return the part most likely to describe purpose, not implementation."""
    body = _body_before_preamble(skill.body)
    body = _body_without_code(body)
    body = re.sub(r"<!--.*?-->", " ", body, flags=re.S)
    return "\n".join(
        [
            skill.name,
            skill.description,
            " ".join(_intent_headings(skill.body)[:4]),
            body[:1800],
        ]
    )


def _frontmatter_text(skill: ParsedSkill) -> str:
    parts: list[str] = []
    for key, value in skill.frontmatter.items():
        if isinstance(value, list):
            parts.append(f"{key}: {' '.join(str(v) for v in value)}")
        elif isinstance(value, dict):
            parts.append(f"{key}: {json.dumps(value, ensure_ascii=False)}")
        else:
            parts.append(f"{key}: {value}")
    return "\n".join(parts)


def build_understanding(skill: ParsedSkill) -> dict:
    intent_text = _intent_text(skill)
    risk_text = "\n".join([intent_text, _frontmatter_text(skill), skill.body[:5000]])
    capabilities = match_rules(intent_text, CAPABILITY_RULES, limit=5)
    scenarios = match_rules(intent_text, SCENARIO_RULES, limit=4)
    risks = match_rules(risk_text, RISK_RULES, limit=4)
    target_users = match_rules(intent_text, TARGET_RULES, limit=3)
    summary = _build_summary(skill, capabilities)
    if not target_users:
        target_users = [{"key": "operator", "label": "Agent 管理者"}]
    return {
        "schema": SCHEMA_VERSION,
        "name": skill.name,
        "path": skill.path,
        "content_hash": skill.content_hash,
        "summary_zh": summary,
        "original_description": skill.description,
        "needs_ai_translation": bool(skill.description and not _is_mostly_chinese(skill.description)),
        "scenarios": scenarios,
        "capabilities": capabilities,
        "risks": risks,
        "target_users": target_users,
        "confidence": _confidence(skill, capabilities, scenarios),
        "evidence": _evidence(skill),
        "size": skill.size,
        "source": "rules",
    }


def understand_skill(skill_dir: str | Path, cache_root: str | Path) -> dict:
    skill = parse_skill_dir(skill_dir)
    cache_path = _cache_file(cache_root, skill)
    if cache_path.exists():
        try:
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
            if (
                cached.get("schema") == SCHEMA_VERSION
                and cached.get("content_hash") == skill.content_hash
            ):
                return cached
        except Exception:
            pass
    result = build_understanding(skill)
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass
    return result


def compact_understanding(full: dict) -> dict:
    return {
        "summary_zh": full.get("summary_zh", ""),
        "original_description": full.get("original_description", ""),
        "needs_ai_translation": full.get("needs_ai_translation", False),
        "scenarios": full.get("scenarios", [])[:3],
        "capabilities": full.get("capabilities", [])[:4],
        "risks": full.get("risks", [])[:2],
        "confidence": full.get("confidence", "low"),
        "source": full.get("source", "rules"),
    }
