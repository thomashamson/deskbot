"""Deskbot: preliminary desk study summaries for a UK point, from public data.

An indication, not a Phase 1 report. Every claim is attributed to its source,
and anything that could not be checked is reported as a gap rather than as an
absence of findings.
"""

from deskbot.boreholes import (
    BoreholeRecord,
    BoreholeReport,
    ScanAvailability,
    boreholes,
)
from deskbot.flood import (
    DEFENCES_CAVEAT,
    FloodDataset,
    FloodPresence,
    FloodReport,
    flood,
)
from deskbot.geology import (
    GeologyLayer,
    GeologyReport,
    GeologyUnit,
    LocationVariation,
    geology,
)
from deskbot.locate import Country, InputKind, LocateError, Location, locate
from deskbot.precision import SearchRadius, resolve_search_radius
from deskbot.results import (
    Assessed,
    NotAssessed,
    NotAssessedReason,
    SourceRef,
    SourceResult,
)
from deskbot.terrain import GroundLevel, Relief, TerrainReport, terrain

__all__ = [
    "DEFENCES_CAVEAT",
    "Assessed",
    "BoreholeRecord",
    "BoreholeReport",
    "Country",
    "FloodDataset",
    "FloodPresence",
    "FloodReport",
    "GeologyLayer",
    "GeologyReport",
    "GeologyUnit",
    "GroundLevel",
    "InputKind",
    "LocateError",
    "Location",
    "LocationVariation",
    "NotAssessed",
    "NotAssessedReason",
    "Relief",
    "ScanAvailability",
    "SearchRadius",
    "SourceRef",
    "SourceResult",
    "TerrainReport",
    "boreholes",
    "flood",
    "geology",
    "locate",
    "resolve_search_radius",
    "terrain",
]
