"""Flood risk at a point, from Environment Agency published extents.

Sources, both OGL v3 and both queried in EPSG:27700:

* Flood Map for Planning (Rivers and Sea) -- Flood Zones 2 and 3, and flood
  storage areas.
* Risk of Flooding from Surface Water -- three annual-chance extents.

**England only.** This is the module where that matters most. Both services
answer an out-of-area query with ``count: 0``, which is indistinguishable from
"not in a flood zone". A Scottish site would therefore read as "no flood risk"
unless the country is checked first, so the gate runs before any request is made.

**Flood Zones ignore flood defences.** This is not a caveat we invented: the
Flood Map for Planning deliberately shows the undefended floodplain, because it
exists to drive planning policy rather than to estimate residual risk. Southwark
is in Flood Zone 3 and sits behind the Thames Barrier. The label therefore names
what the zone actually is, so the qualification survives being quoted out of
context.

**Proximity is reported alongside presence.** A point can sit outside every
extent while flooding is mapped 30 m away. Southwark has no surface water extent
at the point, none within 50 m, and 32 within 250 m. Reporting only the point
would render that site as "not at risk".
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from enum import StrEnum
from typing import Any, Final

import httpx
from pydantic import BaseModel, ConfigDict

from deskbot.locate import Location
from deskbot.precision import SearchRadius, resolve_search_radius
from deskbot.results import (
    Assessed,
    NotAssessed,
    NotAssessedReason,
    SourceRef,
    SourceResult,
    not_assessed,
)

_FMP_URL: Final = (
    "https://services1.arcgis.com/JZM7qJpmv7vJ0Hzx/arcgis/rest/services"
    "/Flood_Map_for_Planning/FeatureServer"
)
_SW_URL: Final = (
    "https://services1.arcgis.com/JZM7qJpmv7vJ0Hzx/arcgis/rest/services"
    "/Risk_of_Flooding_from_Surface_Water_Extents/FeatureServer"
)
_TIMEOUT_S: Final = 45.0

DEFAULT_RADIUS_M: Final = 250.0
"""Matches the borehole search, so one location produces one consistent radius."""

DEFENCES_CAVEAT: Final = (
    "Flood Zones show the undefended floodplain: the Flood Map for Planning "
    "deliberately ignores flood defences, so a site inside a zone may be "
    "protected in practice, and one outside it is not necessarily safe. This is "
    "a planning classification, not an assessment of residual risk."
)


class FloodDataset(StrEnum):
    FLOOD_ZONE_2 = "flood_zone_2"
    FLOOD_ZONE_3 = "flood_zone_3"
    FLOOD_STORAGE_AREA = "flood_storage_area"
    SURFACE_WATER_HIGH = "surface_water_high"
    SURFACE_WATER_MEDIUM = "surface_water_medium"
    SURFACE_WATER_LOW = "surface_water_low"


class _Layer(BaseModel):
    model_config = ConfigDict(frozen=True)

    base_url: str
    layer_id: int
    label: str
    definition: str
    ignores_defences: bool = False


_LAYERS: Final[dict[FloodDataset, _Layer]] = {
    FloodDataset.FLOOD_ZONE_3: _Layer(
        base_url=_FMP_URL,
        layer_id=1,
        label="Flood Zone 3 (undefended floodplain extent)",
        definition=(
            "Land with a 1% or greater annual chance of flooding from rivers, or "
            "0.5% or greater from the sea, ignoring defences."
        ),
        ignores_defences=True,
    ),
    FloodDataset.FLOOD_ZONE_2: _Layer(
        base_url=_FMP_URL,
        layer_id=2,
        label="Flood Zone 2 (undefended floodplain extent)",
        definition=(
            "Land with between a 1% and 0.1% annual chance of flooding from "
            "rivers, or between 0.5% and 0.1% from the sea, ignoring defences."
        ),
        ignores_defences=True,
    ),
    FloodDataset.FLOOD_STORAGE_AREA: _Layer(
        base_url=_FMP_URL,
        layer_id=0,
        label="Flood storage area",
        definition=(
            "An area engineered to store flood water deliberately. Only 509 exist "
            "in England, so a site within one is unusual and significant."
        ),
    ),
    FloodDataset.SURFACE_WATER_HIGH: _Layer(
        base_url=_SW_URL,
        layer_id=0,
        label="Surface water flooding, high risk (3.3% annual chance)",
        definition="Mapped extent of surface water flooding with a 1 in 30 annual chance.",
    ),
    FloodDataset.SURFACE_WATER_MEDIUM: _Layer(
        base_url=_SW_URL,
        layer_id=1,
        label="Surface water flooding, medium risk (1% annual chance)",
        definition="Mapped extent of surface water flooding with a 1 in 100 annual chance.",
    ),
    FloodDataset.SURFACE_WATER_LOW: _Layer(
        base_url=_SW_URL,
        layer_id=2,
        label="Surface water flooding, low risk (0.1% annual chance)",
        definition="Mapped extent of surface water flooding with a 1 in 1000 annual chance.",
    ),
}

PLANNING_DATASETS: Final = (
    FloodDataset.FLOOD_ZONE_3,
    FloodDataset.FLOOD_ZONE_2,
    FloodDataset.FLOOD_STORAGE_AREA,
)
SURFACE_WATER_DATASETS: Final = (
    FloodDataset.SURFACE_WATER_HIGH,
    FloodDataset.SURFACE_WATER_MEDIUM,
    FloodDataset.SURFACE_WATER_LOW,
)


def flood_map_source() -> SourceRef:
    return SourceRef(
        name="EA Flood Map for Planning (Rivers and Sea)",
        url=_FMP_URL,
        licence="OGL v3",
        attribution=(
            "© Environment Agency copyright and/or database right "
            f"{datetime.now(UTC).year}. All rights reserved."
        ),
    )


def surface_water_source() -> SourceRef:
    return SourceRef(
        name="EA Risk of Flooding from Surface Water",
        url=_SW_URL,
        licence="OGL v3",
        attribution=(
            "© Environment Agency copyright and/or database right "
            f"{datetime.now(UTC).year}. All rights reserved."
        ),
    )


class FloodPresence(BaseModel):
    """One dataset's answer for this location.

    Always produced for every dataset consulted, including those with nothing
    here. A dataset that found nothing must stay visible: dropping it would make
    "checked, clear" indistinguishable from "never checked".
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    dataset: FloodDataset
    label: str
    definition: str
    ignores_defences: bool = False

    at_point: bool
    """Whether the query point falls inside the mapped extent."""

    within_radius: int | None = None
    """Extents within the search radius, or ``None`` when not counted.

    Not counted when the extent already covers the point, since presence at the
    point is the stronger statement.
    """

    source_types: tuple[str, ...] = ()
    """Flood source where given, e.g. 'Tidal Models', 'Fluvial Models'."""

    published: date | None = None

    @property
    def nearby(self) -> bool:
        """Mapped within the search radius but not at the point itself."""
        return not self.at_point and bool(self.within_radius)

    def describe(self) -> str:
        if self.at_point:
            detail = f"{self.label}: present at this location"
            if self.source_types:
                detail += f" ({', '.join(self.source_types)})"
            return detail + "."
        if self.nearby:
            return f"{self.label}: not at this location, but mapped within the search radius."
        return f"{self.label}: not mapped at or near this location."


