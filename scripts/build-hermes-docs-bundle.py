#!/usr/bin/env python3
"""Build the pre-enriched Hermes documentation bundle shipped by Calliope.

The input checkout must be the exact revision declared in source.json.  The
script performs deterministic structural chunking and KG extraction locally;
only the final batch embedding call uses Clover.  Customer appliances import
the resulting gzip bundle and never run this build pipeline.
"""

from __future__ import annotations

import argparse
import fnmatch
import gzip
import hashlib
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = ROOT / "docker/calliope/hermes-docs/source.json"
DEFAULT_OVERLAYS = ROOT / "docker/calliope/hermes-docs/overlays"
DEFAULT_OUTPUT = ROOT / "docker/calliope/hermes-docs/hermes-docs.bundle.json.gz"
HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
FRONTMATTER_RE = re.compile(r"\A---\s*\n.*?\n---\s*\n", re.DOTALL)
ENV_RE = re.compile(r"\b[A-Z][A-Z0-9_]{2,}\b")
DOTTED_KEY_RE = re.compile(r"\b[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+\b")
YAML_KEY_RE = re.compile(r"^\s{0,12}([a-z][a-z0-9_-]{1,63})\s*:", re.MULTILINE)
LINK_RE = re.compile(r"\[[^\]]+\]\(([^)\s]+)(?:\s+[^)]*)?\)")
BUILDER_VERSION = "calliope-hermes-docs-builder-v1"


def _sha(value: str | bytes) -> str:
    raw = value.encode("utf-8") if isinstance(value, str) else value
    return hashlib.sha256(raw).hexdigest()


