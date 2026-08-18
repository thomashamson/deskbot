#!/usr/bin/env python3
"""Throwaway reconnaissance probe for Deskbot candidate data sources.

Session 1 artefact. NOT application code - stdlib only, no Pydantic, no error
handling worth the name. Its only job is to prove that each endpoint recorded
in docs/data-sources.md is reachable and returns what that document claims.

Run:  python scripts/probe_sources.py
Exit: 0 if every probe passed, 1 otherwise.
"""

from __future__ import annotations

import json
import math
import sys
import urllib.parse
import urllib.request
from typing import Any, Callable

TIMEOUT = 45
UA = "deskbot-recon/0.0 (session-1 probe)"

# Test points agreed in session 1.
#   RICH   - dense data: London Clay, Thames gravels, tidal flood zone, many boreholes
#   SPARSE - rural England: real coverage, but few or no point records
#   SCOT   - outside England: proves which services are England-only
RICH = {"name": "SE1 Southwark", "e": 532785, "n": 180244, "lat": 51.505538, "lon": -0.088134}
SPARSE = {"name": "NE19 Redesdale", "e": 393851, "n": 587167, "lat": 55.178704, "lon": -2.098317}
SCOT = {"name": "Edinburgh", "e": 325200, "n": 673900, "lat": 55.9486, "lon": -3.1999}


def get(url: str, params: dict[str, Any] | None = None) -> bytes:
    if params:
        url = url + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return resp.read()


def get_json(url: str, params: dict[str, Any] | None = None) -> Any:
    """GET and parse JSON.

    ArcGIS endpoints return HTTP 200 with an error body, so status codes alone
    are not a reachability test. Every ArcGIS probe must inspect the payload.
    This caught us out during recon and will catch the real client too.
    """
    data = json.loads(get(url, params))
    if isinstance(data, dict) and "error" in data:
        raise RuntimeError("ArcGIS error body under HTTP 200: " + str(data["error"].get("message")))
    return data


def bbox_around(lon: float, lat: float, metres: int) -> str:
    """Crude CRS84 bbox. Fine for a probe; the real tool must not do this."""
    dlat = metres / 111320.0
    dlon = metres / (111320.0 * math.cos(math.radians(lat)))
    return "%f,%f,%f,%f" % (lon - dlon, lat - dlat, lon + dlon, lat + dlat)


def wms_feature_info(base: str, layers: str, e: int, n: int, fmt: str) -> bytes:
    return get(
        base,
        {
            "service": "WMS",
            "version": "1.3.0",
            "request": "GetFeatureInfo",
            "layers": layers,
            "query_layers": layers,
            "crs": "EPSG:27700",
            "bbox": "%d,%d,%d,%d" % (e - 100, n - 100, e + 100, n + 100),
            "width": 200,
            "height": 200,
            "i": 100,
            "j": 100,
            "info_format": fmt,
            "feature_count": 10,
        },
    )


def esri_point(pt: dict[str, Any]) -> str:
    return json.dumps({"x": pt["e"], "y": pt["n"], "spatialReference": {"wkid": 27700}})


# --------------------------------------------------------------------------
# Probes. Each returns a one-line human summary, or raises.
# --------------------------------------------------------------------------


def postcode_url(pc: str) -> str:
    """Postcodes contain a space; urllib refuses it where curl silently encodes."""
    return "https://api.postcodes.io/postcodes/" + urllib.parse.quote(pc)


def probe_postcodes_io() -> str:
    d = get_json(postcode_url("SE1 9GF"))["result"]
    assert d["eastings"] == RICH["e"] and d["northings"] == RICH["n"], "coords moved"
    return "SE1 9GF -> E%d N%d / %.6f,%.6f country=%s" % (
        d["eastings"],
        d["northings"],
        d["latitude"],
        d["longitude"],
        d["country"],
    )


def probe_postcodes_io_country() -> str:
    """The country field is the England-only guard for every EA source."""
    out = []
    for pc in ("SE1 9GF", "EH1 1RE"):
        d = get_json(postcode_url(pc))["result"]
        out.append(pc + "=" + d["country"])
    return " ".join(out)


def probe_bgs_geology_50k() -> str:
    """BGS 1:50k via WMS GetFeatureInfo. OGL. Native EPSG:27700, GeoJSON out."""
    seen = []
    for pt in (RICH, SPARSE):
        raw = wms_feature_info(
            "https://map.bgs.ac.uk/arcgis/services/BGS_Detailed_Geology/MapServer/WMSServer",
            "BGS.50k.Bedrock,BGS.50k.Superficial.deposits",
            pt["e"],
            pt["n"],
            "application/geo+json",
        )
        d = json.loads(raw)
        names = [f["properties"]["LEX_D"] for f in d["features"]]
        assert names, "no geology at %s - coverage is national, so this is suspicious" % pt["name"]
        seen.append(pt["name"] + ": " + " / ".join(names))
    return " | ".join(seen)


