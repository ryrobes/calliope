import json

import hermes_skills


class _Result:
    def __init__(self, row):
        self._row = row

    def fetchone(self):
        return self._row


class _Connection:
    def __init__(self):
        self.calls = []

    def execute(self, query, params):
        self.calls.append((query, params))
        return _Result({"result": {"changed": True, "skills": 2}})


def _write_skill(root, relative, frontmatter, body="# Procedure\n\nDo the useful thing.\n"):
    path = root / relative / "SKILL.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\n{frontmatter}\n---\n\n{body}", encoding="utf-8")
    return path


def test_snapshot_is_compact_metadata_not_skill_body(tmp_path):
    _write_skill(
        tmp_path,
        "productivity/revenue-review",
        "name: revenue-review\ndescription: Review recurring revenue movements.",
        body="# Secret procedure\n\nThis large body must not enter the capability stub.\n",
    )

    manifest, stubs = hermes_skills.skill_snapshot(tmp_path)

    assert len(manifest) == 64
    assert stubs == [{
        "name": "revenue-review",
        "load_name": "revenue-review",
        "category": "productivity",
        "description": "Review recurring revenue movements.",
        "source_path": "productivity/revenue-review/SKILL.md",
        "content_hash": stubs[0]["content_hash"],
    }]
    assert "large body" not in json.dumps(stubs)


def test_block_description_and_duplicate_names_use_exact_category_paths(tmp_path):
    _write_skill(
        tmp_path,
        "finance/review",
        "name: review\ndescription: >-\n  Review financial\n  evidence carefully.",
    )
    _write_skill(
        tmp_path,
        "sales/review",
        "name: review\ndescription: Review the sales pipeline.",
    )

    _manifest, stubs = hermes_skills.skill_snapshot(tmp_path)

    assert {stub["load_name"] for stub in stubs} == {"finance/review", "sales/review"}
    assert stubs[0]["description"] == "Review financial evidence carefully."


def test_body_change_updates_manifest_without_projecting_body(tmp_path):
    path = _write_skill(
        tmp_path,
        "general/example",
        "name: example\ndescription: A stable searchable description.",
        body="First version.",
    )
    first_manifest, first = hermes_skills.skill_snapshot(tmp_path)
    path.write_text(path.read_text(encoding="utf-8") + "\nSecond version.\n", encoding="utf-8")
    second_manifest, second = hermes_skills.skill_snapshot(tmp_path)

    assert first_manifest != second_manifest
    assert first[0]["description"] == second[0]["description"]
    assert "Second version" not in json.dumps(second)


def test_sync_calls_the_single_bulk_reconciler(tmp_path, monkeypatch):
    _write_skill(
        tmp_path,
        "general/answering",
        "name: answering\ndescription: Answer with grounded evidence.",
    )
    monkeypatch.setattr(hermes_skills, "_CACHE", None)
    connection = _Connection()

    result = hermes_skills.sync_skill_capabilities(connection, tmp_path)

    assert result == {"changed": True, "skills": 2}
    assert len(connection.calls) == 1
    query, params = connection.calls[0]
    assert "rvbbit.sync_hermes_skill_capabilities" in query
    assert json.loads(params[1])[0]["load_name"] == "answering"


def test_missing_shared_volume_does_not_publish_an_empty_snapshot(tmp_path):
    missing = tmp_path / "not-mounted"
    try:
        hermes_skills.skill_snapshot(missing)
    except FileNotFoundError as exc:
        assert "skills root is unavailable" in str(exc)
    else:
        raise AssertionError("missing volume should fail closed")

