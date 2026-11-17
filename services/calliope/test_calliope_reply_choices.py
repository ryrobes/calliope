"""Contracts for the asynchronous Clover reply-choice projection."""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location(
    "warehouse_calliope_reply_choice_test_module", HERE / "calliope.py"
)
calliope = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = calliope
SPEC.loader.exec_module(calliope)


def test_reply_choice_prompt_extracts_only_explicit_decisions():
    messages = calliope._reply_choice_messages(
        "Which scope should I use: all teams or one project?"
    )

    assert messages[0]["role"] == "system"
    assert "Never invent choices" in messages[0]["content"]
    assert "complete natural-language user reply" in messages[0]["content"]
    assert json.loads(messages[1]["content"]) == {
        "calliope_answer": "Which scope should I use: all teams or one project?"
    }


def test_reply_choice_normalizer_requires_two_complete_distinct_options():
    normalized = calliope._normalize_reply_choices({
        "questions": [
            {
                "prompt": "Which scope should I use?",
                "options": [
                    {"label": "All teams", "response": "Use all teams."},
                    {"label": "One project", "response": "Use one project."},
                    {"label": "ALL TEAMS", "response": "Duplicate."},
                    {"label": "No response", "response": ""},
                ],
            },
            {
                "prompt": "This is not a choice",
                "options": [{"label": "Only", "response": "Only one."}],
            },
        ]
    })

    assert normalized == [{
        "prompt": "Which scope should I use?",
        "options": [
            {"label": "All teams", "response": "Use all teams."},
            {"label": "One project", "response": "Use one project."},
        ],
    }]


def test_reply_choice_json_accepts_fenced_provider_output():
    payload = calliope._reply_choice_json(
        '```json\n{"questions":[{"prompt":"Choose?","options":[]}]}\n```'
    )
    assert payload["questions"][0]["prompt"] == "Choose?"


def test_reply_choice_projection_uses_clover_without_auto_answering(monkeypatch, tmp_path):
    captured = {}

    class Response:
        status_code = 200

        @staticmethod
        def json():
            return {
                "model": "calliope-test",
                "choices": [{
                    "finish_reason": "stop",
                    "message": {"content": json.dumps({
                        "questions": [{
                            "prompt": "How much history?",
                            "options": [
                                {"label": "90 days", "response": "Use the last 90 days."},
                                {"label": "All history", "response": "Use all available history."},
                            ],
                        }]
                    })},
                }],
            }

    class Client:
        def __init__(self, **kwargs):
            captured["client"] = kwargs

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def post(self, url, **kwargs):
            captured.update({"url": url, **kwargs})
            return Response()

    monkeypatch.setattr(calliope.httpx, "Client", Client)
    monkeypatch.setattr(calliope, "_reply_choice_turn", lambda *_args: {
        "id": "turn-id",
        "session_id": "session-id",
        "assistant_message": "Choose 90 days or all history.",
    })
    config = calliope.CalliopeConfig(
        hermes_url="http://hermes:8642",
        hermes_api_key="hermes-key",
        memory_key="company",
        file_root=tmp_path,
        max_image_bytes=1024,
        voice_rewrite_api_key="clover-secret",
        voice_rewrite_base_url="https://clover.example/v1",
        voice_rewrite_model="calliope",
    )

    result = calliope._generate_reply_choices(
        lambda: None, config, "owner@example.com", "session-id", "turn-id"
    )

    assert captured["url"] == "https://clover.example/v1/chat/completions"
    assert captured["json"]["model"] == "calliope"
    assert captured["json"]["temperature"] == 0
    assert "max_tokens" not in captured["json"]
    assert result["provider"] == "clover"
    assert result["model"] == "calliope-test"
    assert result["questions"][0]["options"][1] == {
        "label": "All history",
        "response": "Use all available history.",
    }


def test_reply_choice_frontend_is_transient_replace_only_and_manual_send():
    source = (HERE / "calliope" / "calliope.js").read_text()
    html = (HERE / "calliope" / "index.html").read_text()
    css = (HERE / "calliope" / "calliope.css").read_text()

    assert 'id="reply-choices"' in html
    assert 'fetch("/api/calliope/reply-choices"' in source
    assert "composerSetValue(option.response);" in source
    assert "clearReplyChoices();" in source
    assert "state.replyChoices.sessionId = sessionId;" in source
    assert "state.replyChoices.turnId = turnId;" in source
    assert source.count("scheduleLatestReplyChoices();") >= 3
    click_handler = source.split(
        'els.replyChoices?.addEventListener("click"', 1
    )[1].split("els.stop.addEventListener", 1)[0]
    assert "sendTurn" not in click_handler
    assert ".reply-choice-option.is-selected" in css


def test_reply_choice_route_is_soft_failure_projection():
    source = (HERE / "calliope.py").read_text()

    assert '@mcp.custom_route("/api/calliope/reply-choices", methods=["POST"])' in source
    route = source.split(
        '@mcp.custom_route("/api/calliope/reply-choices"', 1
    )[1].split('@mcp.custom_route("/api/calliope/voice/renders"', 1)[0]
    assert 'return json_response({"version": _REPLY_CHOICE_VERSION, "questions": []})' in route