def probe_bgs_geology_625k() -> str:
    """Openly-licensed fallback. text/plain and GML only - no GeoJSON."""
    raw = wms_feature_info(
        "https://ogc.bgs.ac.uk/cgi-bin/BGS_Bedrock_and_Superficial_Geology/wms",
        "GBR_BGS_625k_BLS,GBR_BGS_625k_SLS",
        RICH["e"],
        RICH["n"],
        "text/plain",
    ).decode("utf-8", "replace")
    lex = [ln.split("=", 1)[1].strip().strip("'") for ln in raw.splitlines() if "LEX_D =" in ln]
    assert lex, "no 625k features"
    return RICH["name"] + ": " + " / ".join(lex) + "  (Group rank - coarser than 50k)"


def probe_sobi_arcgis() -> str:
    """SOBI via ArcGIS: native EPSG:27700 and a true radius query."""
    url = "https://map.bgs.ac.uk/arcgis/rest/services/GeoIndex_Onshore/boreholes/MapServer/0/query"
    counts = {}
    for pt in (RICH, SPARSE, SCOT):
        d = get_json(
            url,
            {
                "geometry": "%d,%d" % (pt["e"], pt["n"]),
                "geometryType": "esriGeometryPoint",
                "inSR": 27700,
                "distance": 250,
                "units": "esriSRUnit_Meter",
                "spatialRel": "esriSpatialRelIntersects",
                "returnCountOnly": "true",
                "f": "json",
            },
        )
        counts[pt["name"]] = d["count"]
    assert counts[RICH["name"]] > 0, "expected boreholes at SE1"
    assert counts[SCOT["name"]] > 0, "BGS should cover Scotland"
    return "within 250m -> " + ", ".join("%s=%s" % kv for kv in counts.items())


def probe_sobi_ogcapi() -> str:
    """SOBI via OGC API Features (BETA): clean GeoJSON, but CRS84 bbox only."""
    d = get_json(
        "https://ogcapi.bgs.ac.uk/collections/onshoreboreholeindex/items",
        {"bbox": bbox_around(RICH["lon"], RICH["lat"], 250), "limit": 3, "f": "json"},
    )
    assert d["features"], "no boreholes returned"
    p = d["features"][0]["properties"]
    return "numberMatched=%s first=%r (%s, %sm, precision %s)" % (
        d["numberMatched"],
        p["name"],
        p["year_known"],
        p["length"],
        p["precision"],
    )


def probe_ea_flood_zones() -> str:
    """Flood Map for Planning. OGL v3. Native EPSG:27700. IGNORES DEFENCES."""
    base = (
        "https://services1.arcgis.com/JZM7qJpmv7vJ0Hzx/arcgis/rest/services"
        "/Flood_Map_for_Planning/FeatureServer"
    )
    out = []
    for pt in (RICH, SPARSE, SCOT):
        hits = []
        for layer, label in ((1, "FZ3"), (2, "FZ2")):
            d = get_json(
                "%s/%d/query" % (base, layer),
                {
                    "geometry": esri_point(pt),
                    "geometryType": "esriGeometryPoint",
                    "inSR": 27700,
                    "spatialRel": "esriSpatialRelIntersects",
                    "outFields": "type",
                    "returnGeometry": "false",
                    "f": "json",
                },
            )
            if d.get("features"):
                hits.append("%s(%s)" % (label, d["features"][0]["attributes"]["type"]))
        out.append(pt["name"] + "=" + (",".join(hits) if hits else "none"))
    return " | ".join(out) + "   <- 'none' for Edinburgh is OUT OF AREA, not safe"


