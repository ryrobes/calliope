"""Speech-tag vocabulary and preparation of cue-free spoken text.

A model writes the spoken take; a separate model call in voice_tagging adds
contextual delivery cues. This module never chooses emotions or invents tags.
"""

from __future__ import annotations

import re

# ── what the speech model documents ──────────────────────────────────────────
EMOTIONS = frozenset({
    # the auto-tagger's own small set
    "joyful", "sarcastic", "soft", "serious", "calm", "excited", "curious", "confident",
    "worried", "surprised", "relaxed", "wry",
    # the longer documented list
    "happy", "sad", "angry", "nervous", "satisfied", "delighted", "scared", "frustrated",
    "empathetic", "embarrassed", "moved", "proud", "grateful", "disdainful", "anxious",
    "uncertain", "doubtful", "confused", "disappointed", "regretful", "hopeful", "optimistic",
    "nostalgic", "bored", "sympathetic", "determined", "resigned", "pleased",
})
TONES = frozenset({"soft tone", "whispering", "in a hurry tone", "shouting"})
EFFECTS = frozenset({"chuckling", "laughing", "sighing", "gasping", "clear throat"})
PAUSES = frozenset({"short pause", "pause", "long pause"})
STRUCTURE = frozenset({"emphasis"})
ALLOWED = EMOTIONS | TONES | EFFECTS | PAUSES | STRUCTURE

# What models write instead, seen in a week of pilot takes, mapped to what lands.
DRIFT = {
    "dry": "wry", "dryly": "wry", "deadpan": "calm", "sardonic": "sarcastic", "snarky": "sarcastic",
    "teasing": "wry", "amused": "wry", "playful": "joyful", "playfully": "joyful", "cheerful": "happy",
    "cheerfully": "happy", "thoughtful": "calm", "pensive": "calm", "matter-of-fact": "calm",
    "matter of fact": "calm", "measured": "calm", "neutral": "calm", "even": "calm",
    "warm": "soft", "warmly": "soft", "gentle": "soft", "gently": "soft", "softly": "soft",
    "quiet": "soft", "quietly": "soft", "tender": "soft", "kind": "soft", "kindly": "soft",
    "firm": "determined", "firmly": "determined", "resolute": "determined", "brisk": "confident",
    "briskly": "confident", "sure": "confident", "upbeat": "optimistic", "eager": "excited",
    "glad": "happy", "grim": "serious", "stern": "serious", "wary": "worried", "concerned": "worried",
    "uneasy": "anxious", "hesitant": "uncertain", "unsure": "uncertain", "skeptical": "doubtful",
    "sceptical": "doubtful", "puzzled": "confused", "sorry": "regretful", "apologetic": "regretful",
    "tired": "resigned", "weary": "resigned", "sigh": "sighing", "sighs": "sighing",
    "chuckle": "chuckling", "chuckles": "chuckling", "dry chuckle": "chuckling",
    "with a dry chuckle": "chuckling", "with a chuckle": "chuckling", "laugh": "laughing",
    "laughs": "laughing", "giggle": "laughing", "giggles": "laughing", "gasp": "gasping",
    "gasps": "gasping", "whisper": "whispering", "whispers": "whispering", "whispered": "whispering",
    "shout": "shouting", "shouts": "shouting", "hurried": "in a hurry tone", "rushed": "in a hurry tone",
    "urgent": "in a hurry tone", "quickly": "in a hurry tone", "emphatic": "emphasis",
    "stress": "emphasis", "stressed": "emphasis", "beat": "short pause", "brief pause": "short pause",
}
# Deleted outright: not audible, or not a thing the speech model has.
DELETED = frozenset({
    "break", "long-break", "long break", "short-break", "short break", "smirk", "smirks", "smirking",
    "grin", "grins", "grinning", "wink", "winks", "winking", "nod", "nods", "nodding", "shrug",
    "shrugs", "shrugging", "smile", "smiles", "smiling", "eye roll", "eyeroll", "leans in", "leaning in",
})

_CUE_RE = re.compile(r"\[([^\[\]\n]+)\](?!\()")          # a bracketed cue, never a markdown link
_BREVITY_RE = re.compile(r"\b(?:brief|briefly|short|terse|concise|snappy|quick|tight|punchy|few words|one[- ]liner|impactful)\b", re.IGNORECASE)
_LENGTH_RE = re.compile(r"\b(?:thorough|detailed|in depth|in-depth|expansive|complete|comprehensive|walk me through|long-form|longform)\b", re.IGNORECASE)

def strip_cues(text: str) -> str:
    """The take without its cues: what the words are, for hashing and for the contract."""
    out = _CUE_RE.sub("", str(text or ""))
    out = re.sub(r"[ \t]+", " ", out)
    out = re.sub(r" ?\n ?", "\n", out)
    out = re.sub(r"\n{3,}", "\n\n", out)
    return "\n".join(line.strip() for line in out.split("\n")).strip()


