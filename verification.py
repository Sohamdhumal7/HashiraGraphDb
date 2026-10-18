"""Deterministic grounding check: every named entity in an answer must appear in the evidence.

Scope (stated honestly): this verifies *named entities* (services, teams, incident and
decision IDs) against the retrieved rows. It is not a full semantic fact-checker.
"""
from __future__ import annotations

import json
import re
from typing import Any

from seed_graph import SERVICES, TEAMS

KNOWN_NAMES = tuple([name for name, *_ in SERVICES] + list(TEAMS))
ID_PATTERN = re.compile(r"\b(?:INC|ADR)-\d+\b", re.IGNORECASE)


def _mentions(text: str) -> set[str]:
    found = {n for n in KNOWN_NAMES if re.search(rf"(?<!\w){re.escape(n)}(?!\w)", text, re.IGNORECASE)}
    return found | {m.upper() for m in ID_PATTERN.findall(text)}


def verify_answer(answer: str, evidence: list[dict[str, Any]]) -> dict[str, Any]:
    if not evidence:
        return {"status": "no_evidence", "supported": [], "unsupported": sorted(_mentions(answer))}
    blob = json.dumps(evidence, default=str, ensure_ascii=False)
    supported_in_evidence = _mentions(blob)
    named = _mentions(answer)
    unsupported = sorted(n for n in named if n not in supported_in_evidence)
    return {"status": "unsupported_claims" if unsupported else "verified",
            "supported": sorted(named - set(unsupported)), "unsupported": unsupported}
