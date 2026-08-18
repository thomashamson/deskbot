"""Bedrock, superficial, artificial and mass movement geology at a point.

Source: BGS Geology 1:50,000 via WMS ``GetFeatureInfo``, queried in EPSG:27700
and returned as GeoJSON. Open Government Licence, with mandatory attribution.

Three shapes here follow from what the data actually does.

**Per-layer results, not one flat list.** A single list of units cannot express
"artificial ground was checked and there is none here". That absence would look
identical to never having asked -- the failure this project exists to avoid, in
miniature. Each layer therefore carries its own assessed-or-not result.

**Faults are a standing gap.** BGS maps them as line geometry, and a point query
essentially never intersects a line. Querying anyway would report "no faults"
everywhere. Reporting the limitation is honest; reporting the empty result is
not.

**Location uncertainty is sampled, not merely noted.** A kilometre-precision
grid reference can span two mapped units, so where uncertainty is material the
corners of the uncertainty square are queried too, and any units found there are
named.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Final

import httpx
from pydantic import BaseModel, ConfigDict

from deskbot.locate import Location
from deskbot.results import (
    Assessed,
    NotAssessed,
    NotAssessedReason,
    SourceRef,
    SourceResult,
    not_assessed,
)

_WMS_URL: Final = "https://map.bgs.ac.uk/arcgis/services/BGS_Detailed_Geology/MapServer/WMSServer"
_TIMEOUT_S: Final = 30.0

MATERIAL_UNCERTAINTY_M: Final = 50.0
"""Above this, the corners of the uncertainty square are sampled too.

Below it the corners sit within tens of metres of the centre and would only
re-report the same polygon at four times the cost.
"""


class GeologyLayer(StrEnum):
    BEDROCK = "bedrock"
    SUPERFICIAL = "superficial"
    ARTIFICIAL_GROUND = "artificial_ground"
    MASS_MOVEMENT = "mass_movement"


_WMS_LAYERS: Final[dict[GeologyLayer, str]] = {
    GeologyLayer.BEDROCK: "BGS.50k.Bedrock",
    GeologyLayer.SUPERFICIAL: "BGS.50k.Superficial.deposits",
    GeologyLayer.ARTIFICIAL_GROUND: "BGS.50k.Artificial.ground",
    GeologyLayer.MASS_MOVEMENT: "BGS.50k.Mass.movement",
}

_LAYER_BY_WMS_NAME: Final[dict[str, GeologyLayer]] = {v: k for k, v in _WMS_LAYERS.items()}

_HUMAN_LAYER_NAMES: Final[dict[GeologyLayer, str]] = {
    GeologyLayer.BEDROCK: "bedrock",
    GeologyLayer.SUPERFICIAL: "superficial deposits",
    GeologyLayer.ARTIFICIAL_GROUND: "artificial ground",
    GeologyLayer.MASS_MOVEMENT: "mass movement deposits",
}


def bgs_source() -> SourceRef:
    """Provenance for the BGS 1:50k WMS, with the licence-required attribution."""
    return SourceRef(
        name="BGS Geology 1:50k (DiGMapGB-50) WMS",
        url=_WMS_URL,
        licence="OGL v3",
        attribution=(
            f"Contains British Geological Survey materials © UKRI {datetime.now(UTC).year}"
        ),
    )


class GeologyUnit(BaseModel):
    """One mapped geological unit at a point."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    layer: GeologyLayer
    name: str
    """Lexicon unit name, e.g. 'London Clay Formation'."""

    lithology: str | None = None
    """Rock or soil composition, e.g. 'Clay, silt and sand'."""

    rank: str | None = None
    group: str | None = None
    formation: str | None = None

    max_period: str | None = None
    max_epoch: str | None = None

    broad_lithology: str | None = None
    setting: str | None = None
    environment: str | None = None
    """Only bedrock carries a depositional-environment description."""

    lexicon_url: str | None = None
    """Per-unit BGS Lexicon entry, so a claim can cite the definition."""

    map_sheet: str | None = None
    map_scale: str | None = None
    lex_code: str | None = None
    version: str | None = None

    def describe(self) -> str:
        """One line naming the unit and what it is made of."""
        if self.lithology:
            return f"{self.name} ({self.lithology})"
        return self.name


