"""Rule-based taxonomy for understanding AI skills.

These rules are deliberately transparent and dependency-free.  They are not a
replacement for model-generated summaries; they provide a portable baseline for
open-source users who do not configure an API key.
"""

from __future__ import annotations

from dataclasses import dataclass
import re


@dataclass(frozen=True)
class Rule:
    key: str
    label: str
    keywords: tuple[str, ...]


CAPABILITY_RULES: tuple[Rule, ...] = (
    Rule("strategy", "产品判断", ("office hours", "brainstorm", "worth building", "product idea", "startup", "narrowest wedge", "demand reality")),
    Rule("browser", "浏览器", ("browser", "playwright", "puppeteer", "网页", "浏览器", "web page", "website")),
    Rule("web-search", "联网搜索", ("web search", "search", "google", "bing", "exa", "tavily", "搜索", "检索")),
    Rule("github", "GitHub", ("github", "pull request", "issue", "repo", "commit", "git ")),
    Rule("memory", "记忆", ("memory", "remember", "knowledge", "nowledge", "gbrain", "记忆", "知识库")),
    Rule("file", "文件", ("file", "filesystem", "read file", "write file", "文件", "目录")),
    Rule("code", "代码", ("codebase", "coding", "debug", "refactor", "test", "lint", "typescript", "python", "代码")),
    Rule("docs", "文档", ("document", "docs", "pdf", "docx", "pptx", "readme", "文档")),
    Rule("image", "图像", ("image", "photo", "picture", "screenshot", "图片", "图像", "截图")),
    Rule("video-audio", "音视频", ("video", "audio", "ffmpeg", "subtitle", "视频", "音频", "字幕")),
    Rule("data", "数据", ("data", "csv", "spreadsheet", "sql", "analysis", "数据", "表格", "分析")),
    Rule("comms", "通讯", ("email", "slack", "feishu", "lark", "wechat", "飞书", "邮件")),
    Rule("deploy", "部署", ("deploy", "release", "ship", "ci", "canary", "部署", "发布")),
    Rule("security", "安全", ("security", "secret", "token", "guard", "safe", "安全", "密钥")),
)


SCENARIO_RULES: tuple[Rule, ...] = (
    Rule("brainstorming", "头脑风暴", ("brainstorm", "help me think through", "office hours", "想法", "头脑风暴")),
    Rule("product-discovery", "产品判断", ("worth building", "product idea", "demand reality", "status quo", "narrowest wedge", "startup", "需求判断")),
    Rule("coding", "代码开发", ("codebase", "coding", "debug", "refactor", "test", "frontend", "backend", "代码")),
    Rule("review", "评审排查", ("review", "audit", "diagnose", "triage", "investigate", "评审", "排查")),
    Rule("automation", "自动化执行", ("automation", "workflow", "agent", "browser", "自动化")),
    Rule("research", "资料检索", ("research", "search", "scrape", "crawl", "调研", "检索")),
    Rule("writing", "内容写作", ("write", "blog", "copy", "seo", "content", "写作", "内容")),
    Rule("knowledge", "知识管理", ("memory", "knowledge", "note", "obsidian", "记忆", "知识")),
    Rule("communication", "协作沟通", ("email", "slack", "feishu", "lark", "飞书", "沟通")),
    Rule("media", "媒体处理", ("image", "video", "audio", "screenshot", "图片", "视频", "音频")),
    Rule("shipping", "发布上线", ("deploy", "release", "ship", "canary", "发布", "上线")),
    Rule("ops", "环境运维", ("setup", "install", "runtime", "server", "docker", "环境", "运维")),
)


RISK_RULES: tuple[Rule, ...] = (
    Rule("writes-files", "可能修改文件", ("write", "edit", "delete", "patch", "move", "rename", "写入", "删除", "修改")),
    Rule("runs-commands", "可能执行命令", ("shell", "terminal", "command", "execute", "subprocess", "命令", "终端")),
    Rule("network", "可能访问网络", ("http", "api", "web", "browser", "github", "search", "网络", "网页")),
    Rule("credentials", "可能需要密钥", ("token", "api key", "secret", "password", "oauth", "cookie", "密钥", "密码")),
    Rule("external-side-effect", "可能产生外部动作", ("send", "post", "publish", "deploy", "email", "upload", "发送", "发布", "上传")),
)


TARGET_RULES: tuple[Rule, ...] = (
    Rule("founder-builder", "创业/产品构建者", ("startup", "builder", "product idea", "worth building", "side project", "hackathon", "创业", "产品")),
    Rule("developer", "开发者", ("codebase", "coding", "debug", "github", "test", "deploy", "代码")),
    Rule("operator", "Agent 管理者", ("agent", "skill", "runtime", "workflow", "memory", "自动化")),
    Rule("researcher", "研究/内容人员", ("research", "search", "write", "content", "调研", "写作")),
    Rule("business", "业务/销售人员", ("sales", "crm", "email", "lead", "客户", "销售")),
    Rule("designer", "设计/媒体人员", ("image", "video", "figma", "visual", "图片", "视觉", "设计图")),
)


def _keyword_present(text: str, keyword: str) -> bool:
    """Match English terms as words/phrases and Chinese terms by substring."""
    kw = keyword.strip().lower()
    if not kw:
        return False
    if re.search(r"[\u4e00-\u9fff]", kw):
        return kw in text
    if re.search(r"[a-z0-9]", kw):
        pattern = r"(?<![a-z0-9_+-])" + re.escape(kw) + r"(?![a-z0-9_+-])"
        return re.search(pattern, text) is not None
    return kw in text


def match_rules(text: str, rules: tuple[Rule, ...], limit: int = 4) -> list[dict[str, str]]:
    low = text.lower()
    scored: list[tuple[int, int, Rule]] = []
    for idx, rule in enumerate(rules):
        score = sum(1 for kw in rule.keywords if _keyword_present(low, kw))
        if score:
            scored.append((score, idx, rule))
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [{"key": rule.key, "label": rule.label} for _, _, rule in scored[:limit]]
