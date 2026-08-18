"""The local reasoning model, confined to advice.

Deskbot uses qwen2.5:7b through Ollama. Nothing leaves the machine: the model is
local, and no site data reaches any third-party service.

**The model never states a fact.** Every finding, gap and caveat in a Deskbot
report is rendered deterministically from the Pydantic models. The model's only
job is to suggest what a Phase 1 investigation would need to look at next. That
is advisory by nature, so a fabrication there is a bad suggestion rather than a
false fact.

This is not caution for its own sake. Given the facts plainly and asked to
summarise, qwen2.5:7b was observed to:

* **relocate a finding.** "Surface water flooding mapped within 260 m" became
  "There is a high probability of surface water flooding at 3.3%" at the site,
  and the conclusion then built on it.
* **invent a mechanism.** "London Clay Formation and Kempton Park Gravel Member
  could exacerbate flood impacts due to their permeability characteristics" is
  unsupported, and London Clay is not permeable.

Both came from a prompt that stated the facts clearly, which is why instruction
alone is not the control. Output is checked before release and withheld when the
check fails.

The check has two halves:

* **Grounding.** Every number in the output must appear in the facts the model
  was given. A figure it invented is a figure it cannot support.
* **Absence claims.** Phrases such as "no risk" or "not at risk" are exactly how
  an unassessed gap becomes false comfort. The model may never originate one. A
  phrase already present in the facts is quotation and is allowed; the same
  phrase absent from the facts is the model's own words and is not.
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime
from typing import Final, Literal

import httpx
from pydantic import BaseModel, ConfigDict

from deskbot.results import (
    Assessed,
    NotAssessed,
    NotAssessedReason,
    SourceRef,
    SourceResult,
    not_assessed,
)

logger = logging.getLogger("deskbot.advisor")

DEFAULT_HOST: Final = "http://127.0.0.1:11434"
"""Literal loopback rather than 'localhost': name resolution can pick IPv6 where
Ollama is listening on IPv4 only."""

DEFAULT_MODEL: Final = "qwen2.5:7b"
_TIMEOUT_S: Final = 300.0

SYSTEM_PROMPT: Final = """\
You advise on preliminary desk studies for UK sites. You are given findings that
have ALREADY been established and written up by other tooling.

Your ONLY task is to list what a Phase 1 desk study should investigate next.

Rules, without exception:
- Do NOT restate, summarise or reword the findings. They are already reported.
- Do NOT state any fact, measurement, figure or conclusion of your own.
- Do NOT say anything is safe, unsafe, low risk, high risk, or at no risk.
- Do NOT treat something mapped NEAR the site as being AT the site.
- Do NOT speculate about causes, mechanisms or ground behaviour.
- Where something was NOT ASSESSED, you may suggest obtaining it. Never infer
  what it would have shown.

Reply with 3 to 6 short suggestions, one per line, each starting "- ".
Each must be an action: something to obtain, check, commission or confirm.
No preamble, no conclusion, no headings.
"""

_ABSENCE_CLAIMS: Final = (
    "no risk",
    "not at risk",
    "no evidence",
    "there is no",
    "poses no",
    "free from",
    "clear of",
    "safe from",
    "no significant",
    "no issues",
    "no concern",
    "nothing of concern",
    "unaffected by",
    "no flood",
    "no contamination",
    "is safe",
    "suitable for development",
    "no further investigation",
)
"""Phrases the model must never originate.

Checked only when the phrase does not already appear in the supplied facts: a
phrase present there is quotation, the same phrase absent is invention. That
matters because dataset labels legitimately contain wording such as
"high risk (3.3% annual chance)".
"""

_LIST_MARKER: Final = re.compile(r"^\s*[-*•]?\s*\d+[.)]\s+", re.MULTILINE)
_NUMBER: Final = re.compile(r"\d+(?:\.\d+)?")


class Violation(BaseModel):
    """Something the model produced that it is not permitted to produce."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["ungrounded_number", "absence_claim", "empty"]
    detail: str


def advisor_source(model: str, host: str) -> SourceRef:
    return SourceRef(
        name=f"Local model {model} via Ollama",
        url=host,
        licence="Not a data source; generated locally",
        attribution=(
            f"Suggestions generated locally by {model}. Advisory only: not a "
            "finding, not attributable to any dataset, and not verified."
        ),
    )


