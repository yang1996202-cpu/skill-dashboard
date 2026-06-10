#!/usr/bin/env python3
"""Skill Dashboard — 零依赖本地 WebUI，可视化管理 AI skill 文件"""

import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import urllib.parse
import webbrowser
from collections import Counter
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse, parse_qs

PORT = 3457
STATE_DIR = Path(__file__).parent / ".data" / "state"
HTML_FILE = Path(__file__).parent / "index.html"
CACHE_DIR = Path(__file__).parent / ".data" / "cache"
DIAG_LOG = Path(__file__).parent / ".data" / "cache" / "diag.log"


def _cache_path(target_path):
    """Get cache file path for a target. Resolves ~ and relative paths first."""
    # Normalize: expand ~ and resolve to absolute path for consistent keys
    p = Path(target_path).expanduser().resolve()
    safe = re.sub(r'[^\w]', '_', str(p))
    return CACHE_DIR / f"{safe}.json"


def load_cached_diagnosis(target_path):
    """Load cached diagnosis for a target, or None."""
    cp = _cache_path(target_path)
    if cp.exists():
        try:
            return json.loads(cp.read_text("utf-8"))
        except Exception:
            pass
    return None


def save_cached_diagnosis(target_path, data):
    """Save diagnosis result to cache."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cp = _cache_path(target_path)
    cp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


# ── Global classification ──

# Keyword table matching frontend CAT_KW for consistency
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


# ── TF-IDF similarity detection ──

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
    THRESHOLD = 0.45
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


def _find_same_name_duplicates(dirs):
    """Find same-name skills across the given directory list.
    Returns (duplicates_identical, duplicates_same_name, all_skills_map).
    """
    all_skills = {}  # name -> [{dir, agent}]
    for tdir in dirs:
        dir_path = str(tdir)
        try:
            entries = sorted(tdir.iterdir())
        except Exception:
            continue
        for d in entries:
            if not (d.is_dir() or d.is_symlink()):
                continue
            if not (d / "SKILL.md").exists():
                continue
            name = d.name
            if name not in all_skills:
                all_skills[name] = []
            all_skills[name].append({
                "dir": dir_path,
                "agent": _agent_from_path(dir_path),
            })

    duplicates_identical = []
    duplicates_same_name = []
    for name, locations in all_skills.items():
        if len(locations) < 2:
            continue
        hashes = {}
        for loc in locations:
            skill_md = Path(loc["dir"]) / name / "SKILL.md"
            try:
                h = hashlib.sha256(skill_md.read_bytes()).hexdigest()[:12]
            except Exception:
                h = "error"
            loc["hash"] = h
            hashes.setdefault(h, []).append(loc)

        agents = list(set(loc["agent"] for loc in locations))
        entry = {
            "name": name,
            "locations": locations,
            "agent_count": len(agents),
            "dir_count": len(locations),
            "hash_count": len(hashes),
        }
        if len(hashes) == 1:
            entry["type"] = "identical"
            duplicates_identical.append(entry)
        else:
            entry["type"] = "same_name_diff"
            duplicates_same_name.append(entry)

    duplicates_identical.sort(key=lambda d: d["dir_count"], reverse=True)
    duplicates_same_name.sort(key=lambda d: d["dir_count"], reverse=True)
    return duplicates_identical, duplicates_same_name


def _find_agent_cross_dir_similar(dirs):
    """Per-agent TF-IDF cross-directory similarity for the given directory list.
    Returns {agent_name: [overlap_groups]}.
    """
    dir_skills = {}  # dir_path -> [skill_names]
    agent_dirs = {}  # agent -> [dir_paths]
    for tdir in dirs:
        dir_path = str(tdir)
        skills_in_dir = []
        try:
            entries = sorted(tdir.iterdir())
        except Exception:
            continue
        for d in entries:
            if not (d.is_dir() or d.is_symlink()):
                continue
            if not (d / "SKILL.md").exists():
                continue
            skills_in_dir.append(d.name)
        if skills_in_dir:
            dir_skills[dir_path] = skills_in_dir
            agent = _agent_from_path(dir_path)
            agent_dirs.setdefault(agent, []).append(dir_path)

    agent_similar = {}
    for agent_name, agent_dir_list in agent_dirs.items():
        if len(agent_dir_list) < 2:
            continue
        agent_skills = []
        agent_docs = {}
        seen_names = set()
        for dp in agent_dir_list:
            for skill_name in dir_skills.get(dp, []):
                if skill_name in seen_names:
                    continue
                seen_names.add(skill_name)
                skill_md = Path(dp) / skill_name / "SKILL.md"
                try:
                    content = skill_md.read_text("utf-8", errors="ignore")
                    agent_docs[skill_name] = content
                    agent_skills.append({"name": skill_name})
                except Exception:
                    pass
        if 1 < len(agent_skills):
            groups = compute_tfidf_similarity(
                target_path=agent_dir_list[0],
                skills_data=agent_skills,
                docs=agent_docs,
                agent_name=agent_name,
            )
            if groups:
                agent_similar[agent_name] = groups
    return agent_similar


def detect_cross_dir_overlaps():
    """Detect duplicate and similar skills across ALL directories.
    Uses _find_same_name_duplicates and _find_agent_cross_dir_similar helpers.
    Cached for 5 minutes.
    """
    # Cache (5-min TTL)
    cache_file = CACHE_DIR / "cross-dir-overlaps.json"
    if cache_file.exists():
        try:
            cached = json.loads(cache_file.read_text("utf-8"))
            if time.time() - cached.get("_ts", 0) < 300:
                return {k: v for k, v in cached.items() if not k.startswith("_")}
        except Exception:
            pass

    all_dirs = _discover_skill_dirs()

    # Same-name detection
    duplicates_identical, duplicates_same_name = _find_same_name_duplicates(all_dirs)

    # Agent summary
    agent_identical = {}
    for dup in duplicates_identical:
        for loc in dup["locations"]:
            agent_identical.setdefault(loc["agent"], []).append(dup["name"])
    agent_summary_final = []
    for ag, names in sorted(agent_identical.items(), key=lambda x: -len(x[1])):
        agent_summary_final.append({
            "agent": ag,
            "identical_count": len(names),
            "skills_sample": names[:5],
        })

    prunable = sum(d["dir_count"] - 1 for d in duplicates_identical)

    # Cross-directory TF-IDF similarity
    agent_similar = _find_agent_cross_dir_similar(all_dirs)

    # Count unique skills across all dirs for stats
    unique_names = set()
    total_dirs = 0
    for tdir in all_dirs:
        try:
            has_any = False
            for d in tdir.iterdir():
                if (d.is_dir() or d.is_symlink()) and (d / "SKILL.md").exists():
                    unique_names.add(d.name)
                    has_any = True
            if has_any:
                total_dirs += 1
        except Exception:
            pass

    result = {
        "duplicates_identical": duplicates_identical,
        "duplicates_same_name": duplicates_same_name,
        "agent_summary": agent_summary_final,
        "agent_similar": agent_similar,
        "total_unique_names": len(unique_names),
        "total_dirs_scanned": total_dirs,
        "total_identical": len(duplicates_identical),
        "total_same_name": len(duplicates_same_name),
        "total_prunable": prunable,
        "total_identical_locations": sum(d["dir_count"] for d in duplicates_identical),
    }

    try:
        cache_file.write_text(
            json.dumps({"_ts": time.time(), **result}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception:
        pass

    return result


def _agent_from_path(dir_path):
    """Infer agent name from directory path."""
    p = str(dir_path)
    agents = [
        (".claude", "Claude Code"), (".workbuddy", "WorkBuddy"), (".hermes", "Hermes"),
        (".agents", "通用 Agents"), (".codex", "Codex"), (".cursor", "Cursor"),
        (".alice", "Alice"), (".openclaw", "OpenClaw"), (".cc-switch", "CC-Switch"),
        (".qclaw", "QClaw"), (".cola", "Cola"), (".codebuddy", "CodeBuddy"),
    ]
    for prefix, name in agents:
        if prefix in p:
            return name
    parts = Path(p).parts
    # For .config/<agent>/skills, the real agent name is the child of .config
    for i, part in enumerate(parts):
        if part.startswith(".") and not part.startswith(".."):
            if part == ".config" and i + 1 < len(parts):
                return parts[i + 1]
            return part.lstrip(".")
    return Path(p).name


def _classify_skill_dir(dir_path):
    """Classify a skill directory by its nature based on path patterns.

    Returns one of:
    - 'user'       : User-created skills (main skills/ dir, no marketplace/cache/backup patterns)
    - 'marketplace': Ecosystem/plugin marketplace skills (marketplace, plugins, agent-plugins, extensions)
    - 'cache'      : Installation artifacts (snapshots, backups, cache, plugins-backup, vendor_imports)
    - 'cross-copy' : Cross-agent copies (e.g., gstack/.cursor/skills inside a skill dir)
    - 'project'    : Project-level skills (under ~/projects/)
    """
    p = str(dir_path).lower()
    home = str(Path.home()).lower()
    rel = p.replace(home + "/", "").replace(home + "\\", "")

    # Cache/backup: snapshots, backups, plugin caches, vendor imports
    cache_signals = [".snapshots", "backup", "plugins-backup", "plugins/cache",
                     "/cache/", "vendor_imports", ".tmp", ".temp",
                     "bundled-marketplaces", "/install/cache/"]
    for sig in cache_signals:
        if sig in p:
            return "cache"

    # Project-level: under ~/projects/ — check BEFORE cross-copy
    # because ~/projects/xz/.claude/skills is project-level, not cross-copy
    if "/projects/" in p or "\\projects\\" in p:
        return "project"

    # Cross-agent copy: .<agent>/skills at depth > 0 from home
    # Pattern: any .xxx/skills that is NOT the agent's own root skills dir.
    # Root: ~/.claude/skills (i=0 in rel_parts)
    # Cross-copy: ~/.skillslm/gstack/.cursor/skills (.cursor/skills at depth > 0)
    parts = Path(dir_path).parts
    home_parts = Path(Path.home()).parts
    rel_parts = parts[len(home_parts):]
    for i, pt in enumerate(rel_parts):
        if pt.startswith(".") and not pt.startswith("..") and i + 1 < len(rel_parts) and rel_parts[i + 1] == "skills":
            if i > 0:  # Not at agent root level (i=0 means ~/.agent/skills)
                return "cross-copy"

    # Marketplace: plugin stores, extension stores, agent-plugin repos
    market_signals = ["marketplace", "agent-plugins", "/plugins/", "\\plugins\\",
                      "extensions/", "\\extensions\\"]
    for sig in market_signals:
        if sig in p:
            if sig == "/plugins/" or sig == "\\plugins\\":
                idx = p.find(sig)
                after = p[idx + len(sig):]
                if after and ("/skills" in after or "\\skills" in after):
                    return "marketplace"
            else:
                return "marketplace"

    # Default: user-created
    return "user"


# ── Content hash tracking ──

CONTENT_HASH_FILE = STATE_DIR / "content-hashes.json"
_hash_lock = threading.Lock()


def _load_content_hashes():
    """Load content hashes from state file."""
    if CONTENT_HASH_FILE.exists():
        try:
            return json.loads(CONTENT_HASH_FILE.read_text("utf-8"))
        except Exception:
            return {}
    return {}


def _save_content_hashes(data):
    """Atomically save content hashes."""
    CONTENT_HASH_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = CONTENT_HASH_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(CONTENT_HASH_FILE)


def record_content_hash(skill_path):
    """Compute SHA256 of SKILL.md and store it. Called during install/copy."""
    skill_md = Path(skill_path) / "SKILL.md"
    if not skill_md.exists():
        return
    try:
        content = skill_md.read_text("utf-8", errors="ignore")
        h = hashlib.sha256(content.encode("utf-8")).hexdigest()
        name = Path(skill_path).name
        with _hash_lock:
            hashes = _load_content_hashes()
            hashes[name] = {"hash": h, "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%S")}
            _save_content_hashes(hashes)
    except Exception:
        pass


def check_content_changes(target_path):
    """Compare current SKILL.md hashes with stored hashes.
    Returns dict with changed/deleted lists.
    """
    target_dir = Path(target_path)
    if not target_dir.is_dir():
        return {"changed": [], "deleted": [], "total_tracked": 0, "total_changed": 0}

    with _hash_lock:
        stored = _load_content_hashes()

    if not stored:
        return {"changed": [], "deleted": [], "total_tracked": 0, "total_changed": 0}

    changed = []
    deleted = []
    tracked = 0

    for name, info in stored.items():
        skill_md = target_dir / name / "SKILL.md"
        if not skill_md.exists():
            deleted.append(name)
            continue
        try:
            current = hashlib.sha256(
                skill_md.read_text("utf-8", errors="ignore").encode("utf-8")
            ).hexdigest()
            tracked += 1
            if current != info.get("hash"):
                changed.append({"name": name, "last_recorded": info.get("recorded_at", "")})
        except Exception:
            tracked += 1

    return {
        "changed": changed,
        "deleted": deleted,
        "total_tracked": tracked,
        "total_changed": len(changed),
    }


def _discover_skill_dirs():
    """Discover all skill directories on the system.
    Returns a list of Path objects pointing to directories that contain SKILL.md entries.

    Discovery strategy (in order):
    1. ~/.xxx/skills/ — any dot-prefixed agent directory with a skills/ subdir
    2. ~/first-level/skills/ — non-hidden directories with a skills/ subdir
    3. ~/projects/*//skills/ — project-level skill directories
    4. .skill-dashboard.json config files (home-level + project-level)
    5. custom-sources.json (user-defined paths)
    """
    home = Path.home()
    candidates = []
    seen_paths = set()

    def add_dir(d):
        d = d.resolve()
        if d.is_dir() and str(d) not in seen_paths:
            seen_paths.add(str(d))
            candidates.append(d)

    # 1. ~/.xxx/ — any dot-prefixed agent directory
    #    Only skip genuine system junk. Everything else: let SKILL.md validation decide.
    _SKIP_DEEP = {".git", ".Trash", "node_modules", "__pycache__", "venv", ".venv",
                  "env", "dist", "build", "logs", ".cache", ".npm", "Library",
                  ".snapshots", ".tmp", ".temp"}
    # Shallow skip: only dirs that are DEFINITELY not agents (system caches, build tools)
    _SHALLOW_SKIP = {".Trash", ".cache", ".git"}

    def _has_skill_md(d):
        """Check if directory contains at least one */SKILL.md entry."""
        try:
            return any((c.is_dir() or c.is_symlink()) and (c / "SKILL.md").exists() for c in d.iterdir())
        except Exception:
            return False

    def _scan_agent_deep(root, max_depth=7, _depth=0):
        """Deep scan within agent dirs for marketplaces/backups/extensions/plugins.

        depth=7 covers deeply nested structures like:
        .vscode/agent-plugins/github.com/org/repo/plugins/name/skills/
        .antigravity/extensions/ms-python.../.github/skills/

        Stops recursing into skill directories (dirs containing SKILL.md)
        to avoid picking up nested agent copies inside skill packages (e.g., gstack).
        """
        if _depth >= max_depth:
            return
        try:
            for entry in root.iterdir():
                if not entry.is_dir() or entry.name in _SKIP_DEEP:
                    continue
                if _has_skill_md(entry):
                    add_dir(entry)
                    # Don't recurse INTO skill directories — their sub-dirs are
                    # internal structure (e.g., gstack/.cursor/skills/), not independent dirs
                    continue
                _scan_agent_deep(entry, max_depth, _depth + 1)
        except (PermissionError, OSError):
            pass

    try:
        for entry in home.iterdir():
            if not entry.is_dir():
                continue
            name = entry.name
            if name in _SHALLOW_SKIP or (name.startswith(".") and name.startswith("..")):
                continue
            if name.startswith("."):
                skills_dir = entry / "skills"
                has_skills = skills_dir.is_dir()
                if has_skills:
                    # Standard: skills/ + its subdirs
                    add_dir(skills_dir)
                    try:
                        for sub in skills_dir.iterdir():
                            if sub.is_dir():
                                add_dir(sub)
                    except (PermissionError, OSError):
                        pass
                # Deep scan for ALL .xxx dirs (not just confirmed agents)
                # This catches: .config/opencode/skills/, .antigravity/extensions/,
                # .alice/backups/, .openclaw/workspace/, etc.
                _scan_agent_deep(entry, max_depth=7)
    except (PermissionError, OSError):
        pass

    # 2. ~/first-level/ — non-hidden directories
    #    Checks: skills/ subdir + dirs that directly contain SKILL.md entries
    try:
        for entry in home.iterdir():
            if not entry.is_dir() or entry.name.startswith("."):
                continue
            name = entry.name
            skills_dir = entry / "skills"
            if skills_dir.is_dir():
                add_dir(skills_dir)
            # Also check if the dir itself is a skills collection (e.g., ~/AI-Skills/)
            if name not in ("Downloads", "Documents", "Desktop", "Movies", "Music", "Pictures", "Public"):
                if _has_skill_md(entry):
                    add_dir(entry)
    except (PermissionError, OSError):
        pass

    # 2b. ~/Downloads/ — scan subdirs for skill collections (depth 3)
    downloads = home / "Downloads"
    if downloads.is_dir():
        try:
            for d in downloads.iterdir():
                if not d.is_dir():
                    continue
                if _has_skill_md(d):
                    add_dir(d)
                _scan_agent_deep(d, max_depth=2, _depth=1)
        except (PermissionError, OSError):
            pass

    # 3. ~/projects/*//skills/ — project-level skill directories
    for proj_root_name in ("projects", "Projects", "code", "Code", "workspace"):
        proj_root = home / proj_root_name
        if not proj_root.is_dir():
            continue
        try:
            for proj in proj_root.iterdir():
                if not proj.is_dir():
                    continue
                add_dir(proj / "skills")
                # Check any .xxx/skills/ inside the project
                for sub in proj.iterdir():
                    if sub.is_dir() and sub.name.startswith(".") and not sub.name.startswith(".."):
                        add_dir(sub / "skills")
        except (PermissionError, OSError):
            pass

    # 4. .skill-dashboard.json config files
    home_config = home / ".skill-dashboard.json"
    if home_config.exists():
        try:
            cfg = json.loads(home_config.read_text("utf-8"))
            for p in cfg.get("paths", []):
                add_dir(Path(p).expanduser())
        except Exception:
            pass
    for proj_root_name in ("projects", "Projects", "code", "Code", "workspace"):
        proj_root = home / proj_root_name
        if not proj_root.is_dir():
            continue
        try:
            for proj in proj_root.iterdir():
                if not proj.is_dir():
                    continue
                cfg_file = proj / ".skill-dashboard.json"
                if cfg_file.exists():
                    try:
                        cfg = json.loads(cfg_file.read_text("utf-8"))
                        for p in cfg.get("paths", []):
                            add_dir(Path(p).expanduser())
                    except Exception:
                        pass
        except (PermissionError, OSError):
            pass

    # 5. Custom sources (legacy)
    try:
        cf = STATE_DIR / "custom-sources.json"
        if cf.exists():
            for p in json.loads(cf.read_text()):
                add_dir(Path(p).expanduser())
    except Exception:
        pass

    # Filter: must contain SKILL.md entries, exclude Trash
    return [d for d in candidates
            if d.is_dir()
            and ".Trash" not in str(d)
            and any(
                (c.is_dir() or c.is_symlink()) and (c / "SKILL.md").exists()
                for c in d.iterdir()
            )]


def _scan_global_categories():
    """Scan all skill dirs, classify unique skills, return distribution.
    Cached for 5 minutes. Uses _discover_skill_dirs for full coverage.
    """
    cache_file = CACHE_DIR / "global-categories.json"
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    # Check cache freshness
    if cache_file.exists():
        try:
            cached = json.loads(cache_file.read_text("utf-8"))
            age = time.time() - cached.get("_ts", 0)
            if age < 300:  # 5 min TTL
                return {k: v for k, v in cached.items() if not k.startswith("_")}
        except Exception:
            pass

    skill_dirs = _discover_skill_dirs()

    seen = {}       # name -> description (first seen wins)

    for tdir in skill_dirs:
        for d in sorted(tdir.iterdir()):
            if not d.is_dir():
                continue
            if not (d / "SKILL.md").exists():
                continue
            name = d.name
            if name not in seen:
                seen[name] = _read_skill_description(d)

    # Classify all unique skills
    cat_dist = {}
    for name, desc in seen.items():
        cat = _classify_skill(name, desc)
        cat_dist[cat] = cat_dist.get(cat, 0) + 1

    result = {
        "unique_skills": len(seen),
        "targets_scanned": len(skill_dirs),
        "category_distribution": cat_dist,
    }
    # Save cache with timestamp
    to_cache = dict(result)
    to_cache["_ts"] = time.time()
    cache_file.write_text(json.dumps(to_cache, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def python_quick_check(target_path):
    """Python-only structure check — no bash, no dashboard.
    Returns: health_score, structure_issues, summary."""
    target_dir = Path(target_path)
    if not target_dir.is_dir():
        return None

    skills = []
    structure_issues = []
    no_desc = 0
    broken = 0
    symlinks = 0
    entities = 0

    for d in sorted(target_dir.iterdir()):
        if not d.is_dir() and not d.is_symlink():
            continue
        skill_md = d / "SKILL.md"
        name = d.name

        # Kind detection
        if d.is_symlink():
            if d.resolve().exists():
                kind = "symlink"
                symlinks += 1
            else:
                kind = "broken_symlink"
                broken += 1
                structure_issues.append({"name": name, "note": "broken symlink", "kind": "broken_symlink"})
        else:
            if not skill_md.exists():
                continue
            kind = "entity"
            entities += 1

        # Parse frontmatter
        description = ""
        has_fm = False
        oversized = False
        try:
            text = skill_md.read_text("utf-8", errors="ignore")
            if len(text.splitlines()) > 500:
                oversized = True
            if text.startswith("---"):
                has_fm = True
                end = text.find("---", 3)
                if end > 0:
                    fm = text[3:end]
                    for line in fm.splitlines():
                        line = line.strip()
                        if line.startswith("description:"):
                            description = line.split(":", 1)[1].strip().strip("'\"")
            else:
                structure_issues.append({"name": name, "note": "missing frontmatter", "kind": "no_frontmatter"})
        except Exception:
            structure_issues.append({"name": name, "note": "read error", "kind": "read_error"})

        if not description:
            no_desc += 1

        skills.append({
            "name": name,
            "description": description,
            "kind": kind,
            "has_frontmatter": has_fm,
            "oversized": oversized,
        })

    total = len(skills)

    # ── Independent upstream detection (no dashboard) ──
    upstream_sources = []
    for s in skills:
        skill_dir = target_dir / s["name"]
        repo = ""
        detected = False

        # 1) Try .git remote
        git_dir = skill_dir / ".git"
        if git_dir.exists():
            try:
                r = subprocess.run(
                    ["git", "-C", str(skill_dir), "remote", "get-url", "origin"],
                    capture_output=True, text=True, timeout=5,
                )
                if r.returncode == 0:
                    url = r.stdout.strip()
                    if "github.com" in url:
                        if url.startswith("git@github.com:"):
                            repo = url.replace("git@github.com:", "").replace(".git", "")
                        elif "github.com/" in url:
                            parts = url.split("github.com/")
                            if len(parts) > 1:
                                repo = parts[1].replace(".git", "")
                    upstream_sources.append({
                        "name": s["name"],
                        "repo": repo or url,
                        "status": "unknown",
                        "source": "git-remote",
                    })
                    detected = True
            except Exception:
                pass

        # 2) Fallback: dashboard source metadata (steal installs)
        if not detected:
            meta_file = skill_dir / ".skill-source.env"
            if not meta_file.exists():
                meta_file = skill_dir / ".skill-manager-source.env"
            if meta_file.exists():
                try:
                    for line in meta_file.read_text().splitlines():
                        if line.startswith("SKILL_SOURCE_URL="):
                            url = line.split("=", 1)[1].strip().strip('"').strip("'")
                            repo = ""
                            if "github.com" in url:
                                # Normalize github.com URLs to user/repo
                                clean = url.replace("https://", "").replace("http://", "").replace("github.com/", "")
                                repo = clean.split("/")[0] + "/" + clean.split("/")[1].split("?")[0].split("#")[0] if "/" in clean else clean
                            upstream_sources.append({
                                "name": s["name"],
                                "repo": repo or url,
                                "status": "unknown",
                                "source": "steal-meta",
                            })
                            detected = True
                            break
                except Exception:
                    pass

    # ── Cleanup candidates (independent rules) ──
    cleanup_candidates = []
    for s in skills:
        if s["kind"] == "broken_symlink":
            cleanup_candidates.append(s["name"])
        elif not s["has_frontmatter"]:
            cleanup_candidates.append(s["name"])
        elif not s["description"]:
            cleanup_candidates.append(s["name"])
        elif s.get("oversized"):
            cleanup_candidates.append(s["name"])

    # Health score (mirrors dashboard check.sh formula)
    score = 100
    # Quantity penalty: >20, -2 per extra (max -60)
    if total > 20:
        penalty = min((total - 20) * 2, 60)
        score -= penalty
    # Structure issue penalty: -3 each
    score -= len(structure_issues) * 3
    # Missing description: proportional, max -15
    if total > 0:
        desc_penalty = min(no_desc * 15 // total, 15)
        score -= desc_penalty
    # Oversized: -2 each
    oversized_count = sum(1 for s in skills if s.get("oversized"))
    score -= oversized_count * 2
    # Clamp
    score = max(0, min(100, score))

    # Accuracy estimate (mirrors dashboard)
    if total <= 5:
        accuracy = 96
    elif total <= 20:
        accuracy = 96 - (total - 5)
    else:
        accuracy = max(15, int(96 * (2.71828 ** (-0.005 * (total - 5) ** 1.3))))

    # Level
    if score >= 80:
        level = "green"
    elif score >= 50:
        level = "yellow"
    else:
        level = "red"

    # TF-IDF similarity (with graceful fallback)
    try:
        overlap_groups = compute_tfidf_similarity(target_path, skills)
    except Exception:
        overlap_groups = []

    # Content change detection
    content_changes = check_content_changes(target_path)

    return {
        "health_score": {
            "score": score,
            "level": level,
            "accuracy_estimate": accuracy,
        },
        "structure_issues": structure_issues,
        "overlap_groups": overlap_groups,
        "upstream_sources": upstream_sources,
        "cleanup_candidates": list(dict.fromkeys(cleanup_candidates)),  # dedup, preserve order
        "content_changes": content_changes,
        "summary": {
            "total": total,
            "entities": entities,
            "symlinks": symlinks,
            "broken_symlinks": broken,
            "no_description": no_desc,
            "structure_issues": len(structure_issues),
            "oversized": oversized_count,
            "runtime_ready": entities - len(structure_issues),
        },
        "source": "python-quick-check",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }


# ── GitHub URL parsing ──
GITHUB_HTTPS_RE = re.compile(
    r"^https?://github\.com/(?P<owner>[^/]+)/(?P<repo>[^/]+?)(?:\.git)?"
    r"(?:/tree/(?P<ref>[^/]+)(?:/(?P<subdir>.+))?)?"
    r"/?$"
)
GITHUB_SSH_RE = re.compile(
    r"^git@github\.com:(?P<owner>[^/]+)/(?P<repo>[^/]+?)(?:\.git)?$"
)


def parse_github_url(url):
    """Parse a GitHub URL into (owner, repo, ref, subdir, clean_url).
    Supports https:// and git@ formats. Returns None if not valid.
    """
    url = url.strip()
    m = GITHUB_HTTPS_RE.match(url)
    if m:
        owner = m.group("owner")
        repo = m.group("repo")
        ref = m.group("ref") or "main"
        subdir = m.group("subdir") or ""
        clean = f"https://github.com/{owner}/{repo}"
        if subdir:
            clean += f"/tree/{ref}/{subdir}"
        return owner, repo, ref, subdir, clean
    m = GITHUB_SSH_RE.match(url)
    if m:
        owner = m.group("owner")
        repo = m.group("repo")
        return owner, repo, "main", "", f"https://github.com/{owner}/{repo}"
    return None


# ── Source metadata I/O ──
def write_source_metadata(skill_dir, repo, ref, subdir, url, commit):
    """Write .skill-source.env to record upstream info."""
    meta_file = Path(skill_dir) / ".skill-source.env"
    lines = [
        f"SKILL_SOURCE_PROVIDER=github",
        f"SKILL_SOURCE_REPO={repo}",
        f"SKILL_SOURCE_REF={ref}",
        f"SKILL_SOURCE_SUBDIR={subdir}",
        f"SKILL_SOURCE_URL={url}",
        f"SKILL_SOURCE_INSTALLED_COMMIT={commit}",
    ]
    meta_file.write_text("\n".join(lines) + "\n", encoding="utf-8")


def read_source_metadata(skill_dir):
    """Read .skill-source.env. Returns dict or None.
    Supports short keys (repo=, ref=) and long keys (SKILL_SOURCE_REPO=).
    """
    meta_file = Path(skill_dir) / ".skill-source.env"
    if not meta_file.exists():
        # Backward compat: read old filename
        meta_file = Path(skill_dir) / ".skill-manager-source.env"
    if not meta_file.exists():
        return None
    result = {}
    for line in meta_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            result[k] = v.strip('"').strip("'")
    # Normalize: support both short keys and long keys
    normalized = {}
    key_map = {
        "SKILL_SOURCE_REPO": "repo",
        "SKILL_SOURCE_REF": "ref",
        "SKILL_SOURCE_SUBDIR": "subdir",
        "SKILL_SOURCE_URL": "source_url",
        "SKILL_SOURCE_INSTALLED_COMMIT": "installed_commit",
        "SKILL_SOURCE_PROVIDER": "provider",
    }
    for long_key, short_key in key_map.items():
        if long_key in result:
            normalized[short_key] = result[long_key]
        elif short_key in result:
            normalized[short_key] = result[short_key]
    # Also expose long keys for convenience
    for long_key, short_key in key_map.items():
        if short_key in normalized:
            result[long_key] = normalized[short_key]
    return result


# ── Snapshot ──
def create_snapshot(skill_dir):
    """Create a timestamped backup of a skill directory."""
    skill_dir = Path(skill_dir)
    if not skill_dir.exists():
        return None
    snap_dir = skill_dir.parent / ".snapshots"
    snap_dir.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%d_%H%M%S")
    snap_path = snap_dir / f"{skill_dir.name}_{ts}"
    shutil.copytree(skill_dir, snap_path)
    return str(snap_path)


# ── Install skill from GitHub (pure Python) ──
def install_skill(source_url, target_path, preferred_name=None):
    """Install a skill from a GitHub URL. Pure Python, no dashboard.

    Steps:
      1. Parse GitHub URL (owner/repo/ref/subdir)
      2. git clone --depth 1 to temp dir
      3. Find SKILL.md (handle subdirectories)
      4. If target exists, create snapshot
      5. shutil.copytree to target
      6. Write .skill-source.env

    Returns: {"ok": bool, "name": str, "output": str, "error": str, "snapshot": str}
    """

    parsed = parse_github_url(source_url)
    if not parsed:
        return {"ok": False, "error": f"不是有效的 GitHub URL: {source_url}"}
    owner, repo, ref, subdir, clean_url = parsed

    # Check git availability
    git_check = subprocess.run(["git", "--version"], capture_output=True, text=True)
    if git_check.returncode != 0:
        return {"ok": False, "error": "当前环境缺少 git，无法从 GitHub 安装"}

    tmp_root = tempfile.mkdtemp(prefix="skill_install_")
    clone_dir = Path(tmp_root) / "repo"

    try:
        # Clone
        clone_url = f"https://github.com/{owner}/{repo}.git"
        branch_args = ["--branch", ref] if ref else []
        clone_cmd = ["git", "clone", "--depth", "1"] + branch_args + [clone_url, str(clone_dir)]
        clone_res = subprocess.run(clone_cmd, capture_output=True, text=True, timeout=60)
        if clone_res.returncode != 0:
            shutil.rmtree(tmp_root, ignore_errors=True)
            return {"ok": False, "error": f"git clone 失败: {clone_res.stderr[-300:] or clone_res.stdout[-300:]}"}

        # Find SKILL.md
        search_dir = clone_dir / subdir if subdir else clone_dir
        candidates = []
        if subdir and (search_dir / "SKILL.md").exists():
            candidates = [search_dir]
        else:
            for d in sorted(clone_dir.rglob("SKILL.md")):
                candidates.append(d.parent)

        if not candidates:
            shutil.rmtree(tmp_root, ignore_errors=True)
            return {"ok": False, "error": "仓库里没有找到 SKILL.md"}

        # Select skill directory
        if len(candidates) == 1:
            selected_dir = candidates[0]
        else:
            # Multiple skills — try preferred_name match
            if preferred_name:
                for c in candidates:
                    if c.name == preferred_name:
                        selected_dir = c
                        break
                else:
                    names = ", ".join(c.name for c in candidates[:5])
                    shutil.rmtree(tmp_root, ignore_errors=True)
                    return {"ok": False, "error": f"仓库里有多个 skill，请指定名称。找到: {names}"}
            else:
                names = ", ".join(c.name for c in candidates[:5])
                shutil.rmtree(tmp_root, ignore_errors=True)
                return {"ok": False, "error": f"仓库里有多个 skill，请指定名称。找到: {names}"}

        selected_name = preferred_name or selected_dir.name
        selected_rel = str(selected_dir.relative_to(clone_dir)) if selected_dir != clone_dir else ""

        # Get installed commit
        commit_res = subprocess.run(
            ["git", "-C", str(clone_dir), "log", "-1", "--format=%H", "--", selected_rel],
            capture_output=True, text=True, timeout=10,
        )
        installed_commit = commit_res.stdout.strip() or ""
        if not installed_commit:
            commit_res = subprocess.run(
                ["git", "-C", str(clone_dir), "rev-parse", "HEAD"],
                capture_output=True, text=True, timeout=10,
            )
            installed_commit = commit_res.stdout.strip()

        target = Path(target_path)
        dest_dir = target / selected_name

        # Snapshot if exists
        snapshot_path = None
        if dest_dir.exists() or dest_dir.is_symlink():
            snapshot_path = create_snapshot(dest_dir)

        # Copy
        if dest_dir.exists() or dest_dir.is_symlink():
            if dest_dir.is_symlink():
                dest_dir.unlink()
            elif dest_dir.is_dir():
                shutil.rmtree(dest_dir)
            else:
                dest_dir.unlink()
        shutil.copytree(selected_dir, dest_dir)

        # Record content hash for change detection
        record_content_hash(dest_dir)

        # Write metadata
        write_source_metadata(dest_dir, f"{owner}/{repo}", ref, selected_rel, clean_url, installed_commit)

        output = f"安装到 {target_path}/{selected_name}\n来源: {owner}/{repo}@{ref}"
        if selected_rel:
            output += f"\n子目录: {selected_rel}"
        output += f"\n提交: {installed_commit[:7]}"
        if snapshot_path:
            output += f"\n快照: {snapshot_path}"

        shutil.rmtree(tmp_root, ignore_errors=True)
        return {
            "ok": True,
            "name": selected_name,
            "output": output,
            "snapshot": snapshot_path,
        }

    except Exception as e:
        shutil.rmtree(tmp_root, ignore_errors=True)
        return {"ok": False, "error": str(e)}


# ── Check upstream status (pure Python, no gh CLI) ──
def check_upstream_status(skill_dir):
    """Check if a skill is behind its upstream GitHub source.
    Returns: {"status": "current"|"outdated"|"unknown", "installed_commit": str, "latest_commit": str, "repo": str, "ahead_by": int, "error": str}
    """
    meta = read_source_metadata(skill_dir)
    if not meta:
        # Try .git remote
        git_dir = Path(skill_dir) / ".git"
        if git_dir.exists():
            try:
                r = subprocess.run(
                    ["git", "-C", str(skill_dir), "remote", "get-url", "origin"],
                    capture_output=True, text=True, timeout=5,
                )
                if r.returncode == 0:
                    url = r.stdout.strip()
                    parsed = parse_github_url(url)
                    if parsed:
                        owner, repo, ref, subdir, clean_url = parsed
                        # Get local HEAD
                        lr = subprocess.run(
                            ["git", "-C", str(skill_dir), "rev-parse", "HEAD"],
                            capture_output=True, text=True, timeout=5,
                        )
                        local_commit = lr.stdout.strip() if lr.returncode == 0 else ""
                        # Query GitHub API for latest
                        latest = _github_latest_commit(f"{owner}/{repo}", ref, subdir)
                        if latest:
                            if local_commit and latest == local_commit:
                                return {"status": "current", "installed_commit": local_commit, "latest_commit": latest, "repo": f"{owner}/{repo}", "ahead_by": 0}
                            else:
                                return {"status": "outdated", "installed_commit": local_commit, "latest_commit": latest, "repo": f"{owner}/{repo}", "ahead_by": None}
                        return {"status": "unknown", "installed_commit": local_commit, "latest_commit": "", "repo": f"{owner}/{repo}", "error": "无法查询 GitHub API"}
            except Exception:
                pass
        return {"status": "unknown", "error": "没有来源记录"}

    repo = meta.get("SKILL_SOURCE_REPO", "")
    ref = meta.get("SKILL_SOURCE_REF", "main")
    subdir = meta.get("SKILL_SOURCE_SUBDIR", "")
    installed_commit = meta.get("SKILL_SOURCE_INSTALLED_COMMIT", "")
    url = meta.get("SKILL_SOURCE_URL", "")

    if not repo:
        return {"status": "unknown", "error": "来源记录不完整"}

    latest = _github_latest_commit(repo, ref, subdir)
    if not latest:
        return {"status": "unknown", "installed_commit": installed_commit, "latest_commit": "", "repo": repo, "error": "GitHub API 查询失败"}

    if installed_commit and latest == installed_commit:
        return {"status": "current", "installed_commit": installed_commit, "latest_commit": latest, "repo": repo, "ahead_by": 0}
    else:
        # Try to get ahead_by via compare API
        ahead_by = _github_compare_ahead_by(repo, installed_commit, latest)
        return {"status": "outdated", "installed_commit": installed_commit, "latest_commit": latest, "repo": repo, "ahead_by": ahead_by}


# ── GitHub API helpers with rate-limit protection ──
_github_cache = {}  # (url,) -> (timestamp, result)
_github_cache_ttl = 300  # 5 minutes
_github_rate_limited = False  # global flag: stop querying after hitting rate limit


def _github_api_get(url):
    """Fetch GitHub API with TTL cache and rate-limit detection.
    Returns (data, rate_limited_bool).
    """
    global _github_rate_limited

    # Check cache
    now = time.time()
    cached = _github_cache.get(url)
    if cached and (now - cached[0]) < _github_cache_ttl:
        return cached[1], False

    # If we already hit rate limit this session, skip
    if _github_rate_limited:
        return None, True

    try:
        req = urllib.request.Request(url, headers={"User-Agent": "skill-dashboard"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw = resp.read().decode("utf-8")
            # Check remaining rate limit from response headers
            remaining = resp.headers.get("X-RateLimit-Remaining", "")
            if remaining == "0":
                _github_rate_limited = True
            data = json.loads(raw)
            _github_cache[url] = (now, data)
            return data, False
    except urllib.error.HTTPError as e:
        if e.code == 403 or e.code == 429:
            _github_rate_limited = True
        return None, True
    except Exception:
        return None, False


def _github_latest_commit(repo, ref="main", subdir=""):
    """Query GitHub API for latest commit on a ref/path. Cached + rate-limit protected."""
    if subdir:
        url = f"https://api.github.com/repos/{repo}/commits?path={urllib.parse.quote(subdir)}&sha={urllib.parse.quote(ref)}&per_page=1"
    else:
        url = f"https://api.github.com/repos/{repo}/commits/{urllib.parse.quote(ref)}"
    data, limited = _github_api_get(url)
    if limited or data is None:
        return ""
    if isinstance(data, list):
        return data[0].get("sha", "") if data else ""
    return data.get("sha", "")


def _github_compare_ahead_by(repo, base, head):
    """Query GitHub compare API for commits ahead. Cached + rate-limit protected."""
    url = f"https://api.github.com/repos/{repo}/compare/{base}...{head}"
    data, limited = _github_api_get(url)
    if limited or data is None:
        return None
    return data.get("ahead_by")


# ── Update skill from upstream ──
def update_skill(skill_name, target_path):
    """Update a skill by re-installing from its tracked upstream source.
    Returns: {"ok": bool, "name": str, "output": str, "error": str}
    """
    skill_dir = Path(target_path) / skill_name
    meta = read_source_metadata(skill_dir)
    if meta:
        url = meta.get("SKILL_SOURCE_URL", "")
        if url:
            return install_skill(url, target_path, preferred_name=skill_name)

    # Fallback: try .git remote
    git_dir = skill_dir / ".git"
    if git_dir.exists():
        try:
            r = subprocess.run(
                ["git", "-C", str(skill_dir), "remote", "get-url", "origin"],
                capture_output=True, text=True, timeout=5,
            )
            if r.returncode == 0:
                url = r.stdout.strip()
                parsed = parse_github_url(url)
                if parsed:
                    return install_skill(url, target_path, preferred_name=skill_name)
        except Exception:
            pass

    return {"ok": False, "error": "没有找到上游来源记录，无法更新"}


# ── Diagnosis state (module-level, protected by lock) ──
_diag_lock = threading.Lock()
_diag_process = None
_diag_target = ""
_diag_start = 0
_diag_phase = ""


class DashboardHandler(BaseHTTPRequestHandler):
    """Serve index.html and API endpoints."""

    def _read_json(self):
        """Read and parse JSON body from request. Returns dict or None."""
        try:
            length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(length).decode('utf-8') if length else '{}'
            return json.loads(body)
        except Exception:
            return None

    @staticmethod
    def _validate_skill_name(name):
        """Sanitize skill name from URL. Rejects path traversal attempts."""
        if not name or '..' in name or '/' in name or '\\' in name:
            return None
        if name.startswith('.') or name.startswith('-'):
            return None
        # Allow letters, digits, hyphens, underscores, dots, @, +
        if not re.match(r'^[a-zA-Z0-9._@+\-]+$', name):
            return None
        return name

    def _check_csrf(self):
        """Reject cross-origin write requests. Returns True if safe."""
        origin = self.headers.get("Origin", "")
        referer = self.headers.get("Referer", "")
        # Allow requests with no Origin/Referer (curl, CLI tools, direct browser nav)
        if not origin and not referer:
            return True
        # Check Origin first (preferred)
        if origin:
            allowed = [f"http://127.0.0.1:{PORT}", f"http://localhost:{PORT}"]
            return origin in allowed
        # Fallback to Referer
        if referer:
            parsed = urlparse(referer)
            return parsed.hostname in ("127.0.0.1", "localhost") and parsed.port == PORT
        return True

    def _csrf_reject(self):
        """Send a 403 CSRF rejection response."""
        self._json_response({"error": "CSRF check failed — cross-origin request rejected"}, status=403)

    def do_GET(self):
        path = urlparse(self.path).path

        if path == "/" or path == "/index.html":
            self._serve_file(HTML_FILE, "text/html; charset=utf-8")
        elif path == "/api/scan":
            self._serve_json(STATE_DIR / "latest-scan.json")
        elif path == "/api/health":
            self._serve_json(STATE_DIR / "latest-health.json")
        elif path == "/api/history":
            self._serve_history()
        elif path == "/api/targets":
            self._list_targets()
        elif path == "/api/category-order":
            f = STATE_DIR / "category-order.json"
            data = f.read_text(encoding="utf-8") if f.exists() else "[]"
            self._json_response(json.loads(data))
        elif path == "/api/fast-scan":
            self._fast_scan()
        elif path == "/api/quick-check":
            self._quick_check()
        elif path == "/api/diagnosis-status":
            self._diagnosis_status()
        elif path == "/api/export":
            self._export_skills()
        elif path == "/api/openapi":
            self._openapi()
        elif path == "/api/source/skills":
            self._list_source_skills()
        elif path == "/api/custom-sources":
            self._get_custom_sources()
        elif path == "/api/global-stats":
            self._json_response(_scan_global_categories())
        elif path == "/api/global-overlap":
            self._json_response(detect_cross_dir_overlaps())
        elif path == "/api/scan-result":
            self._serve_json(CACHE_DIR / "scan-result.json")
        elif path == "/api/favorite-dirs":
            self._get_favorite_dirs()
        elif path == "/api/trash":
            self._list_trash()
        elif path.startswith("/api/trash/") and path.endswith("/restore"):
            self._restore_trash(path)
        elif path.startswith("/api/skill/") and path.endswith("/content"):
            name = self._validate_skill_name(path.split("/")[3])
            if not name:
                self.send_error(400, "Invalid skill name")
            else:
                self._serve_skill_content(name)
        elif path == "/api/preview":
            # Preview skill from any directory: /api/preview?dir=...&name=...
            from urllib.parse import parse_qs
            qs = parse_qs(urlparse(self.path).query)
            preview_dir = qs.get("dir", [""])[0]
            preview_name = qs.get("name", [""])[0]
            if preview_dir and preview_name:
                self._serve_preview(preview_dir, preview_name)
            else:
                self.send_error(400, "Missing dir or name")
        elif path.startswith("/api/skill/") and path.endswith("/upstream"):
            name = self._validate_skill_name(path.split("/")[3])
            if not name:
                self.send_error(400, "Invalid skill name")
            else:
                self._check_skill_upstream(name)
        else:
            self.send_error(404)

    def do_POST(self):
        if not self._check_csrf():
            self._csrf_reject()
            return
        path = urlparse(self.path).path
        if path == "/api/target":
            self._set_target()
        elif path == "/api/diagnose":
            self._diagnose()
        elif path == "/api/scan-run":
            self._run_scan()
        elif path == "/api/steal":
            self._steal_skill()
        elif path == "/api/copy-skill":
            self._copy_skill()
        elif path == "/api/custom-sources":
            self._add_custom_source()
        elif path == "/api/favorite-dirs":
            self._save_favorite_dirs()
        elif path == "/api/category-order":
            try:
                length = int(self.headers.get('Content-Length', 0))
                raw = self.rfile.read(length).decode('utf-8') if length else '[]'
                data = json.loads(raw)
                if isinstance(data, list):
                    (STATE_DIR / "category-order.json").write_text(
                        json.dumps(data, ensure_ascii=False), encoding="utf-8"
                    )
                    self._json_response({"ok": True})
                else:
                    self.send_error(400, "Expected JSON array")
            except Exception as e:
                self._json_response({"error": str(e)}, 400)
        elif path.startswith("/api/trash/") and path.endswith("/restore"):
            self._restore_trash(path)
        elif path.startswith("/api/skill/") and path.endswith("/rehash"):
            name = self._validate_skill_name(path.split("/")[3])
            if not name:
                self.send_error(400, "Invalid skill name")
            else:
                self._rehash_skill(name)
        else:
            self.send_error(404)

    def do_DELETE(self):
        if not self._check_csrf():
            self._csrf_reject()
            return
        path = urlparse(self.path).path
        query = parse_qs(urlparse(self.path).query)
        if path.startswith("/api/skill/"):
            name = self._validate_skill_name(path.split("/")[3])
            if not name:
                self.send_error(400, "Invalid skill name")
            else:
                target = query.get("target", [""])[0]
                self._delete_skill(name, target or None)
        elif path == "/api/custom-sources":
            self._remove_custom_source()
        elif path.startswith("/api/trash/"):
            # Permanent delete: DELETE /api/trash/{trash_dir_name}
            self._delete_trash(path)
        else:
            self.send_error(404)

    # ── API implementations ──

    def _serve_file(self, filepath, content_type):
        try:
            data = filepath.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            self.send_header("Pragma", "no-cache")
            self.send_header("Expires", "0")
            self.end_headers()
            self.wfile.write(data)
        except FileNotFoundError:
            self.send_error(404, f"File not found: {filepath}")

    def _serve_json(self, filepath):
        try:
            data = filepath.read_text(encoding="utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(data.encode("utf-8"))
        except FileNotFoundError:
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"error": "state file not found, switch target to generate data"}')

    def _serve_history(self):
        hist_file = STATE_DIR / "history.jsonl"
        try:
            lines = hist_file.read_text(encoding="utf-8").strip().split("\n")
            entries = []
            for line in lines[-50:]:
                line = line.strip()
                if line:
                    try:
                        entries.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
            self._json_response(entries)
        except FileNotFoundError:
            self._json_response([])

    def _serve_skill_content(self, name):
        """Return SKILL.md content for a named skill."""
        target = self._current_target()
        candidates = [Path(target) / name / "SKILL.md"]
        for skill_md in candidates:
            if skill_md.exists():
                content = skill_md.read_text(encoding="utf-8")
                self._json_response({"name": name, "content": content, "path": str(skill_md)})
                return
        self._json_response({"error": f"Skill '{name}' not found"}, status=404)

    def _serve_preview(self, dir_path, name):
        """Preview SKILL.md from any directory (no target switch needed).
        Query param ?full=1 returns full content instead of 500-char preview."""
        resolved = Path(dir_path).resolve()
        if not resolved.is_relative_to(Path.home()):
            self._json_response({"error": "dir must be under home directory"}, status=403)
            return
        skill_md = resolved / name / "SKILL.md"
        if not skill_md.exists():
            self._json_response({"error": "not found"}, status=404)
            return
        try:
            content = skill_md.read_text(encoding="utf-8", errors="ignore")
            # Extract description from frontmatter
            desc = ""
            if content.startswith("---"):
                end = content.find("---", 3)
                if end > 0:
                    fm = content[3:end]
                    for line in fm.split("\n"):
                        if line.strip().startswith("description:"):
                            desc = line.split(":", 1)[1].strip().strip("'\"")
                            break
            # Body (skip frontmatter)
            body = content
            if content.startswith("---"):
                end = content.find("---", 3)
                if end > 0:
                    body = content[end + 3:].strip()
            qs = parse_qs(urlparse(self.path).query)
            if qs.get("full", [""])[0] == "1":
                preview = body
            else:
                preview = body[:500] + ("…" if len(body) > 500 else "")
            self._json_response({
                "name": name,
                "dir": dir_path,
                "agent": _agent_from_path(dir_path),
                "description": desc,
                "preview": preview,
                "size": len(content),
            })
        except Exception as e:
            self._json_response({"error": str(e)}, status=500)

    def _check_skill_upstream(self, name):
        """Check upstream status for a single skill."""
        target = self._current_target()
        skill_dir = Path(target) / name
        if not skill_dir.exists():
            self._json_response({"error": f"Skill '{name}' not found"}, status=404)
            return
        result = check_upstream_status(skill_dir)
        result["name"] = name
        self._json_response(result)

    def _export_skills(self):
        """Export current target's skills as JSON."""
        target = self._current_target()
        target_dir = Path(target)
        result = []
        if target_dir.is_dir():
            for d in sorted(target_dir.iterdir()):
                if not d.is_dir():
                    continue
                skill_md = d / "SKILL.md"
                if not skill_md.exists():
                    continue
                name = d.name
                description = ""
                category = ""
                try:
                    text = skill_md.read_text("utf-8", errors="ignore")[:2000]
                    if text.startswith("---"):
                        end = text.find("---", 3)
                        if end > 0:
                            fm = text[3:end]
                            for line in fm.splitlines():
                                line = line.strip()
                                if line.startswith("description:"):
                                    description = line.split(":", 1)[1].strip().strip("'\"")
                                elif line.startswith("category:"):
                                    category = line.split(":", 1)[1].strip().strip("'\"")
                except Exception:
                    pass
                result.append({
                    "name": name,
                    "category": category,
                    "description": description,
                })
        self._json_response({
            "exported_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "target": target,
            "skills": result,
        })

    def _openapi(self):
        """Return simple API documentation."""
        self._json_response({
            "title": "Skill Dashboard API",
            "version": "2.0",
            "endpoints": [
                {"method": "GET", "path": "/api/fast-scan", "desc": "Instant skill list + classification"},
                {"method": "GET", "path": "/api/quick-check", "desc": "Health score + structure issues + upstream + cleanup"},
                {"method": "GET", "path": "/api/targets", "desc": "List available skill directories"},
                {"method": "GET", "path": "/api/global-stats", "desc": "Global category distribution across all skill libraries (cached 5min)"},
                {"method": "GET", "path": "/api/export", "desc": "Export skill manifest as JSON"},
                {"method": "GET", "path": "/api/skill/{name}/content", "desc": "Read SKILL.md content"},
                {"method": "GET", "path": "/api/skill/{name}/upstream", "desc": "Check upstream status for a skill"},
                {"method": "POST", "path": "/api/target", "desc": "Switch target directory"},
                {"method": "POST", "path": "/api/diagnose", "desc": "Trigger full diagnosis (Python-only)"},
                {"method": "POST", "path": "/api/scan-run", "desc": "Targeted scan: selected directories + analysis types"},
                {"method": "GET", "path": "/api/scan-result", "desc": "Get cached scan result"},
                {"method": "POST", "path": "/api/steal", "desc": "Install skill from GitHub URL"},
                {"method": "DELETE", "path": "/api/skill/{name}", "desc": "Delete a skill"},
                {"method": "PATCH", "path": "/api/skill/{name}/update", "desc": "Update skill from upstream"},
            ],
        })

    def _list_source_skills(self):
        """Return skills in a given source directory (for穿透 browsing)."""
        query = parse_qs(urlparse(self.path).query)
        source_path = query.get("path", [""])[0]
        if not source_path:
            self._json_response({"error": "missing path param"}, status=400)
            return
        # Normalize path placeholders
        home = str(Path.home())
        source_path = source_path.replace("${HOME}", home).replace("$HOME", home)
        if source_path.startswith("~"):
            source_path = str(Path.home() / source_path[2:])
        source_dir = Path(source_path).resolve()
        if not source_dir.is_relative_to(Path.home()):
            self._json_response({"error": "path must be under home directory"}, status=403)
            return
        if not source_dir.is_dir():
            self._json_response({"error": f"not a dir: {source_path}"}, status=400)
            return

        result = []
        for d in sorted(source_dir.iterdir()):
            if not d.is_dir() and not d.is_symlink():
                continue
            skill_md = d / "SKILL.md"
            if not skill_md.exists():
                continue
            name = d.name
            description = ""
            try:
                text = skill_md.read_text("utf-8", errors="ignore")[:2000]
                if text.startswith("---"):
                    end = text.find("---", 3)
                    if end > 0:
                        fm = text[3:end]
                        for line in fm.splitlines():
                            line = line.strip()
                            if line.startswith("description:"):
                                description = line.split(":", 1)[1].strip().strip("'\"")
            except Exception:
                pass
            result.append({
                "name": name,
                "description": description,
            })
        self._json_response({
            "source": str(source_dir).replace(str(Path.home()), "~"),
            "skills": result,
            "count": len(result),
        })

    def _get_custom_sources(self):
        """Return user-defined custom source paths."""
        self._json_response(self._load_custom_sources())

    def _add_custom_source(self):
        """Add a custom source path."""
        length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(length).decode('utf-8') if length else '{}'
        try:
            data = json.loads(body)
        except Exception:
            data = {}
        new_path = data.get("path", "").strip()
        if not new_path:
            self._json_response({"error": "missing path"}, status=400)
            return
        # Expand ~
        if new_path.startswith("~"):
            new_path = str(Path.home() / new_path[2:])
        p = Path(new_path)
        if not p.exists():
            self._json_response({"error": f"path does not exist: {new_path}"}, status=400)
            return
        # Must have skills/ subdir or be a skills dir itself
        skills_dir = p / "skills" if p.name != "skills" else p
        if not skills_dir.is_dir():
            self._json_response({"error": f"no skills/ subdir found in {new_path}"}, status=400)
            return
        paths = self._load_custom_sources()
        if new_path not in paths:
            paths.append(new_path)
            self._save_custom_sources(paths)
        self._json_response({"ok": True, "path": new_path, "paths": paths})

    def _remove_custom_source(self):
        """Remove a custom source path."""
        query = parse_qs(urlparse(self.path).query)
        rm_path = query.get("path", [""])[0]
        if not rm_path:
            self._json_response({"error": "missing path"}, status=400)
            return
        paths = self._load_custom_sources()
        if rm_path in paths:
            paths.remove(rm_path)
            self._save_custom_sources(paths)
        self._json_response({"ok": True, "paths": paths})

    # ── Favorite directories ──

    def _get_favorite_dirs(self):
        """Return user-pinned favorite directory paths."""
        fav_file = STATE_DIR / "favorite-dirs.json"
        try:
            return self._json_response(json.loads(fav_file.read_text("utf-8")))
        except Exception:
            self._json_response([])

    def _save_favorite_dirs(self):
        """Save user-pinned favorite directory paths."""
        length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(length).decode('utf-8') if length else '[]'
        try:
            dirs = json.loads(body)
        except Exception:
            return self._json_response({"error": "invalid JSON"}, status=400)
        if not isinstance(dirs, list):
            return self._json_response({"error": "expected array"}, status=400)
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        (STATE_DIR / "favorite-dirs.json").write_text(
            json.dumps(dirs, ensure_ascii=False, indent=2), encoding="utf-8")
        self._json_response({"ok": True, "count": len(dirs)})

    # ── Trash ──

    def _list_trash(self):
        """List all trashed skills."""
        trash_dir = STATE_DIR.parent / "trash"
        items = []
        if trash_dir.is_dir():
            for d in sorted(trash_dir.iterdir(), reverse=True):
                if not d.is_dir():
                    continue
                meta_path = d / ".trash-meta.json"
                try:
                    meta = json.loads(meta_path.read_text("utf-8"))
                except Exception:
                    meta = {"name": d.name, "original_path": "", "trashed_at": ""}
                # Count skills inside
                skill_count = sum(1 for c in d.iterdir() if c.is_dir() and (c / "SKILL.md").exists()) if d.is_dir() else 0
                items.append({
                    "id": d.name,
                    "name": meta.get("name", d.name),
                    "original_path": meta.get("original_path", ""),
                    "trashed_at": meta.get("trashed_at", ""),
                    "skill_count": skill_count,
                })
        self._json_response({"items": items, "count": len(items)})

    def _restore_trash(self, path):
        """Restore a trashed skill to its original location (or current target)."""
        trash_id = path.split("/api/trash/")[1].replace("/restore", "")
        if '..' in trash_id or '/' in trash_id or '\\' in trash_id:
            self._json_response({"error": "invalid trash id"}, status=400)
            return
        trash_dir = STATE_DIR.parent / "trash" / trash_id
        if not trash_dir.is_dir():
            self._json_response({"error": "not found"}, status=404)
            return
        # Read metadata for original path
        meta_path = trash_dir / ".trash-meta.json"
        try:
            meta = json.loads(meta_path.read_text("utf-8"))
            original = meta.get("original_path", "")
        except Exception:
            original = ""
        # Determine restore destination
        if original and Path(original).parent.is_dir():
            dest = Path(original)
        else:
            # Fallback: current target
            dest = Path(self._current_target()) / meta.get("name", trash_id.split("_", 2)[-1])
        if dest.exists():
            self._json_response({"error": f"目标已存在: {dest}", "status": "conflict"}, status=409)
            return
        try:
            # Remove meta file before moving
            if meta_path.exists():
                meta_path.unlink()
            shutil.move(str(trash_dir), str(dest))
            self._json_response({"ok": True, "restored_to": str(dest)})
        except Exception as e:
            self._json_response({"error": str(e)}, status=500)

    def _delete_trash(self, path):
        """Permanently delete a trashed skill."""
        trash_id = path.split("/api/trash/")[1]
        if '..' in trash_id or '/' in trash_id or '\\' in trash_id:
            self._json_response({"error": "invalid trash id"}, status=400)
            return
        trash_dir = STATE_DIR.parent / "trash" / trash_id
        if not trash_dir.is_dir():
            self._json_response({"error": "not found"}, status=404)
            return
        try:
            shutil.rmtree(trash_dir)
            self._json_response({"ok": True, "deleted": trash_id})
        except Exception as e:
            self._json_response({"error": str(e)}, status=500)

    def _fast_scan(self):
        """Direct Python directory scan — milliseconds instead of bash subprocess."""
        target = self._current_target()
        target_dir = Path(target)
        if not target_dir.is_dir():
            self._json_response({"error": f"not a dir: {target}"}, status=400)
            return

        start = time.time()
        skills = []
        for d in sorted(target_dir.iterdir()):
            if not d.is_dir():
                continue
            skill_md = d / "SKILL.md"
            if not skill_md.exists():
                continue
            name = d.name
            description = ""
            category = ""
            kind = "entity"
            # Quick frontmatter parse
            try:
                text = skill_md.read_text("utf-8", errors="ignore")[:2000]
                if text.startswith("---"):
                    end = text.find("---", 3)
                    if end > 0:
                        fm = text[3:end]
                        fm_lines = fm.splitlines()
                        for i, line in enumerate(fm_lines):
                            stripped = line.strip()
                            if stripped.startswith("description:"):
                                val = stripped.split(":", 1)[1].strip()
                                # Strip YAML multiline indicators (>, |, >-, |-, >+, |+)
                                if val in (">", "|", ">-", "|-", ">+", "|+"):
                                    # Collect indented continuation lines
                                    parts = []
                                    for cont in fm_lines[i + 1:]:
                                        if cont and not cont[0].isspace():
                                            break
                                        parts.append(cont.strip())
                                    description = " ".join(parts)
                                elif val.startswith('"') and val.endswith('"'):
                                    description = val[1:-1]
                                elif val.startswith("'") and val.endswith("'"):
                                    description = val[1:-1]
                                else:
                                    description = val.strip("'\"")
                            elif stripped.startswith("category:"):
                                category = stripped.split(":", 1)[1].strip().strip("'\"")
            except Exception:
                pass
            # Check if symlink
            if d.is_symlink():
                kind = "symlink" if d.resolve().exists() else "broken_symlink"
            skills.append({
                "name": name,
                "description": description,
                "category": category,
                "kind": kind,
                "agent": "",
            })

        # Build scan-like response
        home = Path.home()
        rel = str(target_dir).replace(str(home), "~")
        result = {
            "target": {
                "path": rel,
                "label": target_dir.parent.name,
                "total": len(skills),
                "entities": len([s for s in skills if s["kind"] == "entity"]),
                "symlinks": len([s for s in skills if s["kind"] == "symlink"]),
                "broken_symlinks": len([s for s in skills if s["kind"] == "broken_symlink"]),
            },
            "installed": skills,
            "totals": {"skills": len(skills)},
            "sources": [],
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "scan_mode": "fast",
            "duration_ms": int((time.time() - start) * 1000),
        }
        self._json_response(result)

    def _quick_check(self):
        """Python-only structure check — instant, no bash."""
        target = self._current_target()
        result = python_quick_check(target)
        if result is None:
            self._json_response({"error": "target not found"}, status=400)
            return
        self._json_response(result)

    # ── Diagnosis (uses module-level globals + lock) ──

    def _run_scan(self):
        """Run full scan across all discovered skill directories."""
        body = self._read_json() or {}

        directories = body.get("directories", [])
        # If no directories specified, scan all discovered dirs
        home = Path.home()
        if not directories:
            skill_dirs = _discover_skill_dirs()
            directories = [str(d) for d in skill_dirs
                          if sum(1 for x in d.iterdir()
                                if (x.is_dir() or x.is_symlink()) and (x / "SKILL.md").exists()) > 0]

        # Always run all check types
        checks = body.get("checks", ["same-name", "similar", "upstream", "content-changes"])

        # Validate directories
        valid_dirs = []
        for d in directories:
            p = Path(d).expanduser().resolve()
            if p.is_dir() and p.is_relative_to(home):
                valid_dirs.append(p)
        if not valid_dirs:
            self._json_response({"error": "没有有效的 skill 目录"}, status=400)
            return

        t0 = time.time()
        result = {
            "upstream_sources": [],
            "overlap_groups": [],
            "duplicates_same_name": [],
            "duplicates_identical": [],
            "agent_similar": {},
            "content_changes": None,
            "scanned_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "scanned_dirs": len(valid_dirs),
        }

        # Per-directory checks
        for tdir in valid_dirs:
            dir_skills = []
            try:
                for d in sorted(tdir.iterdir()):
                    if (d.is_dir() or d.is_symlink()) and (d / "SKILL.md").exists():
                        dir_skills.append({"name": d.name})
            except Exception:
                continue

            if not dir_skills:
                continue

            # Similarity within this directory
            if "similar" in checks and len(dir_skills) > 1:
                groups = compute_tfidf_similarity(
                    str(tdir), dir_skills, agent_name=_agent_from_path(str(tdir))
                )
                if groups:
                    result["overlap_groups"].extend(groups)

            # Upstream tracking
            if "upstream" in checks:
                for s in dir_skills:
                    skill_dir = tdir / s["name"]
                    try:
                        status = check_upstream_status(skill_dir)
                        if status.get("status") in ("current", "outdated"):
                            result["upstream_sources"].append({
                                "name": s["name"],
                                "repo": status.get("repo", ""),
                                "status": status["status"],
                                "installed_commit": status.get("installed_commit", ""),
                                "latest_commit": status.get("latest_commit", ""),
                                "dir": str(tdir),
                            })
                    except Exception:
                        pass

            # Content changes
            if "content-changes" in checks:
                try:
                    changes = check_content_changes(str(tdir))
                    if changes and changes.get("total_changed", 0) > 0:
                        if result["content_changes"] is None:
                            result["content_changes"] = {"changed": [], "deleted": [], "total_tracked": 0, "total_changed": 0}
                        result["content_changes"]["changed"].extend(changes.get("changed", []))
                        result["content_changes"]["deleted"].extend(changes.get("deleted", []))
                        result["content_changes"]["total_tracked"] += changes.get("total_tracked", 0)
                        result["content_changes"]["total_changed"] += changes.get("total_changed", 0)
                except Exception:
                    pass

        # Cross-directory checks (need 2+ dirs)
        if len(valid_dirs) >= 2:
            if "same-name" in checks:
                dup_id, dup_sn = _find_same_name_duplicates(valid_dirs)
                result["duplicates_identical"] = dup_id
                result["duplicates_same_name"] = dup_sn
            if "similar" in checks:
                result["agent_similar"] = _find_agent_cross_dir_similar(valid_dirs)

        result["duration_ms"] = int((time.time() - t0) * 1000)

        # Cache result
        try:
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            (CACHE_DIR / "scan-result.json").write_text(
                json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception:
            pass

        # Lint: verify result consistency
        result["lint"] = self._lint_scan_result(result)

        self._json_response(result)

    def _lint_scan_result(self, result):
        """Check scan result for logical inconsistencies. Returns list of warnings."""
        warnings = []

        # 1. Same-name: every group must have 2+ locations
        sn = result.get("duplicates_same_name", [])
        for dup in sn:
            if len(dup.get("locations", [])) < 2:
                warnings.append(f"same-name group '{dup.get('name','?')}' has {len(dup.get('locations',[]))} locations (need 2+)")

        # 2. Overlap groups: each must have 2+ skills
        ov = result.get("overlap_groups", [])
        for g in ov:
            if len(g.get("skills", [])) < 2:
                warnings.append(f"overlap group has {len(g.get('skills',[]))} skills (need 2+)")

        # 3. Agent-similar: each group must have 2+ skills
        for agent, groups in result.get("agent_similar", {}).items():
            for g in groups:
                if len(g.get("skills", [])) < 2:
                    warnings.append(f"agent_similar[{agent}] group has {len(g.get('skills',[]))} skills (need 2+)")

        # 4. Upstream: each must have name and dir
        for s in result.get("upstream_sources", []):
            if not s.get("name"):
                warnings.append(f"upstream entry missing name: {s}")
            if not s.get("dir"):
                warnings.append(f"upstream entry '{s.get('name','?')}' missing dir")

        # 5. Cross-dir same-name: count groups that span 2+ agents
        cross_agent_count = sum(1 for dup in sn if len(set(l.get("agent", "") for l in dup.get("locations", []))) >= 2)
        within_agent_count = 0
        sn_by_agent = {}
        for dup in sn:
            if len(dup.get("locations", [])) < 2:
                continue
            for loc in dup.get("locations", []):
                a = loc.get("agent", "其他")
                if a not in sn_by_agent:
                    sn_by_agent[a] = set()
                sn_by_agent[a].add(dup["name"])
        for a, names in sn_by_agent.items():
            # Count names where this agent has 2+ locations
            for dup in sn:
                agent_locs = [l for l in dup.get("locations", []) if l.get("agent", "其他") == a]
                if len(agent_locs) >= 2:
                    within_agent_count += 1

        total_shown = cross_agent_count + within_agent_count
        if total_shown != len(sn):
            # Some groups might not be shown anywhere
            pass  # This is expected if some dups only have 1 location per agent

        return {"warnings": warnings, "checks": {
            "same_name_groups": len(sn),
            "cross_agent_groups": cross_agent_count,
            "within_agent_groups": within_agent_count,
            "overlap_groups": len(ov),
            "upstream_sources": len(result.get("upstream_sources", [])),
            "agent_similar_agents": len(result.get("agent_similar", {})),
        }}

    def _diagnose(self):
        """Trigger Python-only diagnosis in background. No dashboard needed."""
        global _diag_process, _diag_target, _diag_start, _diag_phase
        target = self._current_target()

        with _diag_lock:
            # Check if already running
            if _diag_process and _diag_process.poll() is None:
                elapsed = int((time.time() - _diag_start) * 1000)
                if elapsed > 60000:
                    _diag_process.kill()
                    _diag_process = None
                    self._json_response({"status": "error", "error": "诊断超时 (60s)，请重试"})
                    return
                self._json_response({"status": "running", "target": _diag_target,
                                     "elapsed_ms": elapsed, "phase": "check"})
                return

            try:
                CACHE_DIR.mkdir(parents=True, exist_ok=True)
                log_f = open(DIAG_LOG, "w")
                worker_script = Path(__file__).parent / "_diag_worker.py"
                _diag_process = subprocess.Popen(
                    [sys.executable, str(worker_script), target],
                    stdout=log_f, stderr=subprocess.STDOUT,
                )
                _diag_target = target
                _diag_start = time.time()
                _diag_phase = "check"
                self._json_response({"status": "started", "target": target})
            except Exception as e:
                self._json_response({"status": "error", "error": str(e)})

    def _diagnosis_status(self):
        """Poll diagnosis progress. If done, cache and return results."""
        global _diag_process
        target = self._current_target()

        with _diag_lock:
            # If process is running, check if it just finished
            if _diag_process and _diag_process.poll() is not None:
                _diag_process = None
                cached = load_cached_diagnosis(target)
                if cached:
                    cached["status"] = "done"
                    cached["duration_ms"] = int((time.time() - _diag_start) * 1000)
                    self._json_response(cached)
                    return
                else:
                    self._json_response({"status": "error", "error": "诊断完成但缓存未找到"})
                    return

            # Process still running
            if _diag_process and _diag_process.poll() is None:
                elapsed = int((time.time() - _diag_start) * 1000)
                self._json_response({"status": "running", "target": _diag_target,
                                     "elapsed_ms": elapsed, "phase": "check"})
                return

        # No process — check cache
        cached = load_cached_diagnosis(target)
        if cached:
            cached["status"] = "cached"
            self._json_response(cached)
            return

        self._json_response({"status": "idle"})

    def _load_custom_sources(self):
        """Load user-defined custom source paths."""
        try:
            cf = STATE_DIR / "custom-sources.json"
            if cf.exists():
                return json.loads(cf.read_text())
        except Exception:
            pass
        return []

    def _save_custom_sources(self, paths):
        """Save user-defined custom source paths."""
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        cf = STATE_DIR / "custom-sources.json"
        cf.write_text(json.dumps(paths, ensure_ascii=False, indent=2), encoding="utf-8")

    def _list_targets(self):
        """List all discovered skill directories grouped by agent.
        Uses shared _discover_skill_dirs for directory discovery.
        """
        home = Path.home()
        current = self._current_target()

        # Reuse shared discovery
        skill_dirs = _discover_skill_dirs()
        targets = []
        for skills_dir in skill_dirs:
            count = sum(1 for d in skills_dir.iterdir()
                       if (d.is_dir() or d.is_symlink()) and (d / "SKILL.md").exists())
            if count == 0:
                continue
            rel = str(skills_dir).replace(str(home), "~")
            # Use shared agent detection
            agent = _agent_from_path(str(skills_dir))
            scope = "project" if "projects/" in rel else "global"
            category = _classify_skill_dir(skills_dir)
            targets.append({
                "path": str(skills_dir),
                "rel": rel,
                "name": agent,
                "scope": scope,
                "count": count,
                "is_current": str(skills_dir) == current,
                "category": category,
            })
        targets.sort(key=lambda t: (0 if t["is_current"] else 1, -t["count"]))

        # Group by agent name
        grouped = {}
        for t in targets:
            agent = t["name"]
            if agent not in grouped:
                grouped[agent] = {"agent": agent, "dirs": [], "total_skills": 0}
            grouped[agent]["dirs"].append(t)
            grouped[agent]["total_skills"] += t["count"]

        # Sort groups: current target's group first, then by total skills desc
        current_agent = next((t["name"] for t in targets if t["is_current"]), "")
        groups = sorted(grouped.values(),
                        key=lambda g: (0 if g["agent"] == current_agent else 1, -g["total_skills"]))

        # Flat list for backward compat + grouped view
        self._json_response({"targets": targets, "groups": groups})

    def _current_target(self):
        """Read current target from dedicated state file, fallback to latest-scan.json, fallback to ~/.claude/skills."""
        # 1) Dedicated state file (most reliable)
        try:
            ct = json.loads((STATE_DIR / "current-target.json").read_text())
            tp = ct.get("path", "")
            if tp.startswith("~"):
                tp = str(Path.home() / tp[2:])
            if tp and Path(tp).is_dir():
                return tp
        except Exception:
            pass
        # 2) Legacy: latest-scan.json from dashboard
        try:
            scan = json.loads((STATE_DIR / "latest-scan.json").read_text())
            tp = scan.get("target", {}).get("path", "")
            if tp.startswith("~"):
                tp = str(Path.home() / tp[2:])
            return tp
        except Exception:
            pass
        # 3) Fallback
        return str(Path.home() / ".claude/skills")

    def _set_target(self):
        """Switch target — fast scan directly, no bash subprocess."""
        length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(length).decode('utf-8') if length else '{}'
        try:
            data = json.loads(body)
        except Exception:
            data = {}
        target_path = data.get("target", "")
        if not target_path:
            self._json_response({"error": "missing target"}, status=400)
            return
        if target_path.startswith("~"):
            target_path = str(Path.home() / target_path[2:])
        if not Path(target_path).is_dir():
            self._json_response({"error": f"not a directory: {target_path}"}, status=400)
            return

        # Write to dedicated state file so _current_target picks it up
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        home = Path.home()
        rel = str(target_path).replace(str(home), "~")
        ct_file = STATE_DIR / "current-target.json"
        ct_file.write_text(json.dumps({"path": rel, "label": Path(target_path).parent.name}, ensure_ascii=False, indent=2), encoding="utf-8")
        # Also update legacy latest-scan.json for compatibility
        scan_file = STATE_DIR / "latest-scan.json"
        scan_data = {}
        if scan_file.exists():
            try:
                scan_data = json.loads(scan_file.read_text("utf-8"))
            except Exception:
                pass
        scan_data["target"] = {
            "path": rel,
            "label": Path(target_path).parent.name,
        }
        scan_file.write_text(json.dumps(scan_data, ensure_ascii=False, indent=2), encoding="utf-8")

        # Now do fast scan
        self._fast_scan()

    def _trash_dir(self, skill_dir):
        """Move a skill directory to trash. Returns trash path."""
        trash = STATE_DIR.parent / "trash"
        trash.mkdir(parents=True, exist_ok=True)
        ts = time.strftime("%Y%m%d_%H%M%S")
        dest = trash / f"{ts}_{skill_dir.name}"
        # Avoid collision
        if dest.exists():
            for i in range(100):
                candidate = trash / f"{ts}_{skill_dir.name}_{i}"
                if not candidate.exists():
                    dest = candidate
                    break
        shutil.move(str(skill_dir), str(dest))
        # Save metadata for restore
        meta = {"original_path": str(skill_dir), "trashed_at": ts, "name": skill_dir.name}
        (dest / ".trash-meta.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
        return dest

    def _delete_skill(self, name, target=None):
        """Move a skill to trash. If target is given, delete from that dir."""
        if target:
            target_path = Path(target).expanduser().resolve()
            # Validate target is under home directory
            if not target_path.is_relative_to(Path.home()):
                self._json_response({"error": "target must be under home directory"}, status=400)
                return
            skill_dir = target_path / name
            if skill_dir.is_dir():
                try:
                    dest = self._trash_dir(skill_dir)
                    self._json_response({"ok": True, "name": name, "trashed": str(dest)})
                except Exception as e:
                    self._json_response({"error": str(e)}, status=500)
                return
            self._json_response({"error": f"Skill '{name}' not found in {target}"}, status=404)
            return
        # Default: resolve from scan data
        skill_dir = self._resolve_skill_dir(name)
        if not skill_dir:
            self._json_response({"error": f"Skill '{name}' not found"}, status=404)
            return
        try:
            dest = self._trash_dir(skill_dir)
            self._json_response({"ok": True, "name": name, "trashed": str(dest)})
        except Exception as e:
            self._json_response({"error": str(e)}, status=500)

    def _resolve_skill_dir(self, name):
        """Find skill directory on disk. Uses current target first."""
        # 1) Current target (always check first)
        target = self._current_target()
        candidates = [Path(target) / name]
        # 2) Fallback: ~/.claude/skills
        candidates.append(Path.home() / ".claude/skills" / name)
        # 3) Fallback: from latest-scan.json if different
        try:
            scan = json.loads((STATE_DIR / "latest-scan.json").read_text())
            tp = scan.get("target", {}).get("path", "")
            if tp.startswith("~"):
                tp = str(Path.home() / tp[2:])
            p = Path(tp) / name
            if str(p) != str(candidates[0]):
                candidates.append(p)
        except Exception:
            pass
        for d in candidates:
            if d.exists():
                return d
        return None

    def do_PATCH(self):
        """Handle skill update actions."""
        if not self._check_csrf():
            self._csrf_reject()
            return
        path = urlparse(self.path).path
        # Read body
        length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(length).decode('utf-8') if length else '{}'
        try:
            data = json.loads(body)
        except Exception:
            data = {}

        if path.startswith("/api/skill/") and path.endswith("/update"):
            name = self._validate_skill_name(path.split("/")[3])
            if not name:
                self.send_error(400, "Invalid skill name")
            else:
                self._update_upstream(name)
        elif path.startswith("/api/skill/") and path.endswith("/fix"):
            name = self._validate_skill_name(path.split("/")[3])
            if not name:
                self.send_error(400, "Invalid skill name")
            else:
                action = data.get("action", "")
                self._fix_skill(name, action, data)
        else:
            self.send_error(404)

    def _rehash_skill(self, name):
        """Re-record content hash for a skill (confirm change)."""
        target = self._current_target()
        skill_dir = Path(target) / name
        if not skill_dir.is_dir():
            self._json_response({"error": "not found"}, status=404)
            return
        record_content_hash(skill_dir)
        self._json_response({"ok": True, "name": name})

    def _copy_skill(self):
        """Copy a skill from a local directory to the current target library."""
        body = self._read_json()
        if not body:
            self._json_response({"ok": False, "error": "无效请求"}, 400)
            return
        src_path = body.get("src", "")
        target = body.get("target", "") or self._current_target()
        skill_name = body.get("name", "")
        skill_name = self._validate_skill_name(skill_name)
        if not src_path or not skill_name:
            self._json_response({"ok": False, "error": "缺少 src 或 name"}, 400)
            return
        src_dir = Path(src_path).expanduser().resolve()
        if not src_dir.is_dir() or not (src_dir / "SKILL.md").exists():
            self._json_response({"ok": False, "error": f"源目录不存在: {src_path}"}, 400)
            return
        if not src_dir.is_relative_to(Path.home()):
            self._json_response({"ok": False, "error": "src must be under home directory"}, 400)
            return
        target_dir = Path(target).expanduser().resolve()
        if not target_dir.is_relative_to(Path.home()):
            self._json_response({"ok": False, "error": "target must be under home directory"}, 400)
            return
        dest = target_dir / skill_name
        # Snapshot if exists
        if dest.exists():
            create_snapshot(dest)
            shutil.rmtree(dest)
        shutil.copytree(src_dir, dest)
        record_content_hash(dest)
        self._json_response({"ok": True, "name": skill_name, "output": f"Copied to {dest}"})

    def _steal_skill(self):
        """Install a skill from GitHub URL — pure Python, no dashboard."""
        length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(length).decode('utf-8') if length else '{}'
        try:
            data = json.loads(body)
        except Exception:
            data = {}
        source = data.get("source", "").strip()
        skill_name = data.get("name", "").strip()
        if not source:
            self._json_response({"error": "missing source URL"}, status=400)
            return

        target = self._current_target()
        result = install_skill(source, target, preferred_name=skill_name or None)
        self._json_response(result)

    def _update_upstream(self, name):
        """Update a skill from its upstream source — pure Python."""
        target = self._current_target()
        result = update_skill(name, target)
        self._json_response(result)

    def _fix_skill(self, name, action, body=None):
        """Fix a skill issue."""
        if action == "delete":
            self._delete_skill(name)
            return
        elif action == "add_frontmatter":
            skill_dir = self._resolve_skill_dir(name)
            if not skill_dir:
                self._json_response({"error": "not found"}, status=404)
                return
            skill_md = skill_dir / "SKILL.md"
            if not skill_md.exists():
                self._json_response({"error": "no SKILL.md"}, status=400)
                return
            content = skill_md.read_text("utf-8")
            if not content.startswith("---"):
                skill_md.write_text(f"---\nname: {name}\ndescription: ''\n---\n\n{content}", encoding="utf-8")
                self._json_response({"ok": True, "name": name, "fixed": "added frontmatter"})
            else:
                self._json_response({"ok": False, "error": "already has frontmatter"})
            return
        elif action == "add_description":
            skill_dir = self._resolve_skill_dir(name)
            if not skill_dir:
                self._json_response({"error": "not found"}, status=404)
                return
            skill_md = skill_dir / "SKILL.md"
            if not skill_md.exists():
                self._json_response({"error": "no SKILL.md"}, status=400)
                return
            desc = body.get("description", "") if isinstance(body, dict) else ""
            if not desc:
                desc = f"{name} skill"
            content = skill_md.read_text("utf-8")
            if content.startswith("---"):
                # Replace or add description in frontmatter
                import re as _re
                # If description line exists but empty, replace it
                new_content = _re.sub(
                    r'description:\s*[\'"]?\s*[\'"]?\s*\n',
                    f'description: \'{desc}\'\n',
                    content
                )
                if new_content == content:
                    # No description line found — insert after name line
                    new_content = _re.sub(
                        r'(name:\s*.+\n)',
                        rf"\1description: '{desc}'\n",
                        content
                    )
                skill_md.write_text(new_content, encoding="utf-8")
            else:
                # No frontmatter at all — add both
                skill_md.write_text(f"---\nname: {name}\ndescription: '{desc}'\n---\n\n{content}", encoding="utf-8")
            self._json_response({"ok": True, "name": name, "fixed": "added description"})
            return
        self._json_response({"error": f"unknown action: {action}"}, status=400)

    def _json_response(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        """Quieter logging — only show API calls, not static file requests."""
        msg = fmt % args
        if "/api/" in msg or "POST" in msg or "DELETE" in msg:
            sys.stderr.write(f"  {msg}\n")


def main():
    if not STATE_DIR.exists():
        print(f"⚠ State dir not found: {STATE_DIR}")
        print(f"  Creating {STATE_DIR}...")
        STATE_DIR.mkdir(parents=True, exist_ok=True)

    server = HTTPServer(("127.0.0.1", PORT), DashboardHandler)
    url = f"http://localhost:{PORT}"
    print(f"🚀 Skill Dashboard running at {url}")
    print(f"   Data source: {STATE_DIR}")
    print(f"   Install: POST /api/steal {{\"source\": \"https://github.com/...\"}}")
    print(f"   Update:  PATCH /api/skill/{{name}}/update")
    print(f"   Press Ctrl+C to stop")
    print()

    # Auto-open browser
    webbrowser.open(url)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n👋 Stopped.")
        server.server_close()


if __name__ == "__main__":
    main()
