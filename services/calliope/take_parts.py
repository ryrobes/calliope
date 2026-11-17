"""Say it, show it: the parts of an answer that are shown rather than spoken.

Callie's take of an answer is speech, shown word for word with karaoke. Code,
tables, images, long lists and links are not speech. They are taken out of the
answer before the rewrite and shown below the take exactly as written; the
rewrite is told what is there, so it points at them instead of reading them out.
Short lists stay in the prose for the take to say.
"""
from __future__ import annotations

import re
from typing import Any

MIN_LIST_ITEMS = 4
FENCE_OPEN = re.compile(r"^\s*```\s*([^\s`]*)")
FENCE_CLOSE = re.compile(r"^\s*```\s*$")
LIST_ITEM = re.compile(r"^\s*(?:(\d+)[.)]|[-*+•])\s+\S")
SQL_START = re.compile(r"^\s*(?:SELECT|WITH|INSERT|UPDATE|DELETE|CREATE)\b", re.I)
TABLE_RULE = re.compile(r"^\s*\|?\s*:?-{3,}")
IMAGE = re.compile(r"!\[([^\]]*)\]\(([^)\s]+)\)")
MD_LINK = re.compile(r"(?<!!)\[([^\]]+)\]\((https?://[^)\s]+)\)")
BARE_URL = re.compile(r"<?(https?://[^\s<>)\]]+?)>?(?=[\s)\],;]|[.!?](?:\s|$)|$)")
POINTS_AT_SCREEN = re.compile(r"\b(below|beneath|underneath|on (?:the |your )?screen|down there)\b", re.I)
STRUCTURE = re.compile(r"^\s*(?:\|.*\||```|#{1,6}\s|(?:\d+[.)]|[-*+•])\s+\S)", re.M)


def _blocks(text: str) -> list[list[str]]:
    """Paragraphs as line lists; a fenced code block is one paragraph."""
    blocks, current, lines, i = [], [], text.replace("\r\n", "\n").split("\n"), 0
    while i < len(lines):
        line = lines[i]
        if FENCE_OPEN.match(line):
            if current:
                blocks.append(current)
                current = []
            fence = [line]
            i += 1
            while i < len(lines) and not FENCE_CLOSE.match(lines[i]):
                fence.append(lines[i])
                i += 1
            if i < len(lines):
                fence.append(lines[i])
            blocks.append(fence)
        elif line.strip():
            current.append(line)
        elif current:
            blocks.append(current)
            current = []
        i += 1
    if current:
        blocks.append(current)
    return blocks


def _words(text: str, n: int = 8) -> str:
    words = re.sub(r"[*_`#>|]+", " ", text).split()
    return " ".join(words[:n]) + ("…" if len(words) > n else "")


def split(answer: Any) -> tuple[str, list[dict[str, str]]]:
    """(prose for the take, parts shown below it in their original order)."""
    parts: list[dict[str, str]] = []
    kept: list[str] = []
    pending_list: list[list[str]] = []

    def flush_list() -> None:
        items = [line for block in pending_list for line in block if LIST_ITEM.match(line)]
        body = "\n\n".join("\n".join(block) for block in pending_list)
        if len(items) >= MIN_LIST_ITEMS:
            numbered = bool(LIST_ITEM.match(items[0]).group(1))
            first_item = re.sub(r"^\s*(?:\d+[.)]|[-*+•])\s+", "", items[0])
            label = f"{'numbered' if numbered else 'bulleted'} list ({len(items)} items): \"{_words(first_item)}\""
            parts.append({"kind": "list", "label": label, "body": body})
        elif body:
            kept.append(body)
        pending_list.clear()

    for block in _blocks(str(answer or "")):
        first = block[0]
        if FENCE_OPEN.match(first):
            flush_list()
            language = FENCE_OPEN.match(first).group(1).lower()
            lines = max(0, len(block) - 2)
            kind = "SQL query" if language == "sql" or (len(block) > 1 and SQL_START.match(block[1])) else "code block"
            parts.append({"kind": "code", "label": f"{kind} ({lines} lines)", "body": "\n".join(block)})
            continue
        if SQL_START.match(first):
            flush_list()
            parts.append({"kind": "code", "label": f"SQL query ({len(block)} lines)",
                          "body": "```sql\n" + "\n".join(block) + "\n```"})
            continue
        if len(block) >= 2 and "|" in first and TABLE_RULE.match(block[1]):
            flush_list()
            columns = [c.strip() for c in first.strip().strip("|").split("|") if c.strip()]
            parts.append({"kind": "table", "label": f"table, {len(block) - 2} rows; columns: {', '.join(columns)}",
                          "body": "\n".join(block)})
            continue
        if len(block) == 1 and IMAGE.fullmatch(first.strip()):
            flush_list()
            parts.append({"kind": "image", "label": "image: " + (IMAGE.match(first.strip()).group(1) or "picture"),
                          "body": first.strip()})
            continue
        starts = [i for i, line in enumerate(block) if LIST_ITEM.match(line)]
        if starts and starts[0] == 0:
            pending_list.append(block)
            continue
        if pending_list and block[0].startswith((" ", "\t")):
            pending_list.append(block)  # an indented paragraph continues the list item above
            continue
        flush_list()
        if starts:
            # A lead-in ("Have these ready:") stays in the prose; the list goes with the list.
            kept.append("\n".join(block[:starts[0]]))
            pending_list.append(block[starts[0]:])
            continue
        kept.append("\n".join(block))
    flush_list()

    links: list[str] = []

    def keep_link(match: re.Match) -> str:
        links.append(f"[{match.group(1)}]({match.group(2)})")
        return match.group(1)

    def bare_link(match: re.Match) -> str:
        links.append(f"[{match.group(1)}]({match.group(1)})")
        return "(link below)"

    prose = "\n\n".join(kept)
    prose = MD_LINK.sub(keep_link, prose)
    prose = BARE_URL.sub(bare_link, prose)
    if links:
        unique = list(dict.fromkeys(links))
        parts.append({"kind": "links", "label": f"{len(unique)} link{'s' if len(unique) != 1 else ''}: "
                      + "; ".join(MD_LINK.match(link).group(1) for link in unique),
                      "body": "\n".join("- " + link for link in unique)})
    return re.sub(r"\n{3,}", "\n\n", prose).strip(), parts


def rewrite_source(prose: str, parts: list[dict[str, str]]) -> str:
    """The answer the rewrite sees: the prose, then what is shown below the take."""
    if not parts:
        return prose
    listed = "\n\n".join(f"[{i}] {part['label']}:\n{part['body']}" for i, part in enumerate(parts, 1))
    return (f"{prose}\n\nSHOWN BELOW (on screen under your words, exactly as written; "
            f"never read out):\n{listed}").strip()


def contract_issue(script: str, parts: list[dict[str, str]]) -> str | None:
    """What a take must not do once some of the answer is shown below it."""
    if STRUCTURE.search(str(script or "")):
        return "it used list, table, heading or code formatting; it is spoken, so write sentences"
    if parts and not POINTS_AT_SCREEN.search(str(script or "")):
        return "it never mentioned what is shown below; mention each shown part once, in a few words"
    return None


def speakable(script: str) -> str:
    """Markers no one should hear: backticks and bold markers around words."""
    return re.sub(r"\*\*|__|`", "", str(script or ""))
