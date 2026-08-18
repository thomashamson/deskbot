"""Resolving a user's input to a point on the National Grid.

Accepts a UK postcode or an OS grid reference and produces a
:class:`Location`: British National Grid coordinates, how precisely they are
known, and which country they fall in.

Two things here are load-bearing for the rest of Deskbot.

**Precision is carried, not discarded.** Every input form has a different
uncertainty, and downstream searches must be reconciled against it (see
:mod:`deskbot.precision`).

**Country is established authoritatively.** Environment Agency sources cover
England only and answer out-of-area queries with ``count: 0``, which reads
exactly like "nothing found". The country therefore has to be known before any
EA source is consulted, and it is resolved by point-in-polygon against ONS
boundaries rather than inferred from the nearest postcode -- which near a border
can sit in the wrong country.

No coordinate transformation happens here. Every source Deskbot uses queries in
EPSG:27700 natively, so there is no need for a datum shift and no dependency on
the OSTN15 grid. Latitude and longitude are recorded only when a source hands
them to us for free.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Final

import httpx
from pydantic import BaseModel, ConfigDict

from deskbot import gridref
from deskbot.results import NotAssessed, NotAssessedReason, SourceRef, not_assessed

_TIMEOUT_S: Final = 30.0

_POSTCODES_IO_URL: Final = "https://api.postcodes.io/postcodes/{postcode}"
_ONS_COUNTRIES_URL: Final = (
    "https://services1.arcgis.com/ESMARspQHYMw9BZ9/arcgis/rest/services"
    "/Countries_December_2025_Boundaries_UK_BFC/FeatureServer/0/query"
)

COASTAL_TOLERANCE_M: Final = 1000.0
"""How far offshore to still accept a country match.

The ONS boundaries are clipped to the coastline, so a legitimate site on a pier,
a quay or an estuary edge can fall marginally outside every polygon. Rather than
reject those, a second pass allows this tolerance and flags the result as
approximate.
"""

_POSTCODE_PRECISION_M: Final[dict[int, float]] = {
    1: 10.0,
    2: 10.0,
    3: 100.0,
    4: 100.0,
    5: 100.0,
    6: 5000.0,
}
"""Location uncertainty by OS positional quality indicator, in metres.

From the Code-Point Open specification, where postcodes.io ``quality`` N is the
ONS form of PQI N x 10:

===  ==========================================================  ========
PQI  Definition                                                  Used
===  ==========================================================  ========
10   Within the building of the matched address closest to the   10 m
     postcode mean, determined automatically by OS
20   As above, by visual inspection by National Records of        10 m
     Scotland
30   Approximate to within 50 m (developing sites may be within  100 m
     100 m)
40   Mean of positions of addresses since deleted or recoded     100 m
50   Estimated from surrounding postcodes, usually 100 m         100 m
     resolution
60   Postcode sector mean                                        5000 m
90   No coordinates available                                    n/a
===  ==========================================================  ========

