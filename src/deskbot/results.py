"""The three-state result model.

Deskbot reports on data it does not control, from sources with different
coverage. The single most dangerous thing it can do is present *"we did not
check"* as *"we checked and found nothing"*. A reader skimming a report cannot
tell those apart unless the structure forces them apart.

So a source result is a discriminated union of two variants, giving three
observable states:

    Assessed    + findings      the source was queried and returned records
    Assessed    + no findings   the source was queried and there is nothing there
    NotAssessed + reason        the source was never queried, or could not be

``NotAssessed`` has no ``findings`` attribute at all. That is deliberate: it
makes the conflation unrepresentable rather than merely discouraged. Code that
reaches for ``.findings`` on a gap fails at type-check time and at runtime,
instead of quietly rendering an empty section that reads as reassurance.

Rendering must branch on ``status``. A gap must never travel through the same
code path that renders a populated section, and it must propagate to any summary
so a partial report is visible without the reader reaching the section itself.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field


class NotAssessedReason(StrEnum):
    """Why a source produced no assessment.

    Every member means "we do not know", never "there is nothing there".
    """

    OUTSIDE_COVERAGE = "outside_coverage"
    """The source does not cover this location at all.

    Environment Agency data is England-only, so a Scottish or Welsh site falls
    here. Note the EA flood endpoints answer such a query with ``count: 0``,
    which is indistinguishable from "not in a flood zone" -- hence the gate.
    """

    INSUFFICIENT_LOCATION_PRECISION = "insufficient_location_precision"
    """The location is known too coarsely for the search to mean anything.

    Searching 250 m around the centre of a 1 km grid square covers a fraction of
    the area the reference denotes, while reporting hits as though they sit near
    the site. See :mod:`deskbot.precision`.
    """

    SOURCE_UNAVAILABLE = "source_unavailable"
    """The source was queried and failed: network error, timeout, or an error
    payload.

    Note that ArcGIS services return errors under HTTP 200, so a client checking
    only status codes would land such a failure in ``Assessed`` with an empty
    findings list -- manufacturing exactly the false reassurance this model
    exists to prevent.
    """

    NOT_QUERYABLE_AT_A_POINT = "not_queryable_at_a_point"
    """The dataset cannot answer a point query, so we did not pretend to ask one.

    A limitation of our method rather than of coverage. BGS maps faults as line
    geometry, and a point query essentially never intersects a line -- verified
    across four locations including a major fault zone, all returning nothing.
    Querying anyway would report "no faults" everywhere, which is worse than
    saying we did not look.
    """

    WITHHELD_UNVERIFIED = "withheld_unverified"
    """Output was produced but could not be verified, so it was not shown.

    Used for the local reasoning model. It fabricates when synthesising -- in
    testing it turned "surface water mapped within 260 m" into "high probability
    of surface water flooding" at the site -- so its output is checked before
    release and withheld when the check fails. Withholding is a form of not
    knowing, not a finding.
    """

    NOT_REQUESTED = "not_requested"
    """The caller did not ask for this source."""


class SourceRef(BaseModel):
    """Provenance for a claim, so every statement can name where it came from."""

    model_config = ConfigDict(frozen=True)

    name: str
    """Human-readable source name, e.g. 'BGS Geology 1:50k'."""

    url: str
    """The endpoint actually called."""

    licence: str
    """Licence identifier, e.g. 'OGL v3'."""

    attribution: str
    """The attribution string the licence obliges us to reproduce verbatim."""

    retrieved_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class Assessed[T](BaseModel):
    """The source was queried successfully.

    ``findings`` may legitimately be empty, which is a real result: the source
    was consulted and there is nothing at this location. That is a different
    claim from :class:`NotAssessed` and must read differently.
    """

    model_config = ConfigDict(extra="forbid")

    status: Literal["assessed"] = "assessed"

    findings: list[T]
    source: SourceRef

    query: str | None = None
    """What was actually asked, for honest attribution.

    Where a search was widened to cover location uncertainty, this must describe
    the radius genuinely used, not the one requested.
    """

    @property
    def is_empty(self) -> bool:
        """True when the source was consulted and returned nothing.

        Distinct from a gap. Use this only for wording, never to decide whether
        the source was checked.
        """
        return not self.findings


class NotAssessed(BaseModel):
    """The source was not consulted, or could not be.

    Deliberately has no ``findings`` attribute, and forbids extras so that
    attaching one fails loudly rather than being silently dropped -- which would
    let an author believe they had recorded results on a gap.
    """

    model_config = ConfigDict(extra="forbid")

    status: Literal["not_assessed"] = "not_assessed"

    reason: NotAssessedReason
    detail: str
    """One sentence a reader can act on, naming the limitation.

    Good: 'Environment Agency flood data covers England only; this site is in
    Scotland. See SEPA.' Bad: 'No data.'
    """

    source: SourceRef | None = None
    """The source that *would* have been consulted, where one is known.

    Populated even though nothing was retrieved, so a gap can still say which
    dataset is missing rather than appearing as an unexplained hole.
    """


type SourceResult[T] = Annotated[
    Assessed[T] | NotAssessed,
    Field(discriminator="status"),
]
"""A source result: either an assessment, or an explained absence of one."""


def not_assessed(
    reason: NotAssessedReason,
    detail: str,
    source: SourceRef | None = None,
) -> NotAssessed:
    """Build a gap. Present mainly to keep the reason and detail together."""
    return NotAssessed(reason=reason, detail=detail, source=source)