class FloodReport(BaseModel):
    """Flood risk at a location, from two Environment Agency datasets."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    planning: SourceResult[FloodPresence]
    surface_water: SourceResult[FloodPresence]

    search: SearchRadius | None = None

    def _all(self) -> list[FloodPresence]:
        found: list[FloodPresence] = []
        for result in (self.planning, self.surface_water):
            if isinstance(result, Assessed):
                found.extend(result.findings)
        return found

    @property
    def at_point(self) -> tuple[FloodPresence, ...]:
        return tuple(p for p in self._all() if p.at_point)

    @property
    def nearby_only(self) -> tuple[FloodPresence, ...]:
        return tuple(p for p in self._all() if p.nearby)

    @property
    def assessed(self) -> bool:
        return isinstance(self.planning, Assessed) or isinstance(self.surface_water, Assessed)

    def describe(self) -> str:
        """A summary that cannot state a zone without its qualification."""
        if not self.assessed:
            gap = self.planning if isinstance(self.planning, NotAssessed) else self.surface_water
            return gap.detail if isinstance(gap, NotAssessed) else "Not assessed."

        lines: list[str] = []
        here = self.at_point
        near = self.nearby_only

        if here:
            lines.append("At this location: " + "; ".join(p.label for p in here) + ".")
        else:
            lines.append("No mapped flood extent covers this location.")

        if near:
            radius = f"{self.search.effective_m:.0f} m" if self.search else "the search radius"
            lines.append(f"Mapped within {radius}: " + "; ".join(p.label for p in near) + ".")

        if any(p.ignores_defences for p in (*here, *near)):
            lines.append(DEFENCES_CAVEAT)

        return " ".join(lines)


def _published(value: object) -> date | None:
    """ArcGIS dates arrive as epoch milliseconds."""
    if not isinstance(value, int | float):
        return None
    try:
        return datetime.fromtimestamp(value / 1000, UTC).date()
    except (OverflowError, OSError, ValueError):
        return None


def _geometry(location: Location) -> str:
    return f'{{"x":{location.easting},"y":{location.northing},"spatialReference":{{"wkid":27700}}}}'


def _get(client: httpx.Client, url: str, params: dict[str, Any]) -> dict[str, Any]:
    """Query, treating an ArcGIS error payload as a failure.

    These services return errors under HTTP 200, so a status check alone would
    turn a failed flood lookup into a clean bill of health.
    """
    response = client.get(url, params=params, timeout=_TIMEOUT_S)
    response.raise_for_status()
    payload = response.json()
    if "error" in payload:
        raise ValueError(str(payload["error"].get("message", "unknown ArcGIS error")))
    return payload


def _presence(
    client: httpx.Client, dataset: FloodDataset, location: Location, radius_m: float
) -> FloodPresence:
    layer = _LAYERS[dataset]
    url = f"{layer.base_url}/{layer.layer_id}/query"
    common = {
        "geometry": _geometry(location),
        "geometryType": "esriGeometryPoint",
        "inSR": 27700,
        "spatialRel": "esriSpatialRelIntersects",
        "f": "json",
    }

    at_point_payload = _get(client, url, common | {"outFields": "*", "returnGeometry": "false"})
    features = at_point_payload.get("features") or []

    within: int | None = None
    if not features:
        # Only worth counting when the extent does not already cover the point.
        count_payload = _get(
            client,
            url,
            common
            | {
                "distance": radius_m,
                "units": "esriSRUnit_Meter",
                "returnCountOnly": "true",
            },
        )
        within = int(count_payload.get("count", 0))

    types: list[str] = []
    published: date | None = None
    for feature in features:
        attributes = feature.get("attributes") or {}
        flood_type = attributes.get("type")
        if isinstance(flood_type, str) and flood_type.strip() and flood_type not in types:
            types.append(flood_type.strip())
        published = published or _published(attributes.get("PUB_DATE"))

    return FloodPresence(
        dataset=dataset,
        label=layer.label,
        definition=layer.definition,
        ignores_defences=layer.ignores_defences,
        at_point=bool(features),
        within_radius=within,
        source_types=tuple(types),
        published=published,
    )


def flood(
    location: Location,
    *,
    radius_m: float = DEFAULT_RADIUS_M,
    client: httpx.Client | None = None,
) -> FloodReport:
    """Look up flood risk at ``location``.

    Both datasets cover England only, and both answer an out-of-area query with
    zero results, so the country is checked before anything is requested.
    """
    planning_source = flood_map_source()
    water_source = surface_water_source()

    # England first. Neither service can distinguish "not in a zone" from "not in
    # England", so asking at all outside England would invite a false negative.
    planning_gap = location.england_only_gap(planning_source, "The Flood Map for Planning")
    if planning_gap is not None:
        return FloodReport(
            planning=planning_gap,
            surface_water=location.england_only_gap(
                water_source, "Environment Agency surface water flood mapping"
            )
            or planning_gap,
        )

    radius = resolve_search_radius(
        radius_m,
        location.precision_m,
        basis=location.precision_basis,
        source=planning_source,
    )
    if isinstance(radius, NotAssessed):
        return FloodReport(planning=radius, surface_water=radius)

    owns_client = client is None
    client = client or httpx.Client()
    try:

        def group(
            datasets: tuple[FloodDataset, ...], source: SourceRef
        ) -> SourceResult[FloodPresence]:
            try:
                findings = [
                    _presence(client, dataset, location, radius.effective_m) for dataset in datasets
                ]
            except (httpx.HTTPError, ValueError, KeyError) as exc:
                return not_assessed(
                    NotAssessedReason.SOURCE_UNAVAILABLE,
                    f"The Environment Agency service could not be reached: {exc}",
                    source,
                )
            return Assessed[FloodPresence](
                findings=findings,
                source=source,
                query=radius.claim(location.precision_basis),
            )

        return FloodReport(
            planning=group(PLANNING_DATASETS, planning_source),
            surface_water=group(SURFACE_WATER_DATASETS, water_source),
            search=radius,
        )
    finally:
        if owns_client:
            client.close()