def _numbers(text: str) -> set[str]:
    """Numeric tokens, ignoring list ordinals.

    "2." at the start of a line is formatting, not a claim, so stripping markers
    first avoids flagging a numbered list as ungrounded.
    """
    return set(_NUMBER.findall(_LIST_MARKER.sub("", text)))


def check(text: str, facts: str) -> list[Violation]:
    """Return every reason the output must not be shown, or an empty list."""
    violations: list[Violation] = []

    if not text.strip():
        return [Violation(kind="empty", detail="the model returned nothing")]

    grounded = _numbers(facts)
    ungrounded = sorted(n for n in _numbers(text) if n not in grounded)
    if ungrounded:
        violations.append(
            Violation(
                kind="ungrounded_number",
                detail=("figures not present in the findings: " + ", ".join(ungrounded)),
            )
        )

    lowered = text.lower()
    lowered_facts = facts.lower()
    claimed = [
        phrase for phrase in _ABSENCE_CLAIMS if phrase in lowered and phrase not in lowered_facts
    ]
    if claimed:
        violations.append(
            Violation(
                kind="absence_claim",
                detail="asserted an absence of risk: " + ", ".join(repr(c) for c in claimed),
            )
        )

    return violations


def _parse_suggestions(text: str) -> list[str]:
    suggestions: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        stripped = re.sub(r"^[-*•]\s*", "", stripped)
        stripped = re.sub(r"^\d+[.)]\s*", "", stripped)
        if stripped and not stripped.endswith(":"):
            suggestions.append(stripped)
    return suggestions


def suggest(
    facts: str,
    *,
    model: str = DEFAULT_MODEL,
    host: str = DEFAULT_HOST,
    client: httpx.Client | None = None,
) -> SourceResult[str]:
    """Ask the local model what to investigate next.

    Returns suggestions, or a gap explaining why there are none. An unreachable
    Ollama is a gap, not a silent omission: the same treatment applied to every
    other source, applied here to our own component.
    """
    source = advisor_source(model, host)

    owns_client = client is None
    client = client or httpx.Client()
    try:
        response = client.post(
            f"{host}/api/chat",
            json={
                "model": model,
                "stream": False,
                "options": {"temperature": 0.2},
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": facts},
                ],
            },
            timeout=_TIMEOUT_S,
        )
        response.raise_for_status()
        text = response.json()["message"]["content"]
    except (httpx.HTTPError, KeyError, ValueError) as exc:
        logger.info("advisor unavailable: %s", exc)
        return not_assessed(
            NotAssessedReason.SOURCE_UNAVAILABLE,
            (
                f"The local model ({model}) could not be reached at {host}, so no "
                "suggestions were generated. Every finding above is unaffected: "
                "they are produced without the model."
            ),
            source,
        )
    finally:
        if owns_client:
            client.close()

    violations = check(text, facts)
    if violations:
        # Logged so the rate is measurable rather than anecdotal. See
        # scripts/measure_advisor.py.
        for violation in violations:
            logger.warning("advisor output withheld (%s): %s", violation.kind, violation.detail)
        return not_assessed(
            NotAssessedReason.WITHHELD_UNVERIFIED,
            (
                "Suggestions were generated but withheld because they failed "
                "verification: "
                + "; ".join(v.detail for v in violations)
                + ". The model is advisory only and its output is discarded when "
                "it cannot be grounded in the findings."
            ),
            source,
        )

    suggestions = _parse_suggestions(text)
    if not suggestions:
        return not_assessed(
            NotAssessedReason.WITHHELD_UNVERIFIED,
            "The model returned no usable suggestions.",
            source,
        )

    return Assessed[str](
        findings=suggestions,
        source=source,
        query=f"{model} at temperature 0.2, {datetime.now(UTC).date().isoformat()}",
    )


__all__ = [
    "DEFAULT_HOST",
    "DEFAULT_MODEL",
    "SYSTEM_PROMPT",
    "Assessed",
    "NotAssessed",
    "Violation",
    "advisor_source",
    "check",
    "suggest",
]