class LayerVariation(BaseModel):
    """A layer whose mapped unit changes across the location's uncertainty."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    layer: GeologyLayer
    units: tuple[str, ...]
    """Every distinct unit found across the sampled points, centre included."""

    def describe(self) -> str:
        """Name the units, rather than only reporting that they differ.

        'spans Alluvium and Langley Silt Member' is actionable; 'spans more than
        one mapped unit' sends the reader off to look it up.
        """
        *rest, last = self.units
        listed = f"{', '.join(rest)} and {last}" if rest else last
        return f"{_HUMAN_LAYER_NAMES[self.layer]} spans {listed}"


class LocationVariation(BaseModel):
    """What sampling around the location found.

    ``points_sampled == 1`` means the uncertainty was immaterial and only the
    centre was queried -- distinct from having sampled and found agreement.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    points_sampled: int
    offset_m: float = 0.0
    """Distance from centre to each sampled corner, per axis."""

    layers: tuple[LayerVariation, ...] = ()

    @property
    def checked(self) -> bool:
        return self.points_sampled > 1

    @property
    def varies(self) -> bool:
        return bool(self.layers)

    def describe(self) -> str | None:
        """A sentence for the report, or ``None`` if there is nothing to say."""
        if not self.checked:
            return None
        if not self.varies:
            return (
                f"Sampled at {self.points_sampled} points across the location's "
                "uncertainty; the mapped geology is consistent."
            )
        clauses = "; ".join(v.describe() for v in self.layers)
        return (
            f"The location is uncertain to ±{self.offset_m:.0f} m and spans more "
            f"than one mapped unit: {clauses}. The site may sit on any of these."
        )


