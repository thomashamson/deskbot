"""Borehole records near a point, from the BGS Single Onshore Borehole Index.

Source: SOBI via the BGS GeoIndex ArcGIS service, queried in EPSG:27700 with a
true radius search. Open Government Licence, free for commercial, research and
public use.

The index is Great Britain wide, so unlike the Environment Agency sources there
is no country gate. A count of zero here is a real finding: the index was
searched and there are no records, which is common in rural areas.

Three things shape this module.

**The search radius is reconciled against location precision** before anything is
queried, so a coarse grid reference either widens the search or refuses it rather
than reporting distant records as though they were on the site.

**The count is exact; the listing is a sample.** Central London returns over a
thousand records within a kilometre. The report always carries the true total and
lists only the nearest few, flagged as a subset, so a short list can never be
mistaken for a small number of records.

**Absent values are absent, not zero.** Roughly a third of records carry a depth
of ``-1`` meaning unknown, a quarter have no year, and a sixth carry the literal
string ``Not Available`` where a scan URL would go. Rendering any of those as
given would invent facts.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime
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

_QUERY_URL: Final = (
    "https://map.bgs.ac.uk/arcgis/rest/services/GeoIndex_Onshore/boreholes/MapServer/0/query"
)
_TIMEOUT_S: Final = 45.0

DEFAULT_RADIUS_M: Final = 250.0
"""Standard desk-study screening distance.

Also chosen so the precision gate stays usable: 250 m plus the 707 m uncertainty
of a 1 km grid reference is 957 m, just inside the 1 km ceiling. A 500 m default
would refuse every kilometre-precision reference outright.
"""

DEFAULT_LIMIT: Final = 20
"""How many of the nearest records to list. The total is always exact."""

_MAX_RECORD_COUNT: Final = 2000
"""The service's own per-response cap."""

_MAX_PAGES: Final = 5
"""Safety valve. Beyond this the nearest-first ranking is reported as inexact."""

_UNKNOWN_STRINGS: Final = frozenset({"", "-", "n/a", "not available", "not entered", "none"})


class ScanAvailability(StrEnum):
    """Whether the scanned log can actually be obtained."""

    FREE_ONLINE = "free_online"
    """A BGS scans API URL: viewable and downloadable under the OGL."""

    PURCHASE = "purchase"
    """Points at the BGS shop. Not a free record, and must not be described as one."""

    NONE = "none"
    """No scan indexed."""


def sobi_source(url: str = _QUERY_URL) -> SourceRef:
    return SourceRef(
        name="BGS Single Onshore Borehole Index (SOBI)",
        url=url,
        licence="OGL v3",
        attribution=(
            f"Contains British Geological Survey materials © UKRI {datetime.now(UTC).year}"
        ),
    )


class BoreholeRecord(BaseModel):
    """One indexed borehole, shaft or well."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    reference: str
    name: str | None = None

    easting: int
    northing: int

    distance_m: float
    """Distance from the query point, which is not necessarily the site.

    Where the search was widened to cover location uncertainty, this is measured
    from the centre of that uncertainty, not from the site itself.
    """

    depth_m: float | None = None
    """Drilled length. ``None`` where the index records it as unknown.

    About a third of records carry ``-1`` here, which is a sentinel and not a
    depth.
    """

    year: int | None = None
    positional_precision: str | None = None
    """How well the borehole's own position is known, e.g. '± 10 METRES'."""

    scan: ScanAvailability = ScanAvailability.NONE
    scan_url: str | None = None
    ags_log_url: str | None = None
    """Digital AGS geotechnical data, where it exists."""

    held_at: str | None = None

    def describe(self) -> str:
        """One line for a report."""
        depth = f"{self.depth_m:.1f} m" if self.depth_m is not None else "depth unknown"
        year = str(self.year) if self.year is not None else "undated"
        label = self.name or self.reference
        return f"{label} ({self.reference}), {depth}, {year}, {self.distance_m:.0f} m away"


