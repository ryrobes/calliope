"""Contract test for hermes-silent-output-cap.patch (runs in the image build).

A provider route can clamp completions far below the requested max_tokens and
still return HTTP 200 with finish_reason="length" — no error text, so the
explicit output-cap parser never fires. The patch adds module-level helpers to
agent.conversation_loop that discover the silent ceiling from the truncated
response's own usage and phrase the re-plan nudge; this exercises them plus the
compressor's recognition of the nudge as ephemeral scaffolding.
"""

from types import SimpleNamespace

from agent.conversation_loop import (
    _LENGTH_CONTINUATION_DROPPED_TOOLS_PREFIX,
    _SILENT_CAP_MIN_EPHEMERAL,
    _SILENT_CAP_MIN_OBSERVATION,
    _SILENT_OUTPUT_CLAMP_RATIO,
    _last_message_is_replan_nudge,
    _note_silent_output_clamp,
    _observed_completion_tokens,
    _silent_cap_replan_nudge,
)
from agent.context_compressor import ContextCompressor


# ── usage extraction across response shapes ──────────────────────────────
assert _observed_completion_tokens(
    SimpleNamespace(usage=SimpleNamespace(completion_tokens=1686))
) == 1686
assert _observed_completion_tokens(
    SimpleNamespace(usage=SimpleNamespace(completion_tokens=None, output_tokens=1400))
) == 1400
assert _observed_completion_tokens({"usage": {"completion_tokens": 353}}) == 353
assert _observed_completion_tokens(SimpleNamespace(usage=None)) is None
assert _observed_completion_tokens(SimpleNamespace()) is None
assert _observed_completion_tokens(
    SimpleNamespace(usage=SimpleNamespace(completion_tokens="junk"))
) is None

# ── one observation is an anecdote; two make a ceiling ──────────────────
# A single 371-token glitch must not become a "measured ceiling", and it
# must never install the one-shot request override (that made the retry
# truncate at 371 BY REQUEST — the 2026-08-20 field failure).
agent = SimpleNamespace()
ceiling, budget, corroborated = _note_silent_output_clamp(agent, 371)
assert (ceiling, corroborated) == (371, False)
assert not hasattr(agent, "_ephemeral_max_output_tokens")
# Second observation corroborates; the ceiling is the max seen; the
# override only installs at a workable size (>= _SILENT_CAP_MIN_EPHEMERAL).
ceiling, budget, corroborated = _note_silent_output_clamp(agent, 1686)
assert (ceiling, corroborated) == (1686, True)
assert budget == int(1686 * 0.85)
assert agent._ephemeral_max_output_tokens == 1686
assert 1686 >= _SILENT_CAP_MIN_EPHEMERAL
# A corroborated but tiny ceiling never installs the override.
tiny = SimpleNamespace()
_note_silent_output_clamp(tiny, 300)
ceiling, budget, corroborated = _note_silent_output_clamp(tiny, 310)
assert corroborated and ceiling == 310
assert not hasattr(tiny, "_ephemeral_max_output_tokens")
# Cuts below the observation floor never count at the call sites.
assert 100 < _SILENT_CAP_MIN_OBSERVATION

# ── the detection ratio distinguishes clamp from honest budget fill ─────
# gemma-class silent clamp: 1,686 observed against 32,768 requested → fires.
assert 1686 < 32768 * _SILENT_OUTPUT_CLAMP_RATIO
# a large model filling its requested budget: 32,768 of 32,768 → never fires.
assert not (32768 < 32768 * _SILENT_OUTPUT_CLAMP_RATIO)
# a self-capped reasoning burn near the budget → never fires either.
assert not (30000 < 32768 * _SILENT_OUTPUT_CLAMP_RATIO)

# ── the re-plan nudge is compressor-recognized ephemeral scaffolding ────
nudge = _silent_cap_replan_nudge(1686, 1433)
assert nudge.startswith(_LENGTH_CONTINUATION_DROPPED_TOOLS_PREFIX)
assert "1,686" in nudge and "1,433" in nudge
assert "NOT executed" in nudge and "small steps" in nudge
assert ContextCompressor._is_synthetic_compression_user_turn(
    {"role": "user", "content": nudge}
), "the pinned compressor must treat the nudge as internal, not a human turn"
# The masked-truncation variant (router rewrote finish_reason, no measured
# ceiling) keeps the same prefix and the same re-plan instruction.
masked = _silent_cap_replan_nudge()
assert masked.startswith(_LENGTH_CONTINUATION_DROPPED_TOOLS_PREFIX)
assert "cutting long responses short" in masked and "NOT executed" in masked
assert ContextCompressor._is_synthetic_compression_user_turn(
    {"role": "user", "content": masked}
)
assert not ContextCompressor._is_synthetic_compression_user_turn(
    {"role": "user", "content": "please build the dashboard"}
)

# ── retries must not stack identical nudges ─────────────────────────────
assert not _last_message_is_replan_nudge([])
assert not _last_message_is_replan_nudge([{"role": "user", "content": "hi"}])
assert _last_message_is_replan_nudge([
    {"role": "assistant", "content": "x"},
    {"role": "user", "content": _silent_cap_replan_nudge()},
])

# ── the loop-level seams left agent/conversation_loop.py in 0.21.4 ──────
# recover_from_truncation (agent/turn_truncation.py) and the router-masked twin
# in validate_tool_calls (agent/turn_tool_validation.py) are where the ceiling
# is observed and the nudged retries run. A `git apply --include=` narrowed to
# conversation_loop.py drops those hunks silently, leaving only these helpers.
import inspect

from agent import turn_tool_round, turn_tool_validation, turn_truncation

assert "_note_silent_output_clamp" in inspect.getsource(turn_truncation), \
    "silent-cap seam missing from agent/turn_truncation.py (patch hunk dropped?)"
assert "_note_silent_output_clamp" in inspect.getsource(turn_tool_validation), \
    "masked-truncation seam missing from agent/turn_tool_validation.py (patch hunk dropped?)"
assert "api_kwargs" in inspect.signature(turn_tool_round.run_tool_round).parameters, \
    "run_tool_round no longer threads response/api_kwargs to validate_tool_calls"

print("hermes-silent-output-cap: OK")