def _map_cue(raw: str) -> str | None:
    """One bracket's content → the cue the speech model has, or None to delete it."""
    inner = re.sub(r"\s+", " ", str(raw or "").strip().lower().strip(".:;!"))
    if not inner or not re.search(r"[a-z]", inner):
        return None
    # comma- or slash-joined ideas: the first one that lands wins ("pleased, dry" → pleased)
    parts = [p.strip() for p in re.split(r"\s*[,/;+&]\s*|\s+and\s+|\s+then\s+", inner) if p.strip()]
    for part in parts or [inner]:
        candidate = part
        for prefix in ("tone:", "emotion:", "voice:", "delivery:"):
            if candidate.startswith(prefix):
                candidate = candidate[len(prefix):].strip()
        if candidate in DELETED:
            return None
        if candidate in ALLOWED:
            return candidate
        if candidate in DRIFT:
            return DRIFT[candidate]
        # "soft tone" / "in a soft tone" / "warm tone" → the tone or the emotion behind it
        bare = re.sub(r"^(?:in an? |with an? |a )", "", candidate)
        bare = re.sub(r"\s+(?:tone|voice|delivery)$", "", bare)
        if bare != candidate:
            if bare in ALLOWED:
                return "soft tone" if bare == "soft" and candidate.endswith("tone") else bare
            if bare in DRIFT:
                return DRIFT[bare]
        # a single documented word inside a longer phrase ("a little sarcastic")
        words = bare.split()
        for word in words:
            if word in EMOTIONS:
                return word
            if word in DRIFT and DRIFT[word] in EMOTIONS:
                return DRIFT[word]
    return None


def sanitize_cues(text: str) -> str:
    """Reduce every bracketed cue to one the speech model documents; drop the rest."""
    def replace(match: re.Match[str]) -> str:
        mapped = _map_cue(match.group(1))
        return f"[{mapped}]" if mapped else ""
    out = _CUE_RE.sub(replace, str(text or ""))
    # never stacked: two emotions in a row keep the first; an emotion may be followed by one tone/effect
    def unstack(match: re.Match[str]) -> str:
        cues = re.findall(r"\[([^\]]+)\]", match.group(0))
        kept: list[str] = []
        for cue in cues:
            if cue in EMOTIONS and any(k in EMOTIONS for k in kept):
                continue
            if cue in (TONES | EFFECTS) and any(k in (TONES | EFFECTS) for k in kept):
                continue
            if cue in PAUSES and any(k in PAUSES for k in kept):
                continue
            kept.append(cue)
        return " ".join(f"[{c}]" for c in kept) + " "
    out = re.sub(r"(?:\[[^\]\n]+\]\s*){2,}", unstack, out)
    out = re.sub(r"[ \t]{2,}", " ", out)
    out = re.sub(r" ?\n ?", "\n", out)
    out = re.sub(r"\n{3,}", "\n\n", out)
    return "\n".join(line.strip() for line in out.split("\n")).strip()


def plain_speech(text: str) -> str:
    """Markdown as words: what the canonical fallback may read aloud."""
    out = str(text or "").replace("\r\n", "\n")
    out = re.sub(r"```.*?```", " ", out, flags=re.S)                    # code fences
    out = re.sub(r"`([^`\n]*)`", r"\1", out)                           # inline code
    out = re.sub(r"\[([^\]\n]+)\]\((?:[^)\s]+)\)", r"\1", out)          # links → their text
    out = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", out)                      # images
    lines: list[str] = []
    for line in out.split("\n"):
        stripped = line.strip()
        if re.fullmatch(r"\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?", stripped):
            continue                                                    # table rules
        if stripped.startswith("|") and stripped.endswith("|"):
            cells = [c.strip() for c in stripped.strip("|").split("|")]
            stripped = ", ".join(c for c in cells if c)
        if re.match(r"^#{1,6}\s", stripped):
            continue                                                    # headings are navigation, not speech
        stripped = re.sub(r"^>\s?", "", stripped)                      # quotes
        stripped = re.sub(r"^(?:[-*•‣◦]|\d+[.)])\s+", "", stripped)    # bullets and numbering
        lines.append(stripped)
    out = "\n".join(lines)
    out = re.sub(r"(\*\*|__)(.+?)\1", r"\2", out)                       # bold
    out = re.sub(r"(?<!\w)([*_])(?!\s)(.+?)(?<!\s)\1(?!\w)", r"\2", out)  # italics
    out = re.sub(r"[ \t]{2,}", " ", out)
    out = re.sub(r"\n{3,}", "\n\n", out)
    return "\n".join(line.strip() for line in out.split("\n")).strip()


def word_budget(personality: str) -> tuple[int, int]:
    """The spoken-word range the personality asks for. The ceilings were doubled on
    2026-09-20: at 150 words she could not carry a complex answer and the person had to
    open the canonical text too often; the floor still lets a brief persona be brief."""
    text = str(personality or "")
    if _BREVITY_RE.search(text):
        return (40, 160)
    if _LENGTH_RE.search(text):
        return (120, 440)
    return (60, 300)


def finish(script: str, mode: str, personality: str = "", direction: str = "", *, fallback: bool = False) -> str:
    """Prepare cue-free words. Expressive delivery is a subsequent model call."""
    text = plain_speech(script) if fallback else str(script or "")
    return strip_cues(text)