class BoreholeReport(BaseModel):
    """Borehole records near a location."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    records: SourceResult[BoreholeRecord]

    search: SearchRadius | None = None
    """The radius actually used. ``None`` when the search was never run."""

    total_within_radius: int | None = None
    """Exact count from the service, independent of how many are listed."""

    ranking_complete: bool = True
    """False if not every record could be fetched, so 'nearest' is approximate."""

    @property
    def listing_is_sample(self) -> bool:
        """True when more records exist than are listed."""
        if not isinstance(self.records, Assessed) or self.total_within_radius is None:
            return False
        return self.total_within_radius > len(self.records.findings)

    @property
    def nearest_m(self) -> float | None:
        """Distance to the closest record, or ``None`` if there are none.

        The single most useful number for a screening decision: it separates
        "records are on the site" from "the nearest is out at the edge of the
        search", which a count alone cannot express.
        """
        if not isinstance(self.records, Assessed) or not self.records.findings:
            return None
        return self.records.findings[0].distance_m

    @property
    def listed_to_m(self) -> float | None:
        """Distance to the furthest listed record: how far the listing reaches."""
        if not isinstance(self.records, Assessed) or not self.records.findings:
            return None
        return self.records.findings[-1].distance_m

    def describe(self) -> str | None:
        """A sentence stating what was searched, what was found, and how close.

        Counts alone are misleading in both directions: a thousand records mean
        little if the nearest is 240 m away, and a single record on the site is
        worth more than fifty at the edge.
        """
        if not isinstance(self.records, Assessed) or self.search is None:
            return None

        total = self.total_within_radius or 0
        where = self.records.query or f"within {self.search.effective_m:.0f} m"
        if total == 0:
            return f"No borehole records {where}."

        plural = "s" if total != 1 else ""
        sentence = f"{total:,} borehole record{plural} {where}"

        nearest = self.nearest_m
        if nearest is not None:
            sentence += f", nearest at {nearest:.0f} m"

        if self.listing_is_sample:
            reach = self.listed_to_m
            sentence += f"; the closest {len(self.records.findings)} are listed"
            if reach is not None:
                sentence += f", out to {reach:.0f} m"
            if not self.ranking_complete:
                sentence += (
                    ", though too many records were returned to rank them all by "
                    "distance reliably, so these may not be the true nearest"
                )
        return sentence + "."


def _clean(value: object) -> str | None:
    """Normalise the index's several spellings of 'nothing here'."""
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() in _UNKNOWN_STRINGS:
        return None
    return text


def _url_or_none(value: object) -> str | None:
    """Return a URL only if it is one.

    ``SCAN_URL`` carries the literal string ``Not Available`` for records with no
    scan, which would otherwise be rendered as a link.
    """
    text = _clean(value)
    if text is None or not text.lower().startswith(("http://", "https://")):
        return None
    return text


def _scan_availability(url: str | None) -> ScanAvailability:
    if url is None:
        return ScanAvailability.NONE
    if "shop.bgs.ac.uk" in url:
        return ScanAvailability.PURCHASE
    return ScanAvailability.FREE_ONLINE


def _depth(value: object) -> float | None:
    """``-1`` means unknown, not a depth. Treat anything non-positive as unknown."""
    if value is None:
        return None
    try:
        depth = float(value)
    except (TypeError, ValueError):
        return None
    return depth if depth > 0 else None


def _year(value: object) -> int | None:
    text = _clean(value)
    if text is None or not text.isdigit():
        return None
    return int(text)


def _record(attributes: dict[str, Any], from_e: int, from_n: int) -> BoreholeRecord | None:
    reference = _clean(attributes.get("REFERENCE"))
    easting = attributes.get("EASTING")
    northing = attributes.get("NORTHING")
    if reference is None or easting is None or northing is None:
        return None

    scan_url = _url_or_none(attributes.get("SCAN_URL"))
    return BoreholeRecord(
        reference=reference,
        name=_clean(attributes.get("NAME")),
        easting=int(easting),
        northing=int(northing),
        distance_m=math.hypot(easting - from_e, northing - from_n),
        depth_m=_depth(attributes.get("LENGTH")),
        year=_year(attributes.get("YEAR_KNOWN")),
        positional_precision=_clean(attributes.get("PRECISION")),
        scan=_scan_availability(scan_url),
        scan_url=scan_url,
        ags_log_url=_url_or_none(attributes.get("AGS_LOG_URL")),
        held_at=_clean(attributes.get("HELD_AT")),
    )


