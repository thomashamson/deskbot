"""Ground level and local relief, from the EA LIDAR Composite 1 m DTM.

Bare-earth terrain, England only, Open Government Licence.

**This reverses decision 10.2.** That decision chose the DEFRA WMS over the
ArcGIS ImageServer for its durable ``environment.data.gov.uk`` URL, and recorded
"revisit if the WMS proves unreliable". It does:

* The WMS rate-limits to roughly one request per second and answers with a bare
  ``403``, not a ``429``. Measured: a burst of twelve returned one success and
  eleven failures. A throttled response is easy to mistake for absent data.
* It returns one point per request, so any sampling multiplies the problem.
* Its out-of-coverage answer is an empty feature array, indistinguishable from
  other empties.

The ImageServer ran a burst of eight cleanly, exposes ``getSamples`` so an entire
ring costs a single request, says ``NoData`` explicitly, and carries provenance:
which tile, what resolution, which return, which composite year.

The cost is a proxied ``utility.arcgis.com/usrsvcs/<hash>/`` URL, which is the
fragility that motivated 10.2 in the first place. The WMS remains documented as
a fallback if that path ever breaks.

**Elevation alone is thin**, so a ring is sampled around the point. Its radius is
``max(50 m, location uncertainty)``, doing two jobs at once: at 50 m it
characterises local slope, and for a coarse grid reference it widens to say how
much ground level could vary across the area the reference denotes. Which of the
two applied is reported, so the range is never read as the wrong thing.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any, Final

import httpx
from pydantic import BaseModel, ConfigDict

from deskbot.locate import Location
from deskbot.precision import resolve_search_radius
from deskbot.results import (
    Assessed,
    NotAssessed,
    NotAssessedReason,
    SourceRef,
    SourceResult,
    not_assessed,
)

_IMAGE_SERVER: Final = (
    "https://utility.arcgis.com/usrsvcs/servers/f9c4694d7d5140638536c4afe4119e6d"
    "/rest/services/LIDAR/LIDAR_Composite_1m_DTM/ImageServer"
)
_TIMEOUT_S: Final = 45.0

RING_BASE_M: Final = 50.0
"""Minimum ring radius, for characterising slope at a precisely located site."""

_RING: Final = (
    (0.0, 1.0),
    (0.7071, 0.7071),
    (1.0, 0.0),
    (0.7071, -0.7071),
    (0.0, -1.0),
    (-0.7071, -0.7071),
    (-1.0, 0.0),
    (-0.7071, 0.7071),
)
"""Eight compass points. Opposite pairs are four apart, which the gradient uses."""

_NO_DATA: Final = "NoData"

SAMPLING_CAVEAT: Final = (
    "This is eight sample points around the location, not a survey of it: a "
    "steep face between samples would be missed, and the same range could be one "
    "scarp or gentle undulation. Treat as a screening indication."
)
"""Always stated alongside relief.

The figures describe the sampled points, not the site. Without this a reader
takes "range 12 m" as characterising the ground they intend to build on, when it
characterises a ring drawn around it.
"""


def lidar_source() -> SourceRef:
    return SourceRef(
        name="EA LIDAR Composite Digital Terrain Model, 1 m",
        url=_IMAGE_SERVER,
        licence="OGL v3",
        attribution=(
            "© Environment Agency copyright and/or database right "
            f"{datetime.now(UTC).year}. All rights reserved."
        ),
    )


class GroundLevel(BaseModel):
    """Bare-earth ground level at the query point."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    elevation_m: float
    """Metres above Ordnance Datum, from a bare-earth model.

    Not a surface height: buildings and vegetation are removed. Coarse global
    products such as EU-DEM report roughly 8 m higher here because they include
    structures, and the two must never be mixed.
    """

    easting: int
    northing: int

    tile: str | None = None
    resolution: str | None = None
    model_type: str | None = None
    survey_return: str | None = None
    composite_year: int | None = None

    def describe(self) -> str:
        return f"Ground level {self.elevation_m:.2f} m AOD (bare earth, 1 m LIDAR)."