class GeologyReport(BaseModel):
    """Geology at a location, one result per layer."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    bedrock: SourceResult[GeologyUnit]
    superficial: SourceResult[GeologyUnit]
    artificial_ground: SourceResult[GeologyUnit]
    mass_movement: SourceResult[GeologyUnit]

    faults: NotAssessed
    """Always a gap. See :attr:`NotAssessedReason.NOT_QUERYABLE_AT_A_POINT`."""

    variation: LocationVariation

    def layer(self, layer: GeologyLayer) -> SourceResult[GeologyUnit]:
        return getattr(self, layer.value)


def faults_gap() -> NotAssessed:
    """The standing gap for faults, so a report never implies they were checked."""
    return not_assessed(
        NotAssessedReason.NOT_QUERYABLE_AT_A_POINT,
        (
            "Faults and other linear features are mapped by BGS as lines, which a "
            "point query cannot reliably intersect. They have not been assessed. "
            "A fault may cross the site without appearing here; a proximity "
            "search against the linear features layer would be needed."
        ),
        bgs_source(),
    )


def _clean(value: object) -> str | None:
    """Normalise BGS blanks.

    Some fields arrive as a single space rather than empty or absent, which would
    otherwise be stored and rendered as a meaningless value.
    """
    if value is None:
        return None
    text = str(value).strip()
    if not text or text in {"Not Applicable", "Not Defined", "No Parent"}:
        return None
    return text


def _unit_from_feature(feature: dict[str, Any]) -> GeologyUnit | None:
    props = feature.get("properties") or {}
    wms_name = (feature.get("layerName") or "").strip()
    layer = _LAYER_BY_WMS_NAME.get(wms_name)
    name = _clean(props.get("LEX_D"))
    if layer is None or name is None:
        return None

    return GeologyUnit(
        layer=layer,
        name=name,
        lithology=_clean(props.get("RCS_D")),
        rank=_clean(props.get("RANK")),
        group=_clean(props.get("GP_EQ_D")),
        formation=_clean(props.get("FM_EQ_D")),
        max_period=_clean(props.get("MAX_PERIOD")),
        max_epoch=_clean(props.get("MAX_EPOCH")),
        broad_lithology=_clean(props.get("BROAD_D")),
        setting=_clean(props.get("SETTING_D")),
        environment=_clean(props.get("ENVIRONM_D")),
        lexicon_url=_clean(props.get("LEX_WEB")),
        map_sheet=_clean(props.get("MAP_SRC")),
        map_scale=_clean(props.get("NOM_SCALE")),
        lex_code=_clean(props.get("LEX")),
        version=_clean(props.get("VERSION")),
    )


def _query_point(client: httpx.Client, easting: int, northing: int) -> list[GeologyUnit]:
    """One GetFeatureInfo call covering every layer at a single point.

    Raises:
        httpx.HTTPError: on transport failure.
        ValueError: if the response is not the GeoJSON we asked for.
    """
    layers = ",".join(_WMS_LAYERS.values())
    params = {
        "service": "WMS",
        "version": "1.3.0",
        "request": "GetFeatureInfo",
        "layers": layers,
        "query_layers": layers,
        "crs": "EPSG:27700",
        "bbox": f"{easting - 50},{northing - 50},{easting + 50},{northing + 50}",
        "width": 100,
        "height": 100,
        "i": 50,
        "j": 50,
        "info_format": "application/geo+json",
        "feature_count": 20,
    }
    response = client.get(_WMS_URL, params=params, timeout=_TIMEOUT_S)
    response.raise_for_status()

    # A WMS reports failure as an XML ServiceException with a 200, so a parse
    # error here means the service refused rather than that there is no geology.
    payload = response.json()
    features = payload.get("features")
    if features is None:
        raise ValueError("response contained no feature collection")

    units = [_unit_from_feature(f) for f in features]
    return [u for u in units if u is not None]


def _sample_offsets(location: Location) -> list[tuple[int, int]]:
    """Corner offsets to sample, empty when the uncertainty is immaterial.

    ``precision_m`` is a radius (the half-diagonal of a grid square), so the
    per-axis half-side is that divided by root two. For a 1 km reference this
    recovers the actual square corners at ±500 m.
    """
    if location.precision_m <= MATERIAL_UNCERTAINTY_M:
        return []
    half_side = round(location.precision_m / math.sqrt(2))
    return [
        (-half_side, -half_side),
        (half_side, -half_side),
        (-half_side, half_side),
        (half_side, half_side),
    ]


def _variation(
    centre: list[GeologyUnit], corners: list[list[GeologyUnit]], offset_m: float
) -> LocationVariation:
    if not corners:
        return LocationVariation(points_sampled=1)

    varying: list[LayerVariation] = []
    for layer in GeologyLayer:
        names: list[str] = []
        for sample in [centre, *corners]:
            for unit in sample:
                if unit.layer is layer and unit.name not in names:
                    names.append(unit.name)
        if len(names) > 1:
            varying.append(LayerVariation(layer=layer, units=tuple(names)))

    return LocationVariation(
        points_sampled=1 + len(corners),
        offset_m=offset_m,
        layers=tuple(varying),
    )


def _all_layers_gap(gap: NotAssessed, variation: LocationVariation) -> GeologyReport:
    return GeologyReport(
        bedrock=gap,
        superficial=gap,
        artificial_ground=gap,
        mass_movement=gap,
        faults=faults_gap(),
        variation=variation,
    )


def geology(location: Location, *, client: httpx.Client | None = None) -> GeologyReport:
    """Look up mapped geology at ``location``.

    Every layer is reported separately, so "checked, nothing here" stays distinct
    from "not checked". Faults are always a gap. Where the location is uncertain
    by more than :data:`MATERIAL_UNCERTAINTY_M`, the corners of the uncertainty
    square are sampled and any additional units are named.
    """
    source = bgs_source()
    owns_client = client is None
    client = client or httpx.Client()
    try:
        try:
            centre = _query_point(client, location.easting, location.northing)
        except (httpx.HTTPError, ValueError) as exc:
            return _all_layers_gap(
                not_assessed(
                    NotAssessedReason.SOURCE_UNAVAILABLE,
                    f"The BGS 1:50k geology service could not be reached: {exc}",
                    source,
                ),
                LocationVariation(points_sampled=0),
            )

        # Bedrock is a complete onshore coverage. Nothing there means the point
        # is off the map, not that there is no rock beneath it.
        if not any(u.layer is GeologyLayer.BEDROCK for u in centre):
            return _all_layers_gap(
                not_assessed(
                    NotAssessedReason.OUTSIDE_COVERAGE,
                    (
                        "No bedrock is mapped at this location, which means it "
                        "falls outside the BGS onshore geological map rather than "
                        "that no geology is present. Offshore and inter-tidal "
                        "points fall here."
                    ),
                    source,
                ),
                LocationVariation(points_sampled=1),
            )

        offsets = _sample_offsets(location)
        corners: list[list[GeologyUnit]] = []
        for dx, dy in offsets:
            try:
                corners.append(_query_point(client, location.easting + dx, location.northing + dy))
            except (httpx.HTTPError, ValueError):
                # A failed corner narrows the check but does not invalidate the
                # centre, so carry on with fewer samples.
                continue

        offset_m = float(offsets[0][0]) if offsets else 0.0
        variation = _variation(centre, corners, abs(offset_m))

        query = f"point query at E{location.easting} N{location.northing}"
        if corners:
            query += f", plus {len(corners)} corner samples at ±{abs(offset_m):.0f} m"

        def result_for(layer: GeologyLayer) -> Assessed[GeologyUnit]:
            return Assessed[GeologyUnit](
                findings=[u for u in centre if u.layer is layer],
                source=source,
                query=query,
            )

        return GeologyReport(
            bedrock=result_for(GeologyLayer.BEDROCK),
            superficial=result_for(GeologyLayer.SUPERFICIAL),
            artificial_ground=result_for(GeologyLayer.ARTIFICIAL_GROUND),
            mass_movement=result_for(GeologyLayer.MASS_MOVEMENT),
            faults=faults_gap(),
            variation=variation,
        )
    finally:
        if owns_client:
            client.close()
