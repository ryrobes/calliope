"""Say it, show it: what is shown below a spoken take instead of being read out."""
import pytest

import calliope
import take_parts

SQL_ANSWER = """I'd use this. I also ran it to check it works, and the result is on the Stage:

SELECT s.store_id, s.store_name,
       sum(o.total) AS revenue
FROM shop.orders o
JOIN shop.stores s ON s.store_id = o.store_id
GROUP BY s.store_id, s.store_name
ORDER BY revenue DESC
LIMIT 10;

A few choices here were mine:

1. Only shipped orders count.
2. "Since July" means from July 1.
3. `total` has no currency column.

Want me to save this as a governed metric?"""

STEPS_ANSWER = """Here's how the process works.

1. An admin opens a Calliope chat and asks to connect the database.

2. The admin fills in the secure form.
   The same form handles SSH tunnels.

3. Calliope lists the schemas.

4. Calliope copies the chosen tables.

5. You can query them.

Before you go to an admin, have these ready:
- The host and port.
- The database name.
- A read-only login.
- Whether it requires SSL.

The easiest path is to have an admin join this conversation."""


def test_code_long_lists_and_links_are_shown_and_short_lists_stay_spoken():
    prose, parts = take_parts.split(SQL_ANSWER)
    assert [p["kind"] for p in parts] == ["code"]
    assert parts[0]["label"] == "SQL query (7 lines)"
    assert parts[0]["body"].startswith("```sql\nSELECT") and parts[0]["body"].endswith("LIMIT 10;\n```")
    assert "SELECT" not in prose and "1. Only shipped orders count." in prose  # three items: said, not shown

    prose, parts = take_parts.split(STEPS_ANSWER)
    assert [(p["kind"], p["label"]) for p in parts] == [
        ("list", 'numbered list (5 items): "An admin opens a Calliope chat and asks…"'),
        ("list", 'bulleted list (4 items): "The host and port."'),
    ]
    assert "The same form handles SSH tunnels." in parts[0]["body"]  # the indented line stays with its item
    assert "Before you go to an admin, have these ready:" in prose     # the lead-in is said
    assert "The host and port" not in prose


def test_tables_fenced_code_images_and_links():
    answer = """North led.

| Region | Orders |
|---|---|
| North | 412 |
| West | 220 |

```python
print("hi")
```

![Orders chart](https://example.com/chart.png)

See [the Hindsight docs](https://hindsight.vectorize.io/) and https://docs.voyageai.com/docs/reranker."""
    prose, parts = take_parts.split(answer)
    assert [p["kind"] for p in parts] == ["table", "code", "image", "links"]
    assert parts[0]["label"] == "table, 2 rows; columns: Region, Orders"
    assert parts[1]["label"] == "code block (1 lines)"
    assert parts[3]["body"] == ("- [the Hindsight docs](https://hindsight.vectorize.io/)\n"
                                "- [https://docs.voyageai.com/docs/reranker](https://docs.voyageai.com/docs/reranker)")
    assert prose == "North led.\n\nSee the Hindsight docs and (link below)."


def test_a_plain_answer_has_nothing_below():
    assert take_parts.split("Revenue is up 8%. Churn is the risk.") == ("Revenue is up 8%. Churn is the risk.", [])
    assert take_parts.rewrite_source("Revenue is up.", []) == "Revenue is up."


def test_the_rewrite_sees_what_is_below_and_must_point_at_it():
    prose, parts = take_parts.split(STEPS_ANSWER)
    source = take_parts.rewrite_source(prose, parts)
    assert "SHOWN BELOW" in source and "[2] bulleted list (4 items)" in source and "- A read-only login." in source
    assert take_parts.contract_issue("Ask an admin to join. Have the details handy.", parts).startswith("it never mentioned")
    assert take_parts.contract_issue("Ask an admin to join; the steps are below.", parts) is None
    assert take_parts.contract_issue("Here goes:\n- first\n- second", []).startswith("it used list")
    assert take_parts.contract_issue("It was 1,016 orders.\n\n2026 was busy.", []) is None
    assert take_parts.speakable("Use `shop.orders`, **not** orders_v2.") == "Use shop.orders, not orders_v2."


def test_the_prompt_points_at_what_is_shown_only_when_something_is():
    shown = calliope._voice_rewrite_instruction("expressive", "dry", shown=True)
    plain = calliope._voice_rewrite_instruction("expressive", "dry")
    assert "SHOWN BELOW" in shown and "Mention each one once" in shown
    assert "SHOWN BELOW" not in plain and "on screen" not in plain
    assert "stays visible" not in shown + plain  # the take replaces the answer; nothing else stays


def test_without_a_rewrite_the_take_is_the_prose_not_the_parts(monkeypatch, tmp_path):
    monkeypatch.setattr(calliope.httpx, "Client", lambda **_kw: pytest.fail("no Hermes configured"))
    config = calliope.CalliopeConfig(hermes_url="", hermes_api_key="", memory_key="company",
                                     file_root=tmp_path, max_image_bytes=1024)
    prose, parts = take_parts.split(SQL_ANSWER)
    script, provider, _ = calliope._generate_voice_script(
        config, take_parts.rewrite_source(prose, parts), "fast", "", "owner@example.com",
        shown=parts, fallback_source=prose)
    assert provider == "canonical" and "SELECT" not in script and "SHOWN BELOW" not in script
    assert "`" not in script and "governed metric" in script