PQI 30 takes the stated worst case rather than the headline 50 m. A postcode is
a *unit* covering several addresses; this models the accuracy of its coordinate,
not the extent of the unit, which is a further uncertainty we cannot measure.
"""

_UNKNOWN_QUALITY_PRECISION_M: Final = 5000.0
"""Used for unrecognised quality codes. Deliberately coarse: an unknown
positional quality should refuse a search, not quietly pass one."""


class Country(StrEnum):
    ENGLAND = "England"
    SCOTLAND = "Scotland"
    WALES = "Wales"
    NORTHERN_IRELAND = "Northern Ireland"


class InputKind(StrEnum):
    POSTCODE = "postcode"
    GRID_REFERENCE = "grid_reference"


class LocateError(Exception):
    """Input could not be resolved to a usable point."""


class UnparseableLocationError(LocateError):
    """Input is neither a recognisable postcode nor a grid reference."""


class UnknownPostcodeError(LocateError):
    """The postcode is well-formed but not in the ONS directory."""


class OutsideUnitedKingdomError(LocateError):
    """The point does not fall in any UK country, so no source covers it."""


class LocationServiceUnavailableError(LocateError):
    """A lookup service failed, so the location could not be established."""


class Location(BaseModel):
    """A resolved point, with its uncertainty and country."""

    model_config = ConfigDict(frozen=True)

    easting: int
    northing: int

    precision_m: float
    """Radius within which the true location lies."""

    precision_basis: str
    """Short phrase naming what the coordinate represents, for restated claims."""

    country: Country
    country_is_approximate: bool = False
    """True when the country was matched only within :data:`COASTAL_TOLERANCE_M`."""

    input_raw: str
    input_kind: InputKind
    normalised_input: str

    latitude: float | None = None
    longitude: float | None = None
    """Recorded only where a source supplied them. Never derived here."""

    postcode_quality: int | None = None
    sources: tuple[SourceRef, ...] = ()

    def england_only_gap(self, source: SourceRef, dataset: str) -> NotAssessed | None:
        """Return a gap if ``dataset`` cannot be assessed here, else ``None``.

        The gate for every Environment Agency source. Returning a
        :class:`~deskbot.results.NotAssessed` rather than a bool is deliberate:
        the caller cannot accidentally proceed with an empty result set, because
        what it gets back is already the explanation.
        """
        if self.country is Country.ENGLAND:
            return None

        # Names the equivalent authority without asserting what it publishes:
        # this helper gates terrain as well as flood, and "SEPA publishes flood
        # maps" is wrong on a terrain lookup.
        alternative = {
            Country.SCOTLAND: (
                "The Scottish Environment Protection Agency (SEPA) is the equivalent "
                "authority for Scotland."
            ),
            Country.WALES: ("Natural Resources Wales (NRW) is the equivalent authority for Wales."),
            Country.NORTHERN_IRELAND: (
                "The Department for Infrastructure is the equivalent authority for "
                "Northern Ireland."
            ),
        }.get(self.country, "")

        return not_assessed(
            NotAssessedReason.OUTSIDE_COVERAGE,
            (
                f"{dataset} covers England only; this site is in "
                f"{self.country.value}. It has not been assessed. {alternative}"
            ).strip(),
            source,
        )


def _attribution_year() -> int:
    return datetime.now(UTC).year


def _postcodes_io_source(postcode: str) -> SourceRef:
    year = _attribution_year()
    return SourceRef(
        name="postcodes.io (ONS Postcode Directory)",
        url=_POSTCODES_IO_URL.format(postcode=postcode),
        licence="OGL v3",
        attribution=(
            f"Contains OS data © Crown copyright and database right {year}; "
            f"Contains Royal Mail data © Royal Mail copyright and database right {year}; "
            "Source: Office for National Statistics licensed under the Open "
            "Government Licence v.3.0"
        ),
    )


def _ons_countries_source() -> SourceRef:
    return SourceRef(
        name="ONS Countries (December 2025) Boundaries UK BFC",
        url=_ONS_COUNTRIES_URL,
        licence="OGL v3",
        attribution=(
            "Source: Office for National Statistics licensed under the Open "
            "Government Licence v.3.0; "
            f"Contains OS data © Crown copyright and database right {_attribution_year()}"
        ),
    )


def _get_json(client: httpx.Client, url: str, params: dict[str, Any]) -> dict[str, Any]:
    """GET and parse JSON, treating ArcGIS error payloads as failures.

    ArcGIS returns errors under HTTP 200. Without this check a failed lookup
    would parse as a successful one with no features.
    """
    try:
        response = client.get(url, params=params, timeout=_TIMEOUT_S)
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise LocationServiceUnavailableError(f"{url} failed: {exc}") from exc

    if isinstance(payload, dict) and "error" in payload:
        message = payload["error"].get("message", "unknown error")
        raise LocationServiceUnavailableError(f"{url} returned an error payload: {message}")
    return payload


def _lookup_country(client: httpx.Client, easting: int, northing: int) -> tuple[Country, bool]:
    """Point-in-polygon against ONS country boundaries.

    Returns the country and whether the coastal tolerance was needed.
    """
    base_params: dict[str, Any] = {
        "geometry": f'{{"x":{easting},"y":{northing},"spatialReference":{{"wkid":27700}}}}',
        "geometryType": "esriGeometryPoint",
        "inSR": 27700,
        "spatialRel": "esriSpatialRelIntersects",
        "outFields": "CTRY25NM",
        "returnGeometry": "false",
        "f": "json",
    }

    for tolerance in (None, COASTAL_TOLERANCE_M):
        params = dict(base_params)
        if tolerance is not None:
            params["distance"] = tolerance
            params["units"] = "esriSRUnit_Meter"

        payload = _get_json(client, _ONS_COUNTRIES_URL, params)
        features = payload.get("features") or []
        if features:
            name = features[0]["attributes"]["CTRY25NM"]
            try:
                return Country(name), tolerance is not None
            except ValueError as exc:
                raise LocationServiceUnavailableError(
                    f"ONS returned an unrecognised country name: {name!r}"
                ) from exc

    raise OutsideUnitedKingdomError(
        f"E{easting} N{northing} does not fall within any UK country, even "
        f"allowing {COASTAL_TOLERANCE_M:.0f} m for the coastline. Deskbot covers "
        "Great Britain only."
    )


def _locate_postcode(client: httpx.Client, raw: str) -> dict[str, Any]:
    source = _postcodes_io_source(raw.strip())
    try:
        response = client.get(source.url, timeout=_TIMEOUT_S)
    except httpx.HTTPError as exc:
        raise LocationServiceUnavailableError(f"postcodes.io failed: {exc}") from exc

    if response.status_code == 404:
        raise UnknownPostcodeError(
            f"{raw!r} is not a postcode in the ONS Postcode Directory. It may be "
            "newly issued, terminated, or mistyped."
        )
    try:
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise LocationServiceUnavailableError(f"postcodes.io failed: {exc}") from exc

    result = payload.get("result")
    if not result or result.get("eastings") is None:
        raise UnknownPostcodeError(
            f"{raw!r} has no grid reference in the ONS Postcode Directory "
            "(positional quality 90), so it cannot be located."
        )
    result["_source"] = source
    return result


def locate(raw: str, *, client: httpx.Client | None = None) -> Location:
    """Resolve a postcode or grid reference to a :class:`Location`.

    Args:
        raw: A UK postcode ('SE1 9GF') or OS grid reference ('TQ 32785 80244').
        client: Optional HTTP client, for testing or connection reuse.

    Raises:
        UnparseableLocationError: input matches neither form.
        UnknownPostcodeError: postcode not in the ONS directory, or has no coordinates.
        OutsideUnitedKingdomError: the point is not in a UK country.
        LocationServiceUnavailableError: a lookup service failed.
    """
    text = raw.strip()
    if not text:
        raise UnparseableLocationError("No location given.")

    owns_client = client is None
    client = client or httpx.Client()
    try:
        if gridref.looks_like_grid_reference(text):
            parsed = gridref.parse(text)
            country, approximate = _lookup_country(client, parsed.easting, parsed.northing)
            square = (
                f"{parsed.square_size_m} m"
                if parsed.square_size_m < 1000
                else f"{parsed.square_size_m // 1000} km"
            )
            return Location(
                easting=parsed.easting,
                northing=parsed.northing,
                precision_m=parsed.precision_m,
                precision_basis=f"centre of a {square} grid square",
                country=country,
                country_is_approximate=approximate,
                input_raw=raw,
                input_kind=InputKind.GRID_REFERENCE,
                normalised_input=parsed.normalised,
                sources=(_ons_countries_source(),),
            )

        result = _locate_postcode(client, text)
        source: SourceRef = result.pop("_source")
        easting = int(result["eastings"])
        northing = int(result["northings"])
        quality = result.get("quality")
        precision_m = _POSTCODE_PRECISION_M.get(quality, _UNKNOWN_QUALITY_PRECISION_M)
        country, approximate = _lookup_country(client, easting, northing)

        return Location(
            easting=easting,
            northing=northing,
            precision_m=precision_m,
            precision_basis="postcode centroid",
            country=country,
            country_is_approximate=approximate,
            input_raw=raw,
            input_kind=InputKind.POSTCODE,
            normalised_input=result.get("postcode", text),
            latitude=result.get("latitude"),
            longitude=result.get("longitude"),
            postcode_quality=quality,
            sources=(source, _ons_countries_source()),
        )
    finally:
        if owns_client:
            client.close()