class Relief(BaseModel):
    """How the ground varies around the point."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    ring_radius_m: float
    points_requested: int
    points_sampled: int
    """Fewer than requested means some ring points have no LIDAR coverage."""

    min_m: float
    max_m: float
    max_gradient: float
    """Steepest fall across the ring, as a fraction (0.05 is 1 in 20)."""

    widened_for_uncertainty: bool
    """True when the ring was expanded past 50 m to cover location uncertainty."""

    @property
    def range_m(self) -> float:
        return self.max_m - self.min_m

    def describe(self) -> str:
        if self.widened_for_uncertainty:
            # The ring covers where the site might be, not the site. A gradient
            # here would divide a real height difference by a kilometre of
            # separation and report a 15 m spread as "effectively level".
            text = (
                f"Ground ranges {self.min_m:.1f} to {self.max_m:.1f} m AOD across the "
                f"{self.ring_radius_m:.0f} m the location could lie within, so ground "
                f"level at the site is uncertain by up to {self.range_m:.1f} m. No site "
                "gradient can be inferred at this location precision."
            )
        else:
            text = (
                f"Ground ranges {self.min_m:.1f} to {self.max_m:.1f} m AOD within "
                f"{self.ring_radius_m:.0f} m (a range of {self.range_m:.1f} m)."
            )
            if self.max_gradient >= 0.01:
                text += (
                    f" Steepest gradient about {self.max_gradient * 100:.0f}% "
                    f"(1 in {1 / self.max_gradient:.0f})."
                )
            else:
                text += " Effectively level."
        if self.points_sampled < self.points_requested:
            missing = self.points_requested - self.points_sampled
            text += (
                f" {missing} of {self.points_requested} sample points have no LIDAR "
                "coverage, so the range may be incomplete."
            )
        return f"{text} {SAMPLING_CAVEAT}"


class TerrainReport(BaseModel):
    """Ground level and relief at a location."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    ground_level: SourceResult[GroundLevel]
    relief: Relief | None = None

    def describe(self) -> str:
        if isinstance(self.ground_level, NotAssessed):
            return self.ground_level.detail
        if not self.ground_level.findings:
            return "No ground level recorded."
        parts = [self.ground_level.findings[0].describe()]
        if self.relief is not None:
            parts.append(self.relief.describe())
        return " ".join(parts)


def _get(client: httpx.Client, url: str, params: dict[str, Any]) -> dict[str, Any]:
    """Query, treating an ArcGIS error payload as a failure.

    The ImageServer returns errors under HTTP 200, and a throttled or rejected
    request must never be read as flat ground.
    """
    response = client.get(url, params=params, timeout=_TIMEOUT_S)
    response.raise_for_status()
    payload = response.json()
    if "error" in payload:
        raise ValueError(str(payload["error"].get("message", "unknown ArcGIS error")))
    return payload


def _identify(client: httpx.Client, easting: int, northing: int) -> GroundLevel | None:
    """Ground level with provenance, or ``None`` where there is no coverage."""
    payload = _get(
        client,
        f"{_IMAGE_SERVER}/identify",
        {
            "geometry": json.dumps(
                {"x": easting, "y": northing, "spatialReference": {"wkid": 27700}}
            ),
            "geometryType": "esriGeometryPoint",
            "returnGeometry": "false",
            "f": "json",
        },
    )
    raw = payload.get("value")
    if raw is None or raw == _NO_DATA:
        return None
    try:
        elevation = float(raw)
    except (TypeError, ValueError):
        return None

    items = (payload.get("catalogItems") or {}).get("features") or []
    attributes = items[0].get("attributes", {}) if items else {}
    raw_year = attributes.get("CompYear")
    year = (
        datetime.fromtimestamp(raw_year / 1000, UTC).year
        if isinstance(raw_year, int | float)
        else None
    )

    return GroundLevel(
        elevation_m=elevation,
        easting=easting,
        northing=northing,
        tile=attributes.get("Name"),
        resolution=attributes.get("LIDARRes"),
        model_type=attributes.get("ModelType"),
        survey_return=attributes.get("LIDARRtn"),
        composite_year=year,
    )


def _ring_points(easting: int, northing: int, radius_m: float) -> list[tuple[int, int]]:
    return [(round(easting + dx * radius_m), round(northing + dy * radius_m)) for dx, dy in _RING]


