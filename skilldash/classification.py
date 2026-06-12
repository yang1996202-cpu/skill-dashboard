"""Skill category helpers shared by scans and UI APIs."""

from __future__ import annotations

from pathlib import Path


_CLASSIFY_KW = {
    'code-dev': ['tdd','frontend','backend','api','debug','refactor','lint','ci','git','commit',
        'pull-request','review','typescript','python','rust','react','vue','next','npm','pnpm',
        'bun','dev','ios','qa','fix','clean','deploy','benchmark','test','plan','eng','devex',
        'guard','spec','code','ios-','sync','qa-only','learning','learn','handoff','交接',
        'checkpoint','cleanup','session','skill-manager'],
    'content': ['write','article','blog','copywriting','seo','newsletter','writer','content',
        'khazix','title','prd','explain','解释','洞察','insight','report'],
    'image-gen': ['image','picture','photo','cover','banner','illustration','dalle','midjourney',
        'flux','logo','glm-image','seedream','illustrator','picgo'],
    'video-audio': ['video','audio','ffmpeg','remotion','mp4','podcast','subtitle','srt','tts','voice'],
    'data': ['data','analytics','chart','csv','excel','dashboard','visualization','stats',
        'metrics','sql','analysis','笔记','知识库','note','knowledge'],
    'web-search': ['search','web','browse','scrape','crawl','spider','google','bing',
        'perplexity','web-access','gstack'],
    'social': ['xhs','twitter','weibo','wechat','instagram','tiktok','youtube','bilibili',
        'wechat-styler'],
    'doc': ['pdf','docx','pptx','xlsx','notion','confluence','readme','make-pdf','document'],
    'comms': ['email','mail','slack','feishu','lark','dingtalk','telegram','discord'],
    'design': ['figma','canvas','theme','brand','sketch','wireframe','prototype','tailwind',
        'css','design','design-html'],
    'translate': ['translate','translation','i18n','l10n','locale'],
    'sysadmin': ['server','docker','k8s','kubernetes','devops','ssh','linux','nginx','infra',
        'terraform','aws','gcp','azure','deploy','setup','macos','sleep','caffeinate','pmset'],
    'persona': ['personality','persona','mbti','sbti','character','role','elon','feynman',
        '女娲','造人','skill'],
    'finance': ['finance','invoice','receipt','stock','trade','accounting'],
    'sales': ['sales','crm','销售','线索','客户','lead','求职','岗位','面试','简历',
        'boss','job','hiring'],
}

def _classify_skill(name, description=""):
    """Classify a skill by name + description using keyword matching."""
    low = (name + " " + description).lower()
    best, best_score = "other", 0
    for cat, kws in _CLASSIFY_KW.items():
        score = sum(1 for kw in kws if kw in low)
        if score > best_score:
            best_score = score
            best = cat
    return best if best_score > 0 else "other"

def _read_skill_description(skill_dir):
    """Read description from SKILL.md frontmatter. Handles YAML multiline."""
    skill_md = Path(skill_dir) / "SKILL.md"
    if not skill_md.exists():
        return ""
    try:
        text = skill_md.read_text("utf-8", errors="ignore")[:2000]
        if not text.startswith("---"):
            return ""
        end = text.find("---", 3)
        if end <= 0:
            return ""
        fm_lines = text[3:end].splitlines()
        for i, line in enumerate(fm_lines):
            stripped = line.strip()
            if stripped.startswith("description:"):
                val = stripped.split(":", 1)[1].strip()
                if val in (">", "|", ">-", "|-", ">+", "|+"):
                    parts = []
                    for cont in fm_lines[i + 1:]:
                        if cont and not cont[0].isspace():
                            break
                        parts.append(cont.strip())
                    return " ".join(parts)
                return val.strip("'\"")
    except Exception:
        pass
    return ""
