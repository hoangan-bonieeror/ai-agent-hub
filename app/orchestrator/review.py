"""Reviewer verdicts: what a review step asks for and how its reply is read."""

from __future__ import annotations

import json
import re
from typing import Any

REVIEW_FORMAT = (
    "Reply with a JSON object: "
    '{"approved": true or false, "feedback": "what must change, written to the authors of '
    'the reviewed outputs"}. You may add fields, such as "summary". Approve when the '
    "outputs are correct and complete enough to use."
)

_FENCE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


def parse_verdict(output: Any) -> dict[str, Any] | None:
    """Return the verdict dict when ``output`` holds one, else None.

    Accepts a dict, a JSON string, a fenced ```json block, or text that ends
    with a JSON object. A verdict needs a boolean ``approved``.
    """
    candidates: list[Any] = []
    if isinstance(output, dict):
        candidates.append(output)
    elif isinstance(output, str):
        text = output.strip()
        candidates.append(text)
        candidates.extend(m.group(1) for m in _FENCE.finditer(text))
        start, end = text.find("{"), text.rfind("}")
        if 0 <= start < end:
            candidates.append(text[start : end + 1])
    for candidate in candidates:
        data = candidate
        if isinstance(candidate, str):
            try:
                data = json.loads(candidate)
            except ValueError:
                continue
        if isinstance(data, dict) and isinstance(data.get("approved"), bool):
            return data
    return None