def _base_params(location: Location, radius_m: float) -> dict[str, Any]:
    return {
        "geometry": f"{location.easting},{location.northing}",
        "geometryType": "esriGeometryPoint",
        "inSR": 27700,
        "distance": radius_m,
        "units": "esriSRUnit_Meter",
        "spatialRel": "esriSpatialRelIntersects",
        "f": "json",
    }


def _get(client: httpx.Client, params: dict[str, Any]) -> dict[str, Any]:
    """Query SOBI, treating an ArcGIS error payload as a failure.

    ArcGIS reports errors under HTTP 200, so a status check alone would turn a
    failed search into an empty one.
    """
    response = client.get(_QUERY_URL, params=params, timeout=_TIMEOUT_S)
    response.raise_for_status()
    payload = response.json()
    if "error" in payload:
        raise ValueError(str(payload["error"].get("message", "unknown ArcGIS error")))
    return payload


def _fetch_all(
    client: httpx.Client, location: Location, radius_m: float
) -> tuple[list[BoreholeRecord], bool]:
    """Fetch every record within the radius, paginating as needed.

    Returns the records and whether the fetch was exhaustive.
    """
    fields = (
        "REFERENCE,NAME,EASTING,NORTHING,LENGTH,YEAR_KNOWN,PRECISION,SCAN_URL,AGS_LOG_URL,HELD_AT"
    )
    records: list[BoreholeRecord] = []

    for page in range(_MAX_PAGES):
        params = _base_params(location, radius_m) | {
            "outFields": fields,
            "returnGeometry": "false",
            "resultOffset": page * _MAX_RECORD_COUNT,
            "resultRecordCount": _MAX_RECORD_COUNT,
        }
        payload = _get(client, params)
        features = payload.get("features") or []
        for feature in features:
            record = _record(feature.get("attributes") or {}, location.easting, location.northing)
            if record is not None:
                records.append(record)

        if not payload.get("exceededTransferLimit") or not features:
            return records, True

    return records, False


def boreholes(
    location: Location,
    *,
    radius_m: float = DEFAULT_RADIUS_M,
    limit: int = DEFAULT_LIMIT,
    client: httpx.Client | None = None,
) -> BoreholeReport:
    """Search SOBI around ``location``.

    Args:
        location: Where to search, carrying its own precision.
        radius_m: Requested radius. Widened to cover location uncertainty, or
            refused if that uncertainty makes the search meaningless.
        limit: How many of the nearest records to list. The total is exact
            regardless.
        client: Optional HTTP client.
    """
    source = sobi_source()

    radius = resolve_search_radius(
        radius_m,
        location.precision_m,
        basis=location.precision_basis,
        source=source,
    )
    if isinstance(radius, NotAssessed):
        return BoreholeReport(records=radius)

    owns_client = client is None
    client = client or httpx.Client()
    try:
        try:
            count_payload = _get(
                client,
                _base_params(location, radius.effective_m) | {"returnCountOnly": "true"},
            )
            total = int(count_payload["count"])
            found, exhaustive = _fetch_all(client, location, radius.effective_m)
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            return BoreholeReport(
                records=not_assessed(
                    NotAssessedReason.SOURCE_UNAVAILABLE,
                    f"The BGS borehole index could not be searched: {exc}",
                    source,
                ),
                search=radius,
            )

        found.sort(key=lambda r: r.distance_m)
        return BoreholeReport(
            records=Assessed[BoreholeRecord](
                findings=found[:limit],
                source=source,
                query=radius.claim(location.precision_basis),
            ),
            search=radius,
            total_within_radius=total,
            ranking_complete=exhaustive,
        )
    finally:
        if owns_client:
            client.close()
