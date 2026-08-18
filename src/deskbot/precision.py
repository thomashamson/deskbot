"""Reconciling a requested search radius with how well the location is known.

A radius search is only meaningful relative to a point. If the point itself is
uncertain by more than the radius, the search silently stops answering the
question that was asked.

Searching 250 m around the centre of a 1 km grid square covers roughly a fifth
of the area the reference denotes, and the true site may be 700 m from anything
found. Reporting those hits as "within 250 m of the site" is false precision of
the same family as reporting an unchecked flood zone as "no risk": the number
looks authoritative and is not.

So the radius is widened to cover the uncertainty, and the resulting claim is
restated in terms of what was genuinely searched. Where widening would push the
search past a useful limit, the search is refused as
:attr:`~deskbot.results.NotAssessedReason.INSUFFICIENT_LOCATION_PRECISION`
rather than answered misleadingly.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from deskbot.results import NotAssessed, NotAssessedReason, SourceRef, not_assessed

DEFAULT_CEILING_M = 1000.0
"""Largest effective radius worth running, in metres.

Not arbitrary. BGS caps responses at 2000 records and central London already
returns 1109 boreholes within 1 km, so a wider search risks silent truncation --
trading one false precision for another. It is also the point beyond which
"near the site" stops meaning much in a desk study.
"""

_MATERIAL_WIDENING_M = 1.0
"""Below this, widening is noise and not worth reporting."""


class SearchRadius(BaseModel):
    """A search radius that accounts for how well the location is known."""

    model_config = ConfigDict(frozen=True)

    requested_m: float
    location_uncertainty_m: float
    effective_m: float
    """The radius to actually query with."""

    @property
    def widened(self) -> bool:
        return self.effective_m - self.requested_m >= _MATERIAL_WIDENING_M

    def claim(self, basis: str) -> str:
        """How the result may honestly be described.

        When widened, the claim is about the query point, not the site: we can
        say nothing was missed within the requested distance of the true
        location, but not that what we found sits that close to it.
        """
        if not self.widened:
            return f"within {self.requested_m:.0f} m"
        return (
            f"within {self.effective_m:.0f} m of the {basis}, widened from "
            f"{self.requested_m:.0f} m to cover a location uncertainty of "
            f"{self.location_uncertainty_m:.0f} m"
        )


def resolve_search_radius(
    requested_m: float,
    uncertainty_m: float,
    *,
    basis: str,
    ceiling_m: float = DEFAULT_CEILING_M,
    source: SourceRef | None = None,
) -> SearchRadius | NotAssessed:
    """Widen ``requested_m`` to cover ``uncertainty_m``, or refuse.

    Args:
        requested_m: The radius the caller asked for.
        uncertainty_m: Radius within which the true location lies. For a grid
            reference this is the half-diagonal of the denoted square; for a
            postcode it derives from the OS positional quality indicator.
        basis: Short phrase naming what the coordinate represents, e.g.
            '1 km grid square centre'. Used in the restated claim.
        ceiling_m: Largest effective radius to allow before refusing.
        source: Attached to the gap so it can name the dataset not consulted.

    Returns:
        A :class:`SearchRadius` to query with, or a :class:`NotAssessed` gap.
    """
    if requested_m <= 0:
        raise ValueError(f"requested_m must be positive, got {requested_m}")
    if uncertainty_m < 0:
        raise ValueError(f"uncertainty_m cannot be negative, got {uncertainty_m}")

    effective_m = requested_m + uncertainty_m

    if effective_m > ceiling_m:
        return not_assessed(
            NotAssessedReason.INSUFFICIENT_LOCATION_PRECISION,
            (
                f"The location is only known to within {uncertainty_m:.0f} m "
                f"({basis}). Covering a {requested_m:.0f} m search would need a "
                f"radius of {effective_m:.0f} m, beyond the {ceiling_m:.0f} m "
                "limit. A narrower search would report results as though they "
                "were near the site when they need not be. Supply a more "
                "precise location to assess this."
            ),
            source,
        )

    return SearchRadius(
        requested_m=requested_m,
        location_uncertainty_m=uncertainty_m,
        effective_m=effective_m,
    )