def _sample_ring(
    client: httpx.Client, points: list[tuple[int, int]]
) -> dict[tuple[int, int], float]:
    """Sample every ring point in one request.

    ``getSamples`` silently omits points with no coverage rather than returning
    NoData for them, so results are matched back by location instead of by
    position in the list.
    """
    payload = _get(
        client,
        f"{_IMAGE_SERVER}/getSamples",
        {
            "geometry": json.dumps(
                {"points": [list(p) for p in points], "spatialReference": {"wkid": 27700}}
            ),
            "geometryType": "esriGeometryMultipoint",
            "returnFirstValueOnly": "true",
            "f": "json",
        },
    )

    found: dict[tuple[int, int], float] = {}
    for sample in payload.get("samples") or []:
        location = sample.get("location") or {}
        value = sample.get("value")
        if value in (None, _NO_DATA):
            continue
        try:
            key = (round(float(location["x"])), round(float(location["y"])))
            found[key] = float(value)
        except (KeyError, TypeError, ValueError):
            continue
    return found


def _relief(
    centre: float,
    points: list[tuple[int, int]],
    sampled: dict[tuple[int, int], float],
    radius_m: float,
    widened: bool,
) -> Relief | None:
    values = [sampled[p] for p in points if p in sampled]
    if not values:
        return None

    heights = [*values, centre]
    gradient = 0.0
    half = len(points) // 2
    for index in range(half):
        near, far = points[index], points[index + half]
        if near in sampled and far in sampled:
            gradient = max(gradient, abs(sampled[near] - sampled[far]) / (2 * radius_m))

    return Relief(
        ring_radius_m=radius_m,
        points_requested=len(points),
        points_sampled=len(values),
        min_m=min(heights),
        max_m=max(heights),
        max_gradient=gradient,
        widened_for_uncertainty=widened,
    )


def terrain(location: Location, *, client: httpx.Client | None = None) -> TerrainReport:
    """Look up ground level and local relief at ``location``.

    England only: the LIDAR composite does not cover Scotland, Wales or Northern
    Ireland, and the country is checked before anything is requested.
    """
    source = lidar_source()

    gap = location.england_only_gap(source, "The Environment Agency LIDAR terrain model")
    if gap is not None:
        return TerrainReport(ground_level=gap)

    # The gate only decides whether the location is precise enough to say
    # anything. The ring radius itself is max(base, uncertainty): a precise site
    # gets a clean 50 m ring for slope, a coarse one gets a ring covering where
    # the site could actually be.
    gate = resolve_search_radius(
        RING_BASE_M,
        location.precision_m,
        basis=location.precision_basis,
        source=source,
    )
    if isinstance(gate, NotAssessed):
        return TerrainReport(ground_level=gate)
    ring_radius_m = max(RING_BASE_M, location.precision_m)

    owns_client = client is None
    client = client or httpx.Client()
    try:
        try:
            ground = _identify(client, location.easting, location.northing)
        except (httpx.HTTPError, ValueError) as exc:
            return TerrainReport(
                ground_level=not_assessed(
                    NotAssessedReason.SOURCE_UNAVAILABLE,
                    f"The LIDAR terrain service could not be reached: {exc}",
                    source,
                ),
            )

        if ground is None:
            return TerrainReport(
                ground_level=not_assessed(
                    NotAssessedReason.OUTSIDE_COVERAGE,
                    (
                        "The LIDAR composite records no data at this point. Coverage "
                        "is England only and is not quite complete within it, so this "
                        "means ground level is unknown here rather than that the "
                        "ground is at zero."
                    ),
                    source,
                ),
            )

        points = _ring_points(location.easting, location.northing, ring_radius_m)
        try:
            sampled = _sample_ring(client, points)
        except (httpx.HTTPError, ValueError):
            # The ground level stands on its own; relief is the optional extra.
            sampled = {}

        relief = _relief(
            ground.elevation_m,
            points,
            sampled,
            ring_radius_m,
            widened=ring_radius_m > RING_BASE_M,
        )

        return TerrainReport(
            ground_level=Assessed[GroundLevel](
                findings=[ground],
                source=source,
                query=(
                    f"point sample at E{location.easting} N{location.northing}"
                    + (f", with a {ring_radius_m:.0f} m ring" if relief else "")
                ),
            ),
            relief=relief,
        )
    finally:
        if owns_client:
            client.close()
