"""Published decks carry the deck runtime inline, built from the in-repo source."""
import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "services" / "calliope" / "deck-runtime"
GENERATED = ROOT / "datarabbit" / "src" / "lib" / "server" / "deck-runtime.generated.ts"
DECKS = ROOT / "datarabbit" / "src" / "lib" / "server" / "decks.ts"


def source_digest():
    # Mirrors sourceDigest() in datarabbit/scripts/build-deck-runtime.mjs.
    files = [p for p in (RUNTIME / "src").rglob("*") if p.is_file()]
    files += [RUNTIME / n for n in ("vite.config.ts", "tsconfig.json", "tsconfig.node.json") if (RUNTIME / n).exists()]
    digest = hashlib.sha256()
    for name in sorted(p.relative_to(RUNTIME).as_posix() for p in files):
        content = (RUNTIME / name).read_bytes()
        digest.update(f"{name}\n{hashlib.sha256(content).hexdigest()}\n".encode())
    return digest.hexdigest()


def generated_constants():
    values = {}
    for line in GENERATED.read_text(encoding="utf-8").splitlines():
        match = re.match(r"export const (\w+) = (.*)$", line)
        if match:
            values[match.group(1)] = json.loads(match.group(2))
    return values


def test_generated_runtime_matches_the_runtime_source():
    constants = generated_constants()
    assert constants["DECK_RUNTIME_SOURCE_SHA256"] == source_digest(), (
        "services/calliope/deck-runtime changed; run node datarabbit/scripts/build-deck-runtime.mjs"
    )
    assert "RvbbitDeck" in constants["DECK_RUNTIME_JS"]
    assert "</script" not in constants["DECK_RUNTIME_JS"].lower()
    assert "</style" not in constants["DECK_RUNTIME_CSS"].lower()


def test_published_decks_inline_the_runtime():
    decks = DECKS.read_text(encoding="utf-8")
    assert "<style>${DECK_RUNTIME_CSS}</style>" in decks
    assert "<script>${DECK_RUNTIME_JS}</script>" in decks
    assert "rvbbit.ai" not in decks
    assert "<script src=" not in decks and "rel=\"stylesheet\"" not in decks
