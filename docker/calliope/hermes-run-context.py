"""First-party task provenance, scoped to one /v1/runs execution.

Unlike the conversation ID this changes on every turn. It is never read from
process environment or exposed as a model-supplied tool argument.
"""
from contextlib import contextmanager
from contextvars import ContextVar

_run_id = ContextVar("calliope_run_id", default="")


def current_run_id():
    return _run_id.get()


@contextmanager
def bind_run_id(run_id):
    token = _run_id.set(str(run_id))
    try:
        yield
    finally:
        _run_id.reset(token)