def probe_ea_surface_water() -> str:
    """A point can sit outside every band while the layer is dense nearby."""
    base = (
        "https://services1.arcgis.com/JZM7qJpmv7vJ0Hzx/arcgis/rest/services"
        "/Risk_of_Flooding_from_Surface_Water_Extents/FeatureServer"
    )
    e, n = RICH["e"], RICH["n"]
    at_point, nearby = [], []
    for layer, label in ((0, "3.3%"), (1, "1%"), (2, "0.1%")):
        common = {
            "geometryType": "esriGeometryPoint",
            "inSR": 27700,
            "spatialRel": "esriSpatialRelIntersects",
            "returnCountOnly": "true",
            "f": "json",
        }
        d = get_json("%s/%d/query" % (base, layer), dict(common, geometry=esri_point(RICH)))
        at_point.append("%s=%s" % (label, d["count"]))

        env = json.dumps(
            {
                "xmin": e - 1000,
                "ymin": n - 1000,
                "xmax": e + 1000,
                "ymax": n + 1000,
                "spatialReference": {"wkid": 27700},
            }
        )
        d2 = get_json(
            "%s/%d/query" % (base, layer),
            dict(common, geometry=env, geometryType="esriGeometryEnvelope"),
        )
        nearby.append("%s=%s" % (label, d2["count"]))
    return "at SE1 [%s] but within 1km [%s]" % (", ".join(at_point), ", ".join(nearby))


def probe_ea_lidar() -> str:
    """EA LIDAR Composite 1m DTM. Bare earth. England only -> 'NoData' outside."""
    url = (
        "https://utility.arcgis.com/usrsvcs/servers/f9c4694d7d5140638536c4afe4119e6d"
        "/rest/services/LIDAR/LIDAR_Composite_1m_DTM/ImageServer/identify"
    )
    out = []
    for pt in (RICH, SPARSE, SCOT):
        d = get_json(
            url,
            {
                "geometry": esri_point(pt),
                "geometryType": "esriGeometryPoint",
                "returnGeometry": "false",
                "f": "json",
            },
        )
        items = (d.get("catalogItems") or {}).get("features") or []
        tile = items[0]["attributes"]["Name"] if items else "-"
        out.append("%s=%s (%s)" % (pt["name"], d["value"], tile))
    return " | ".join(out) + "   <- 'NoData' is distinguishable, unlike the flood zones"


def probe_elevation_fallbacks() -> str:
    """Third-party DSMs. NOT interchangeable with a bare-earth DTM."""
    d = get_json(
        "https://api.opentopodata.org/v1/eudem25m",
        {"locations": "%f,%f" % (RICH["lat"], RICH["lon"])},
    )
    eudem = d["results"][0]["elevation"]
    return "opentopodata eudem25m at SE1 = %.2fm vs EA LIDAR DTM 4.77m -> %.0fm apart" % (
        eudem,
        eudem - 4.77,
    )


def probe_proj_grids() -> str:
    """OSTN15 grid shift files, for sub-metre OSGB36 <-> WGS84 via pyproj."""
    ok = []
    for f in ("uk_os_OSTN15_NTv2_OSGBtoETRS.tif", "uk_os_OSGM15_GB.tif"):
        req = urllib.request.Request(
            "https://cdn.proj.org/" + f, headers={"User-Agent": UA, "Range": "bytes=0-1"}
        )
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            ok.append("%s=%d" % (f, r.status))
    return "PROJ CDN reachable: " + ", ".join(ok)


PROBES: list[tuple[str, Callable[[], str]]] = [
    ("postcodes.io   postcode -> coords", probe_postcodes_io),
    ("postcodes.io   country guard", probe_postcodes_io_country),
    ("BGS geology    1:50k  (OGL, WMS)", probe_bgs_geology_50k),
    ("BGS geology    1:625k (OGL, WMS)", probe_bgs_geology_625k),
    ("BGS SOBI       (ArcGIS, radius)", probe_sobi_arcgis),
    ("BGS SOBI       (OGC API, beta)", probe_sobi_ogcapi),
    ("EA flood zones (Flood Map for Planning)", probe_ea_flood_zones),
    ("EA surface water risk", probe_ea_surface_water),
    ("EA LIDAR       1m DTM", probe_ea_lidar),
    ("elevation      third-party fallbacks", probe_elevation_fallbacks),
    ("PROJ           OSTN15 grids", probe_proj_grids),
]


def main() -> int:
    # BGS returns non-ASCII (the +/- in borehole precision); the Windows console
    # defaults to cp1252 and mangles it.
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        pass

    failures = 0
    print("Deskbot source recon - %d probes" % len(PROBES))
    print("=" * 78)
    for label, fn in PROBES:
        try:
            summary = fn()
            print("[PASS] " + label + "\n       " + summary)
        except Exception as exc:  # throwaway: any failure is just a failure
            failures += 1
            print("[FAIL] " + label + "\n       %s: %s" % (type(exc).__name__, exc))
    print("=" * 78)
    print("%d/%d passed" % (len(PROBES) - failures, len(PROBES)))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