def _git_revision(path: Path) -> str:
    result = subprocess.run(
        ["git", "-C", str(path), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _matching_files(root: Path, includes: list[str], excludes: list[str]) -> list[Path]:
    selected: dict[str, Path] = {}
    for pattern in includes:
        for path in root.glob(pattern):
            if not path.is_file():
                continue
            relative = path.relative_to(root).as_posix()
            if any(fnmatch.fnmatch(relative, item) for item in excludes):
                continue
            selected[relative] = path
    return [selected[name] for name in sorted(selected)]


def _title(markdown: str, fallback: str) -> str:
    for line in markdown.splitlines():
        match = HEADING_RE.match(line)
        if match:
            return re.sub(r"[`*_]", "", match.group(2)).strip()
    return fallback


def _split_oversize(text: str, limit: int) -> Iterable[str]:
    remaining = text.strip()
    while len(remaining) > limit:
        cut = remaining.rfind("\n", 0, limit + 1)
        if cut < limit // 2:
            cut = remaining.rfind(" ", 0, limit + 1)
        if cut < limit // 2:
            cut = limit
        yield remaining[:cut].strip()
        remaining = remaining[cut:].strip()
    if remaining:
        yield remaining


def chunk_markdown(markdown: str, max_chars: int = 3200) -> list[tuple[str, str]]:
    """Return heading-aware chunks while keeping fenced examples intact."""
    markdown = FRONTMATTER_RE.sub("", markdown, count=1).strip()
    sections: list[tuple[str, list[str]]] = []
    heading = "Overview"
    lines: list[str] = []
    fenced = False
    for line in markdown.splitlines():
        if line.lstrip().startswith("```") or line.lstrip().startswith("~~~"):
            fenced = not fenced
        match = None if fenced else HEADING_RE.match(line)
        if match:
            if any(item.strip() for item in lines):
                sections.append((heading, lines))
            heading = re.sub(r"[`*_]", "", match.group(2)).strip()
            lines = [line]
        else:
            lines.append(line)
    if any(item.strip() for item in lines):
        sections.append((heading, lines))

    chunks: list[tuple[str, str]] = []
    for section_heading, section_lines in sections:
        paragraphs: list[str] = []
        buffer: list[str] = []
        fenced = False
        for line in section_lines:
            if line.lstrip().startswith("```") or line.lstrip().startswith("~~~"):
                fenced = not fenced
            if not fenced and not line.strip() and buffer:
                paragraphs.append("\n".join(buffer).strip())
                buffer = []
            else:
                buffer.append(line)
        if buffer:
            paragraphs.append("\n".join(buffer).strip())

        packed = ""
        for paragraph in paragraphs:
            if not paragraph:
                continue
            if packed and len(packed) + len(paragraph) + 2 > max_chars:
                chunks.extend((section_heading, part) for part in _split_oversize(packed, max_chars))
                packed = ""
            if len(paragraph) > max_chars:
                if packed:
                    chunks.extend((section_heading, part) for part in _split_oversize(packed, max_chars))
                    packed = ""
                chunks.extend((section_heading, part) for part in _split_oversize(paragraph, max_chars))
            else:
                packed = paragraph if not packed else f"{packed}\n\n{paragraph}"
        if packed:
            chunks.extend((section_heading, part) for part in _split_oversize(packed, max_chars))
    # A reference manual often has many tiny adjacent headings. Keep their
    # heading lines in the text, but pack neighboring sections into one bounded
    # embedding unit so the shipping corpus stays compact and retrieval returns
    # useful surrounding instructions rather than isolated option fragments.
    packed_chunks: list[tuple[str, str]] = []
    for section_heading, text in chunks:
        if packed_chunks and len(packed_chunks[-1][1]) + len(text) + 2 <= max_chars:
            prior_heading, prior_text = packed_chunks[-1]
            packed_chunks[-1] = (prior_heading, f"{prior_text}\n\n{text}")
        else:
            packed_chunks.append((section_heading, text))
    return packed_chunks


def _entity_pairs(text: str) -> set[tuple[str, str]]:
    entities = {("environment_variable", match.group(0)) for match in ENV_RE.finditer(text)}
    entities.update(("config_key", match.group(0)) for match in DOTTED_KEY_RE.finditer(text))
    entities.update(("config_key", match.group(1)) for match in YAML_KEY_RE.finditer(text))
    entities.update(("reference", match.group(1)) for match in LINK_RE.finditer(text))
    return {(kind, label[:500]) for kind, label in entities if 1 < len(label) <= 500}


def _embed_batches(
    texts: list[str], *, base_url: str, api_key: str, batch_size: int
) -> list[list[float]]:
    vectors: list[list[float]] = []
    endpoint = base_url.rstrip("/") + "/embeddings"
    for offset in range(0, len(texts), batch_size):
        payload = json.dumps({"input": texts[offset : offset + batch_size]}).encode("utf-8")
        request = urllib.request.Request(
            endpoint,
            data=payload,
            method="POST",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=180) as response:
                result = json.load(response)
        except urllib.error.HTTPError as exc:
            detail = exc.read(800).decode("utf-8", errors="replace")
            raise RuntimeError(f"Clover embeddings failed ({exc.code}): {detail}") from exc
        data = sorted(result.get("data") or [], key=lambda item: int(item.get("index", 0)))
        batch_vectors = [item.get("embedding") for item in data]
        if len(batch_vectors) != len(texts[offset : offset + batch_size]):
            raise RuntimeError("Clover embeddings response length did not match the request")
        for vector in batch_vectors:
            if not isinstance(vector, list) or not vector:
                raise RuntimeError("Clover returned an empty embedding")
            vectors.append([float(value) for value in vector])
        print(
            f"embedded {min(offset + batch_size, len(texts))}/{len(texts)} chunks",
            file=sys.stderr,
        )
    if vectors and len({len(vector) for vector in vectors}) != 1:
        raise RuntimeError("Clover returned inconsistent embedding dimensions")
    return vectors


def build_bundle(
    checkout: Path,
    source_spec: dict[str, Any],
    overlays: Path,
    *,
    base_url: str,
    api_key: str,
    batch_size: int,
) -> dict[str, Any]:
    expected_revision = str(source_spec["revision"])
    actual_revision = _git_revision(checkout)
    if actual_revision != expected_revision:
        raise RuntimeError(
            f"Hermes checkout revision {actual_revision} does not match {expected_revision}"
        )

    inputs: list[tuple[str, Path, str]] = [
        ("upstream", path, path.relative_to(checkout).as_posix())
        for path in _matching_files(
            checkout,
            list(source_spec.get("include") or []),
            list(source_spec.get("exclude") or []),
        )
    ]
    inputs.extend(
        ("calliope_overlay", path, f"calliope/{path.relative_to(overlays).as_posix()}")
        for path in sorted(overlays.rglob("*.md"))
    )
    if not inputs:
        raise RuntimeError("no Hermes documentation files matched")

    documents: list[dict[str, Any]] = []
    chunks: list[dict[str, Any]] = []
    nodes: dict[str, dict[str, Any]] = {}
    edges: dict[str, dict[str, Any]] = {}
    source_hash = hashlib.sha256(BUILDER_VERSION.encode("utf-8"))
    for source_kind, path, relative in inputs:
        body = path.read_text(encoding="utf-8")
        source_hash.update(relative.encode("utf-8"))
        source_hash.update(b"\0")
        source_hash.update(body.encode("utf-8"))
        doc_id = _sha(f"{source_kind}:{relative}")[:24]
        title = _title(body, path.stem.replace("-", " ").title())
        documents.append({
            "doc_id": doc_id,
            "source_kind": source_kind,
            "path": relative,
            "title": title,
            "body": body,
            "metadata": {
                "upstream_revision": expected_revision,
                "source_repository": source_spec["repository"],
            },
        })
        doc_node_id = f"doc:{doc_id}"
        nodes[doc_node_id] = {
            "node_id": doc_node_id,
            "kind": "document",
            "label": title,
            "properties": {"path": relative, "source_kind": source_kind},
        }
        for ordinal, (heading, text) in enumerate(chunk_markdown(body)):
            chunk_id = _sha(f"{doc_id}:{ordinal}:{heading}:{text}")[:28]
            chunks.append({
                "chunk_id": chunk_id,
                "doc_id": doc_id,
                "ordinal": ordinal,
                "heading": heading,
                "text": text,
                "embedding_text": f"{title}\n{heading}\n{text}",
                "token_est": max(1, len(text) // 4),
                "metadata": {"source_kind": source_kind, "path": relative},
            })
            section_node_id = f"section:{chunk_id}"
            nodes[section_node_id] = {
                "node_id": section_node_id,
                "kind": "section",
                "label": heading,
                "properties": {"doc_id": doc_id, "chunk_id": chunk_id, "ordinal": ordinal},
            }
            edge_id = _sha(f"{doc_node_id}:contains:{section_node_id}")[:28]
            edges[edge_id] = {
                "edge_id": edge_id,
                "source_node_id": doc_node_id,
                "target_node_id": section_node_id,
                "relation": "contains",
                "properties": {},
            }
            for kind, label in sorted(_entity_pairs(text)):
                entity_id = f"{kind}:{_sha(label.lower())[:24]}"
                nodes.setdefault(entity_id, {
                    "node_id": entity_id,
                    "kind": kind,
                    "label": label,
                    "properties": {},
                })
                edge_id = _sha(f"{section_node_id}:mentions:{entity_id}")[:28]
                edges[edge_id] = {
                    "edge_id": edge_id,
                    "source_node_id": section_node_id,
                    "target_node_id": entity_id,
                    "relation": "mentions",
                    "properties": {},
                }

    vectors = _embed_batches(
        [chunk.pop("embedding_text") for chunk in chunks],
        base_url=base_url,
        api_key=api_key,
        batch_size=batch_size,
    )
    for chunk, vector in zip(chunks, vectors, strict=True):
        chunk["embedding"] = vector
    dimensions = len(vectors[0]) if vectors else 0
    if dimensions <= 0:
        raise RuntimeError("the shipping bundle must contain embeddings")

    request = urllib.request.Request(base_url.rstrip("/") + "/capabilities", headers={"Authorization": "Bearer " + api_key})
    with urllib.request.urlopen(request, timeout=20) as response:
        inventory = json.load(response)
    entries = inventory.get("data", [])
    embedding_route = next(item for item in entries if item["id"] == "embed")
    embedding_identity = embedding_route["model_version"]
    embedding_model = embedding_route.get("model") or embedding_identity
    source_hash.update(embedding_identity.encode())
    logical_hash = source_hash.hexdigest()
    bundle_id = f"hermes-{expected_revision[:12]}-{logical_hash[:12]}-e{dimensions}"
    manifest = {
        "format": "calliope.hermes-docs.v1",
        "builder_version": BUILDER_VERSION,
        "bundle_id": bundle_id,
        "repository": source_spec["repository"],
        "hermes_revision": expected_revision,
        "hermes_image_digest": source_spec["image_digest"],
        "embedding_model": embedding_model,
        "embedding_profile": embedding_identity,
        "embedding_dimensions": dimensions,
        "content_hash": logical_hash,
        "document_count": len(documents),
        "chunk_count": len(chunks),
        "node_count": len(nodes),
        "edge_count": len(edges),
    }
    return {
        "manifest": manifest,
        "documents": documents,
        "chunks": chunks,
        "nodes": sorted(nodes.values(), key=lambda item: item["node_id"]),
        "edges": sorted(edges.values(), key=lambda item: item["edge_id"]),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--checkout", type=Path, required=True)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--overlays", type=Path, default=DEFAULT_OVERLAYS)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--clover-base-url",
        default=os.environ.get("RVBBIT_CLOVER_OPENAI_BASE_URL", "http://clover:8090/v1"),
    )
    parser.add_argument("--batch-size", type=int, default=32)
    args = parser.parse_args()
    api_key = os.environ.get("RVBBIT_CLOVER_KEY", "")
    if not api_key:
        parser.error("RVBBIT_CLOVER_KEY is required to build the shipping bundle")
    source_spec = json.loads(args.source.read_text(encoding="utf-8"))
    bundle = build_bundle(
        args.checkout.resolve(),
        source_spec,
        args.overlays.resolve(),
        base_url=args.clover_base_url,
        api_key=api_key,
        batch_size=max(1, min(args.batch_size, 128)),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(bundle, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    with args.output.open("wb") as output_handle:
        with gzip.GzipFile(filename="", mode="wb", fileobj=output_handle, mtime=0) as handle:
            handle.write(encoded.encode("utf-8"))
    print(json.dumps(bundle["manifest"], indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
