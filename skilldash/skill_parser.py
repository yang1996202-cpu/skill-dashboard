"""Small SKILL.md parser used by the local dashboard.

The project intentionally avoids PyYAML and markdown dependencies.  This parser
only handles the frontmatter and markdown features that matter for browsing and
triage.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class ParsedSkill:
    name: str
    path: str
    content: str
    body: str
    frontmatter: dict[str, Any]
    description: str
    headings: list[str]
    content_hash: str
    size: int


def _strip_quotes(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        return value[1:-1]
    return value


def _parse_scalar(value: str) -> Any:
    value = _strip_quotes(value.strip())
    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1].strip()
        if not inner:
            return []
        return [_strip_quotes(part.strip()) for part in inner.split(",") if part.strip()]
    return value


def parse_frontmatter(content: str) -> tuple[dict[str, Any], str]:
    """Parse simple YAML frontmatter and return (metadata, body)."""
    lines = content.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, content.strip()

    end_idx = None
    for i, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            end_idx = i
            break
    if end_idx is None:
        return {}, content.strip()

    fm_lines = lines[1:end_idx]
    body = "\n".join(lines[end_idx + 1 :]).strip()
    data: dict[str, Any] = {}
    i = 0
    while i < len(fm_lines):
        raw = fm_lines[i]
        stripped = raw.strip()
        i += 1
        if not stripped or stripped.startswith("#") or ":" not in stripped:
            continue
        key, value = stripped.split(":", 1)
        key = key.strip()
        value = value.strip()
        if value in (">", "|", ">-", "|-", ">+", "|+"):
            parts: list[str] = []
            while i < len(fm_lines):
                cont = fm_lines[i]
                if cont and not cont[0].isspace():
                    break
                parts.append(cont.strip())
                i += 1
            data[key] = " ".join(part for part in parts if part)
        elif not value and i < len(fm_lines) and fm_lines[i].lstrip().startswith("- "):
            items: list[str] = []
            while i < len(fm_lines) and fm_lines[i].lstrip().startswith("- "):
                items.append(_strip_quotes(fm_lines[i].lstrip()[2:].strip()))
                i += 1
            data[key] = items
        else:
            data[key] = _parse_scalar(value)
    return data, body


def markdown_headings(body: str, limit: int = 10) -> list[str]:
    headings: list[str] = []
    for line in body.splitlines():
        match = re.match(r"^\s{0,3}#{1,3}\s+(.+?)\s*$", line)
        if match:
            text = re.sub(r"\s+#*$", "", match.group(1)).strip()
            if text:
                headings.append(text)
        if len(headings) >= limit:
            break
    return headings


def parse_skill_dir(skill_dir: str | Path) -> ParsedSkill:
    skill_path = Path(skill_dir).expanduser().resolve()
    skill_md = skill_path / "SKILL.md"
    content = skill_md.read_text(encoding="utf-8", errors="ignore")
    frontmatter, body = parse_frontmatter(content)
    name = str(frontmatter.get("name") or skill_path.name).strip() or skill_path.name
    description = str(frontmatter.get("description") or "").strip()
    content_hash = hashlib.sha256(content.encode("utf-8", errors="ignore")).hexdigest()
    return ParsedSkill(
        name=name,
        path=str(skill_path),
        content=content,
        body=body,
        frontmatter=frontmatter,
        description=description,
        headings=markdown_headings(body),
        content_hash=content_hash,
        size=len(content.encode("utf-8", errors="ignore")),
    )

