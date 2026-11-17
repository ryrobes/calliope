"""The server image copies an explicit file list; every module it imports must be on it.

A module that tests import from the checkout but the Dockerfile never copies
passes every test and then kills the container at boot with
ModuleNotFoundError (stage_arrangements.py did this on the AWS pilot).
"""
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
IMPORT = re.compile(r"^\s*(?:from\s+([A-Za-z_]\w*)[\w.]*\s+import\b|import\s+([A-Za-z_][\w, .]*))", re.M)


def _copied_modules():
    dockerfile = (HERE / "Dockerfile").read_text(encoding="utf-8")
    copied = set()
    for pattern in re.findall(r"services/calliope/([\w*?\[\]-]+)\.py\b", dockerfile):
        copied |= {p.stem for p in HERE.glob(pattern + ".py")}  # plain names and globs alike
    return copied


def _imports(path):
    names = set()
    for single, multi in IMPORT.findall(path.read_text(encoding="utf-8")):
        for part in ([single] if single else multi.split(",")):
            name = part.strip().split(" ")[0].split(".")[0]
            if name:
                names.add(name)
    return names


def test_every_local_module_the_image_imports_is_copied():
    local = {p.stem for p in HERE.glob("*.py") if not p.stem.startswith("test_")}
    copied = _copied_modules()
    assert {"server", "calliope"} <= copied
    missing, seen, queue = {}, set(), sorted(copied & local)
    while queue:
        module = queue.pop()
        if module in seen:
            continue
        seen.add(module)
        for name in _imports(HERE / f"{module}.py") & local:
            if name not in copied:
                missing.setdefault(name, set()).add(module)
            queue.append(name)
    assert not missing, (
        "Add these to the COPY list in services/calliope/Dockerfile: "
        + ", ".join(f"{name}.py (imported by {', '.join(sorted(by))})" for name, by in sorted(missing.items()))
    )
