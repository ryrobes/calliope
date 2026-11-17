"""Compose a served artifact page: host-injected head content plus the authored document.

Injected content must follow the author's doctype. Anything placed before
``<!DOCTYPE html>`` puts the browser in quirks mode, so the same artifact would
render one way in Cowork or a plain tab and another way on Calliope's hosted
routes and the Stage (white-on-white tables, different box sizing).
"""
from __future__ import annotations

import re

# A byte-order mark, whitespace and comments may precede the doctype without
# leaving standards mode, so they stay ahead of the injected content too.
_DOCTYPE_PREAMBLE = re.compile(r"\A(﻿?(?:\s|<!--.*?-->)*<!doctype\b[^>]*>)", re.I | re.S)


def with_injected_head(injected: str, document: str) -> str:
    """Place ``injected`` right after the document's doctype, or first when it has none.

    A document without a doctype renders in quirks mode wherever it is opened,
    so prepending keeps it consistent; one with a doctype keeps standards mode.
    """
    document = document or ""
    preamble = _DOCTYPE_PREAMBLE.match(document)
    if not preamble:
        return injected + document
    return preamble.group(1) + "\n" + injected + document[preamble.end():]
