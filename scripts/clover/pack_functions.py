#!/usr/bin/env python3
"""Keep the managed Clover pack's copies of extension functions current.

The pack's install SQL re-creates some rvbbit functions that pg_rvbbit
migrations also own (the Jev decision helpers and decision trees). The
installer runs after the schema, so a stale copy here silently overwrites a
newer migration on every Clover install or re-apply. This module compares each
pack copy with the latest migration definition of the same signature.

    pack_functions.py check   # exit 1 when any copy differs from its latest migration
    pack_functions.py sync    # replace differing copies with the latest migration text

Functions only the pack defines are left alone.
"""
import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS = ROOT / "crates/pg_rvbbit/sql/migrations"
PACK = ROOT / "capabilities/packs/managed/clover/capability.json"
START = re.compile(r"CREATE\s+(?:OR\s+REPLACE\s+)?FUNCTION\s+rvbbit\.([a-z_0-9]+)\s*\(", re.I)
DOLLAR = re.compile(r"\$([A-Za-z_0-9]*)\$")


def _split_args(text):
    depth, current, args = 0, "", []
    for ch in text:
        depth += {"(": 1, ")": -1}.get(ch, 0)
        if ch == "," and depth == 0:
            args.append(current)
            current = ""
        else:
            current += ch
    if current.strip():
        args.append(current)
    return args


def _arg_types(text):
    """Signature identity: argument types without names or defaults."""
    types = []
    for arg in _split_args(text):
        arg = re.split(r"\s+DEFAULT\s+|\s*=\s*", arg.strip(), maxsplit=1, flags=re.I)[0]
        tokens = arg.split()
        if tokens and tokens[0].upper() in ("IN", "OUT", "INOUT", "VARIADIC"):
            tokens = tokens[1:]
        if len(tokens) > 1:
            tokens = tokens[1:]
        types.append(" ".join(tokens).lower())
    return tuple(types)


def functions(sql):
    """Yield ((name, arg_types), statement) for each dollar-quoted rvbbit function."""
    for match in START.finditer(sql):
        i, depth = match.end(), 1
        while depth:
            depth += {"(": 1, ")": -1}.get(sql[i], 0)
            i += 1
        quote = DOLLAR.search(sql, i)
        if not quote:
            continue
        close = sql.find(quote.group(0), quote.end())
        end = sql.find(";", close + len(quote.group(0)))
        if close < 0 or end < 0:
            continue
        yield (match.group(1), _arg_types(sql[match.end():i - 1])), sql[match.start():end + 1]


def _normal(statement):
    return re.sub(r"\s+", " ", statement).strip()


def migration_history():
    history = {}
    for path in sorted(MIGRATIONS.glob("*.sql")):
        for key, statement in functions(path.read_text()):
            history.setdefault(key, []).append((path.stem, statement))
    return history


def report(pack=PACK):
    """One row per pack function: (name, types, state, latest migration or None)."""
    history = migration_history()
    rows = []
    for text in json.loads(pack.read_text())["managed"]["install"]["sql"]:
        for key, statement in functions(text):
            copies = history.get(key)
            if not copies:
                rows.append((key[0], key[1], "pack-only", None))
            elif _normal(statement) == _normal(copies[-1][1]):
                rows.append((key[0], key[1], "current", copies[-1][0]))
            else:
                rows.append((key[0], key[1], "stale", copies[-1][0]))
    return rows


def sync(pack=PACK):
    """Rewrite stale copies with the latest migration text; return what changed."""
    history = migration_history()
    document = json.loads(pack.read_text())
    statements = document["managed"]["install"]["sql"]
    changed = []
    for index, text in enumerate(statements):
        for key, statement in functions(text):
            copies = history.get(key)
            if copies and _normal(statement) != _normal(copies[-1][1]):
                text = text.replace(statement, copies[-1][1])
                changed.append((key[0], key[1], copies[-1][0]))
        statements[index] = text
    if changed:
        pack.write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n")
    return changed


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=["check", "sync"])
    command = parser.parse_args().command
    if command == "sync":
        for name, types, source in sync():
            print(f"synced {name}({', '.join(types)}) from {source}")
        return 0
    stale = [row for row in report() if row[2] == "stale"]
    for name, types, _, source in stale:
        print(f"stale: {name}({', '.join(types)}); latest is {source}", file=sys.stderr)
    return 1 if stale else 0


if __name__ == "__main__":
    sys.exit(main())
