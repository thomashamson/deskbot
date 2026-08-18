# Deskbot: UK public data source reconnaissance

**Status:** Reconnaissance findings (sections 1-9) plus the decisions taken from
them (section 10). Sections 3.3, 4.1, 5.5, 6, 7 and 10.5-10.10 were added or revised during
the build sessions and are marked where that matters.
**Date:** 2026-08-18
**Verified by:** `scripts/probe_sources.py` (stdlib only, 11/11 probes passing)
and the project test suite (`uv run pytest`).

Every endpoint below was called for real. Sample responses are genuine, trimmed
only for length. Where something is *not* verified, it says so explicitly.

**Built:** complete. Five tools (`locate`, `geology`, `boreholes`, `flood`,
`terrain`), the CLI, and the local advisory model. See the README for how the
parts fit together.

---

## 1. Test points

Three points, chosen to separate three failure modes that otherwise look identical.

| Role | Location | Easting | Northing | Lat | Lon |
|---|---|---|---|---|---|
| **RICH** | SE1 9GF, Southwark | 532785 | 180244 | 51.505538 | -0.088134 |
| **SPARSE** | NE19 centroid, Redesdale, Northumberland | 393851 | 587167 | 55.178704 | -2.098317 |
| **OUT OF AREA** | Edinburgh | 325200 | 673900 | 55.9486 | -3.1999 |

The sparse point is deliberately in rural **England**, not Scotland. A Scottish
point would conflate "no records at this location" with "this service does not
cover this country", and those need different wording in the output. Edinburgh
is carried as a separate third point purely to test the jurisdiction boundary.

---

## 2. Verdict summary

| Source | Key? | Licence | Format | Verdict |
|---|---|---|---|---|
| postcodes.io | No | OGL v3 | JSON | **Chosen** |
| ONS Countries BFC boundaries | No | OGL v3 | Esri JSON | **Chosen** (3.3) |
| BGS Geology 1:50k WMS | No | OGL + attribution | GeoJSON | **Chosen** |
| BGS SOBI via ArcGIS | No | OGL | Esri JSON | **Chosen** (10.1) |
| EA Flood Map for Planning | No | OGL v3 | Esri JSON | **Chosen**, England only |
| EA Surface Water risk | No | OGL v3 | Esri JSON | **Chosen**, England only |
| EA LIDAR 1m DTM (ImageServer) | No | OGL v3 | Esri JSON | **Chosen** (10.9), England only |
| pyproj + OSTN15 grids | No | MIT / OS open | n/a | Not needed (3.2) |
| BGS SOBI via OGC API | No | OGL | GeoJSON | Researched, not used (10.1) |
| BGS Geology 1:625k WMS | No | BGS open terms | text/plain, GML | Researched, not used (10.3) |
| EA LIDAR 1m DTM (DEFRA WMS) | No | OGL v3 | GeoJSON | Rate-limited, not used (10.9) |
| opentopodata / open-elevation | No | Mixed | JSON | **Flagged**, see 9.2 |
| DiGMapGB-50 bulk data | n/a | **Paid licence** | n/a | **Dropped** |
| OS Names / Places API | **Yes** | n/a | n/a | **Dropped** |
| OS Terrain 50 download | No | OGL | Bulk zip | Out of scope, see 9.3 |

"Researched, not used" means verified working and openly licensed, but not
selected. Those rows are kept deliberately: if a chosen source degrades, the
alternative is already proven rather than needing rediscovery.

Seven chosen sources map onto five tools: **locate** (postcodes.io + ONS
boundaries), **geology**, **boreholes**, **flood** (planning zones + surface
water), **terrain**. No coordinate-transform dependency is needed: every one of
them queries in EPSG:27700 natively.

---

## 3. Postcode and coordinates

### 3.1 postcodes.io

- **Endpoint:** `https://api.postcodes.io/postcodes/{postcode}`
- **Also:** `/outcodes/{outcode}` for a district centroid, `/postcodes?lon=&lat=` for reverse lookup
- **Key:** none
- **Licence:** OGL v3 (derives from ONS Postcode Directory and OS Open Data)
- **Format:** JSON
- **Response time:** ~110 ms

Returns OSGB36 eastings/northings **and** WGS84 lat/lon in one call, so for
postcode input there is no transform to perform at all.

```json
{"status":200,"result":{
  "postcode":"SE1 9GF","quality":1,
  "eastings":532785,"northings":180244,
  "longitude":-0.088134,"latitude":51.505538,
  "country":"England","region":"London",
  "admin_district":"Southwark","parish":"Southwark, unparished area",
  "lsoa":"Southwark 006F","codes":{"admin_district":"E09000028"}
}}
```

Two things beyond coordinates matter here:

- **`country`** is the guard for every Environment Agency source (section 6).
  Verified: `SE1 9GF -> England`, `EH1 1RE -> Scotland`.
- **`quality`** is a positional-accuracy code. `1` is the best. This should be
  surfaced, not swallowed, since it bounds the precision of everything downstream.

**Gotcha:** the postcode contains a space. `curl` encodes it silently, Python's
`urllib` raises `InvalidURL`. Encode the path segment explicitly.

### 3.2 OSGB36 <-> WGS84

There is **no free keyless web service** for arbitrary coordinate conversion, so
this has to be local. That is the right answer anyway: it is pure computation and
needs no outbound call.

- **Library:** `pyproj`, EPSG:27700 <-> EPSG:4326
- **Licence:** pyproj is MIT; the OSTN15 transform is published openly by OS

**Verified:** the high-accuracy OSTN15/OSGM15 grid-shift files are reachable on
the PROJ CDN and return HTTP 206 to a range request:

- `https://cdn.proj.org/uk_os_OSTN15_NTv2_OSGBtoETRS.tif`
- `https://cdn.proj.org/uk_os_OSGM15_GB.tif`

**Not yet verified:** whether the installed `pyproj` wheel bundles these or
fetches them at runtime. This matters: without the grid, PROJ silently falls back
to a Helmert approximation with roughly 5 m of error instead of about 0.1 m.
Silently, with no exception. Confirm at build time and consider vendoring the
grid, or setting `PROJ_NETWORK=ON` deliberately.

Grid reference parsing (`TQ 32785 80244` -> `532785, 180244`) is plain arithmetic
on the 100 km letter pairs. No service required.

**Cross-check available:** postcodes.io returns both systems for the same point,
which gives a free ground-truth pair for testing any transform we write.

**Update (build session):** the project takes no `pyproj` dependency. Every chosen
source queries in EPSG:27700 natively, so nothing needs a datum shift. Latitude
and longitude are recorded only where postcodes.io supplies them, and are never
derived. The OSTN15 question is therefore deferred rather than answered.

### 3.3 ONS country boundaries -- the England-only gate

Added during the build session. Section 9.4 established that the country must be
known *before* any Environment Agency source is called. postcodes.io supplies
`country`, but a grid reference carries none, and the obvious substitute --
reverse geocoding to the nearest postcode -- can land in the wrong country near
a border. The sparse test point sits about 15 km from Scotland.

- **Endpoint:** `https://services1.arcgis.com/ESMARspQHYMw9BZ9/arcgis/rest/services/Countries_December_2025_Boundaries_UK_BFC/FeatureServer/0/query`
- **Key:** none, `access: public`
- **CRS:** `EPSG:27700` native
- **Licence:** OGL v3. Two attributions are required verbatim:
  `Source: Office for National Statistics licensed under the Open Government Licence v.3.0`
  and `Contains OS data © Crown copyright and database right [year]`

`BFC` is full resolution, clipped to the coastline -- chosen over the generalised
`BGC`/`BUC` variants because a generalised boundary can misclassify a point near
a land border, which is the case this exists to get right.

```json
{"attributes":{"CTRY25CD":"E92000001","CTRY25NM":"England","CTRY25NMW":"Lloegr"}}
```

Verified: SE1 and NE19 both England, Edinburgh and Carter Bar both Scotland, and
a point north of Shetland returns `features: []`.

**Coastline caveat.** Because the boundaries are clipped to the coast, a genuine
site on a pier, quay or estuary edge can fall marginally outside every polygon.
A second pass therefore retries with a 1 km tolerance and flags the result as
approximate, rather than rejecting the site outright. An empty result after that
means outside the UK.

---

## 4. Geology

### 4.1 BGS Geology 1:50k -- the one to use

- **Endpoint:** `https://map.bgs.ac.uk/arcgis/services/BGS_Detailed_Geology/MapServer/WMSServer`
- **Operation:** WMS 1.3.0 `GetFeatureInfo`
- **Key:** none. Capabilities declares `<Fees>none</Fees>`
- **Format:** `application/geo+json` (also HTML, GML, plain text)
- **CRS:** `EPSG:27700` natively, so queries use eastings/northings directly
- **Layers:** `BGS.50k.Bedrock`, `BGS.50k.Superficial.deposits`,
  `BGS.50k.Artificial.ground`, `BGS.50k.Mass.movement`, `BGS.50k.Linear.features`

**Licence -- resolved, and this was the session's biggest open question.**
The response embeds `"USAGE": "© UKRI www.bgs.ac.uk/ipr"`, and the *bulk*
DiGMapGB-50 dataset does require a paid commercial licence. But the **WMS itself**
is separately and explicitly OGL. Verbatim from BGS:

> "Terms of use -- This data is delivered under the terms of the Open Government
> Licence, subject to the following acknowledgement accompanying the reproduced
> BGS materials: 'Contains British Geological Survey materials © UKRI [year]'."

Source: <https://www.bgs.ac.uk/technologies/web-map-services-wms/web-map-services-geology-50k/>

So the WMS is usable, and that attribution string is **mandatory** in output.
The bulk data product is dropped (section 9.1).

Sample, SE1, bedrock feature, trimmed from about 40 properties:

```json
{"type":"Feature","geometry":null,"properties":{
  "LEX_D":"London Clay Formation",
  "RCS_D":"Clay, silt and sand",
  "BGSTYPE":"Bedrock",
  "MAX_PERIOD":"Palaeogene","MAX_EPOCH":"Eocene","MAX_TIME_D":"Ypresian Age",
  "GP_EQ_D":"Thames Group","RANK":"Formation",
  "BROAD_D":"mud and sand","SETTING_D":"deep seas",
  "ENVIRONM_D":"These sedimentary rocks are marine in origin...",
  "LEX_WEB":"https://webapps.bgs.ac.uk/lexicon/lexicon.cfm?pub=LC",
  "MAP_SRC":"ew256_North_London","NOM_SCALE":"50000","VERSION":"9.25",
  "USAGE":"© UKRI www.bgs.ac.uk/ipr"
},"layerName":"BGS.50k.Bedrock"}
```

Verified results at both points:

| Point | Bedrock | Superficial |
|---|---|---|
| SE1 | London Clay Formation | Kempton Park Gravel Member |
| NE19 | Tyne Limestone Formation | Till, Devensian |

Both are correct for their locations. `LEX_WEB` gives a per-unit BGS Lexicon URL
and `MAP_SRC` names the source map sheet, which is ideal for per-claim attribution.

**Note:** geology is a *complete polygon coverage*. Unlike boreholes, it returns
something almost everywhere, and an empty result means the point is outside the
mapped layer (for example no artificial ground present), not that data is missing.

**Constraint:** the ArcGIS REST `identify` operation on this service is disabled
(`capabilities: Map` only, no `Query`). WMS `GetFeatureInfo` is the only route.

**Layer behaviour (build session).** All four polygon layers are queryable in a
single call, with features tagged by `layerName`. Artificial ground and mass
movement are genuinely sparse rather than broken -- verified positives:

| Layer | Test point | Returns |
|---|---|---|
| Artificial ground | Canary Wharf, E537500 N180400 | Infilled Ground (the filled West India Docks) |
| Mass movement | Black Ven, Charmouth, E335500 N93000 | Landslide deposits |

`BGS.50k.Linear.features` is **not usable from a point query**. It holds faults
and other lines, and a point essentially never intersects a line: verified
returning nothing at four locations including the Craven Fault zone and the
Black Ven landslide. Querying it would report "no faults" everywhere. It is
therefore never called, and faults are reported as a standing gap (10.6).

Artificial ground and mass movement carry the same schema as bedrock but omit
the descriptive fields (`BROAD_D`, `SETTING_D`, `ENVIRONM_D`), so those are
optional. `BROAD_D` also arrives as a single space rather than empty, and
`RANK`/`GP_EQ_D`/`MAX_EPOCH` use the literals `Not Applicable`, `No Parent` and
`Not Defined` -- all of which must be normalised away rather than rendered.

### 4.2 BGS Geology 1:625k -- open fallback

- **Endpoint:** `https://ogc.bgs.ac.uk/cgi-bin/BGS_Bedrock_and_Superficial_Geology/wms`
- **Layers:** `GBR_BGS_625k_BLS` (bedrock lithostratigraphy), `GBR_BGS_625k_SLS` (superficial), `GBR_BGS_625k_BA` (age), plus lithology variants
- **Key:** none
- **Format:** `text/plain`, `application/vnd.ogc.gml`, `text/html`. **No GeoJSON.**
- **Licence:** the most permissive of anything found. Verbatim from `AccessConstraints`:

> "The 1:625k DiGMap data is made available for all uses - including commercial
> use, however the British Geological Survey (BGS) at all times retains the
> copyright in this material and you are not permitted, without an appropriate
> licence, to set up a service selling on this material."

A CLI that drafts a summary is not "selling on this material", so this is clear.

**Why it is a fallback, not the primary.** It is materially coarser:

| Point | 1:50k | 1:625k |
|---|---|---|
| SE1 bedrock | London Clay **Formation** | Thames **Group** |
| NE19 bedrock | Tyne Limestone **Formation** | Yoredale **Group** |
| SE1 superficial | Kempton Park Gravel Member | **Alluvium** |

The bedrock differences are just rank (Formation vs Group) and are consistent.
The **superficial difference is not** -- alluvium and terrace gravel are
different deposits with different engineering behaviour. At 1:625k a point can
land in a genuinely different unit. Do not present the two interchangeably.

---

## 5. Boreholes (BGS SOBI)

Two independent routes, both OGL, both keyless. They have different strengths.

**Licence:** the Single Onshore Borehole Index is free for commercial, research
and public use under the Open Government Licence, and the scanned records are
free to view, print or download under the same terms.
Source: <https://www.bgs.ac.uk/datasets/boreholes-index/>

### 5.1 ArcGIS route -- native grid, true radius

- **Endpoint:** `https://map.bgs.ac.uk/arcgis/rest/services/GeoIndex_Onshore/boreholes/MapServer/0/query`
- **Capabilities:** `Map,Query,Data`. `maxRecordCount` 2000
- **CRS:** `EPSG:27700` native
- **Supports:** genuine radius search via `distance` + `units=esriSRUnit_Meter`, and `returnCountOnly`

Layer 0 is `Borehole.records`. The same MapServer also carries `Water.wells`,
`Site.investigation.reports`, `Drillcore` and others (layers 1-10), not
investigated this session.

Sample, within 250 m of SE1:

```json
{"attributes":{
  "REFERENCE":"TQ38SW3827","NAME":"HAYS WHARF 2",
  "EASTING":532974.0,"NORTHING":180302.0,
  "LENGTH":35.0,"YEAR_KNOWN":"1982",
  "SCAN_URL":"https://api.bgs.ac.uk/sobi-scans/v1/borehole/scans/items/13601099",
  "AGS_LOG_URL":null,"HELD_AT":"KW"}}
```

Verified counts within 250 m: **SE1 = 86**, **NE19 = 0**, **Edinburgh = 71**.
At 1 km SE1 returns 1109, and NE19 still 0 (117 at 5 km).

### 5.2 OGC API Features route -- cleaner payload

- **Endpoint:** `https://ogcapi.bgs.ac.uk/collections/onshoreboreholeindex/items`
- **Status:** BETA, per BGS
- **Format:** proper GeoJSON, snake_case properties
- **Pagination:** real -- `numberMatched`, `numberReturned`, and a `next` link
- **CRS:** **CRS84 only.** A `bbox-crs` of EPSG:27700 is rejected

```json
{"type":"Feature","id":1063247.0,
 "geometry":{"type":"Point","coordinates":[-0.08931636847532251,51.50372102866996]},
 "properties":{
   "reference":"TQ38SW10","name":"GUYS HOSPITAL SOUTHWARK",
   "grid_ref":"TQ 32710 80040","easting":532710.0,"northing":180040.0,
   "precision":"± 10 METRES","length":132.89,"year_known":"1859",
   "held_at":"KW","scan_url":"https://api.bgs.ac.uk/sobi-scans/v1/borehole/scans/items/1063247",
   "ags_log_url":null,"scan_quality":"not Entered"}}
```

### 5.3 Choosing between them

| | ArcGIS | OGC API |
|---|---|---|
| CRS | 27700 native | CRS84 only, needs a transform first |
| Search shape | true radius | bbox only, so a square not a circle |
| Payload | Esri JSON | clean GeoJSON |
| Uncertainty field | `PRECISION`, same values | `precision`, e.g. "± 10 METRES" |
| Count without fetching | `returnCountOnly` | `numberMatched` |
| Stability | long-standing | **beta** |

Genuine trade-off, deferred to the build session (section 10). Note that
`PRECISION` is available from **both** routes -- it simply has to be named in
`outFields` on the ArcGIS one, which is easy to miss.

A bbox is not a radius. A bbox of half-width *r* reaches *r*&#8730;2 at its
corners, so a "250 m" bbox search returns records up to 354 m away. For a
document that attributes each claim to a stated search distance, that is a
misstatement rather than a rounding issue.

### 5.4 Borehole gotchas

- **`LENGTH: -1.0` is a sentinel for "unknown", not a depth.** Seen on the
  Jubilee Line Extension records. Rendering that as "-1 m deep" would be wrong.
- **`SCAN_URL` is not always a scan, and not always a URL.** Three shapes occur:
  `https://api.bgs.ac.uk/sobi-scans/v1/...` (free under OGL),
  `http://shop.bgs.ac.uk/GeoRecords` (the BGS shop, so a purchase rather than a
  free record), and the **literal string `Not Available`**, which is neither a
  URL nor null and would render as a broken link if passed through.
- **`maxRecordCount` is 2000** and SE1 already returns 1109 within 1 km. Any
  radius much beyond that in a city will silently truncate. Check
  `exceededTransferLimit`.
- **`year_known` is a string and is often null.** Values as early as 1859 appear
  (Guy's Hospital). Verified nulls in the same 250 m result set as populated
  years, so it cannot be treated as always present. Old records may also predate
  modern datums and logging standards.

**How common the gaps are (build session).** Measured across the 86 records
within 250 m of SE1, so these are the norm rather than edge cases:

| Field | Missing | Meaning |
|---|---|---|
| `LENGTH` | 32 of 86 (37%) carry `-1` | depth unknown, not zero |
| `YEAR_KNOWN` | 21 of 86 (24%) null | undated |
| `SCAN_URL` | 16 of 86 (19%) `Not Available` | no scan; 16 more are shop links |
| `AGS_LOG_URL` | present on only 12 of 86 | digital AGS data is the exception |
| `PRECISION` | 16 of 86 `NOT AVAILABLE` | others are `± 10 METRES` or `± METRE` |

A tool that renders these literally would report boreholes "-1 m deep" with a
link labelled "Not Available" for over a third of a typical result set.

**Pagination is supported** (`supportsPagination`, `supportsOrderBy` and
`supportsStatistics` are all true on layer 0), so the 2000 cap can be worked
past with `resultOffset` rather than accepted as truncation. Distance is not a
field, so nearest-first ordering has to be done client-side over the full set.

---

### 5.5 The other SOBI layers, and one serious trap

The same MapServer carries ten further layers. Counts within 250 m of SE1:
`Water.wells` 0, `Drillcore` 0, **`Site.investigation.reports` 663**.

That 663 is not a proximity result, and this is the most dangerous number found
anywhere in this reconnaissance.

`Site.investigation.reports` is a **polygon** layer, and every polygon is a
5 km × 5 km box (`SHAPE_Area: 25000000`, `SWE` 530000 to `NEE` 535000, `SWN`
180000 to `NEN` 185000). Each report is indexed to a grid square, not to its
location. One of the two records sampled at SE1 is titled
`166-170 BISHOPSGATE LONDON EC2` -- over a kilometre away, on the other side of
the river, and returned because its 25 km² index square happens to contain the
query point.

Reporting "663 site investigation reports within 250 m" would be wrong by three
orders of magnitude in area, while looking more precise than any other figure in
the report. The layer is therefore **not used**. It is recorded here so nobody
rediscovers it and wires it up on the strength of the count alone.

If it is ever wanted, the only honest framing is "N reports are indexed to the
surrounding 5 km square", never a distance.

---

## 6. Flood risk (Environment Agency)

**England only.** This is the single most important caveat in this document; see 9.4.

### 6.1 Flood Map for Planning (rivers and sea)

- **Endpoint:** `https://services1.arcgis.com/JZM7qJpmv7vJ0Hzx/arcgis/rest/services/Flood_Map_for_Planning/FeatureServer`
- **Layers:** `0` Flood Storage Areas, `1` Flood Zone 3, `2` Flood Zone 2
- **Key:** none, `access: public`
- **CRS:** `EPSG:27700` native
- **Licence:** OGL v3, stated per-dataset on the item record. Attribution:
  `© Environment Agency copyright and/or database right 2024. All rights reserved.`
  (also contains CEH and OS-derived material -- see the full `accessInformation`)

```json
{"attributes":{"type":"Tidal Models","layer":"Flood Zone 3","OBJECTID":94492}}
```

Verified: **SE1 = FZ3 and FZ2, both "Tidal Models"**. NE19 = none. Edinburgh = none.

**Interpretation caveat, and it is a serious one.** The Flood Map for Planning
deliberately **ignores flood defences**. SE1 being in Flood Zone 3 does *not*
mean high actual risk -- it sits behind the Thames Barrier. Reporting "Flood
Zone 3" without that qualification would materially mislead a reader. This is a
planning-policy dataset, not a residual-risk assessment.

**Measured detail (build session).**

- `type` takes three values: `Fluvial Models`, `Tidal Models`,
  `Fluvial / Tidal Models`. This names the flood source and is worth surfacing.
- Flood Zone 3 is 231,054 polygons nationally; **flood storage areas are only
  509**. A site inside one is rare and significant, since it is an area
  engineered to flood deliberately.
- Proximity queries work on these layers via `distance` + `units`, and are
  informative. At NE19 there is no Flood Zone 3 within 500 m but there is one at
  1000 m, which locates the Rede floodplain without a second dataset.

### 6.2 Risk of Flooding from Surface Water

- **Endpoint:** `https://services1.arcgis.com/JZM7qJpmv7vJ0Hzx/arcgis/rest/services/Risk_of_Flooding_from_Surface_Water_Extents/FeatureServer`
- **Layers:** `0` = 3.3% annual chance, `1` = 1%, `2` = 0.1%
- **Licence:** OGL v3

Verified at SE1: the point is in **none** of the three bands, yet within 1 km
there are **286 / 649 / 1304** features respectively. The counts rising as
probability falls is physically correct -- rarer events flood wider areas -- and
confirms the query is well formed rather than silently broken.

**This is why proximity is reported (build session).** At SE1 there is nothing at
the point, nothing within 50 m, one extent within 100 m, and 32 within 250 m.
Reporting only the point would describe that site as not at risk from surface
water, which is true of the point and misleading about the site. Each extent also
carries `PUB_DATE` -- 2022-06-29 at SE1 -- so the report can say how old the
mapping is rather than implying a live reading.

That check is worth keeping. An empty point-in-polygon result and a malformed
query look identical, and here the difference is "no surface water risk" versus
"we failed to ask properly".

### 6.3 Also present, not investigated

The EA org publishes 116 services. `FRR_C2_RoFRS_*` and `FRR_C2_RoFSW_*`
(receptor risk maps), `Thames_Estuary_2100_Flood_Zone_2`, and the separate
real-time `flood-monitoring` API (live warnings, OGL v3, explicitly beta) are
all out of scope for this session.

---

## 7. Terrain and elevation

### 7.1 EA LIDAR Composite 1 m DTM -- the authoritative option

Two routes to the same underlying 2025 composite. Both keyless, both OGL v3,
both England only.

**Route A -- ImageServer `identify`**

`https://utility.arcgis.com/usrsvcs/servers/f9c4694d7d5140638536c4afe4119e6d/rest/services/LIDAR/LIDAR_Composite_1m_DTM/ImageServer/identify`

```json
{"objectId":0,"name":"Pixel","value":"4.76984",
 "location":{"x":532785,"y":180244,"spatialReference":{"wkid":27700}},
 "catalogItems":{"features":[{"attributes":{
   "Name":"TQ38sw_DTM_1m","ProductName":"LIDAR Composite",
   "ModelType":"Digital Terrain Model (DTM)","LIDARRes":"1m",
   "LIDARRtn":"Last Return","OSGridRef":"TQ38sw","CompYear":1748736000000}}]}}
```

**Route B -- DEFRA WMS `GetFeatureInfo`**

`https://environment.data.gov.uk/spatialdata/lidar-composite-digital-terrain-model-dtm-1m/wms`
with `layers=Lidar_Composite_Elevation_DTM_1m`, `info_format=application/json`,
`crs=EPSG:27700`.

```json
{"type":"FeatureCollection","features":[
  {"type":"Feature","geometry":null,"properties":{"Elevation":4.769163131713867}}]}
```

Verified values, and the two routes agree to within a millimetre:

| Point | Route A | Route B |
|---|---|---|
| SE1 | 4.76984 | 4.769163 |
| NE19 | 263.713 | 263.713 |
| Edinburgh | `"NoData"` | `"features": []` |

**The difference that matters is the last row.** Route A returns an explicit
`"NoData"` string and an empty `catalogItems`, which is unambiguous. Route B
returns an empty feature array, which is indistinguishable from any other
empty result. Route A also carries provenance -- which tile, what resolution,
which return, which composite year -- which suits per-claim attribution.

Against that, Route A's URL is a proxied
`utility.arcgis.com/usrsvcs/servers/<opaque hash>/` path, which looks
considerably more fragile than a `environment.data.gov.uk` address. Trade-off
deferred to section 10.

### 7.2 Third-party elevation -- flagged, do not mix

| Source | SE1 elevation |
|---|---|
| EA LIDAR 1 m **DTM** (bare earth) | **4.77 m** |
| `api.opentopodata.org` `eudem25m` | 13.12 m |
| `api.open-elevation.com` | 12.00 m |

About 8 m apart, and not because anything is broken. EU-DEM and SRTM-derived
products are coarse (25 m) and include built structures, so in dense urban
London they are measuring rooftops, not ground. The EA product is true bare
earth at 1 m.

**These are different physical quantities and must not be used as silent
fallbacks for one another.** For a geotechnical desk study, quoting a rooftop
height as ground level is a real defect. If a non-England fallback is ever
wanted, it must be labelled as a different measurement with its own caveat.

Both are also third-party services outside UK government (opentopodata's public
instance is rate limited and asks for fair use). No key required for either.

---

## 8. Cross-cutting gotchas

1. **ArcGIS returns HTTP 200 with an error body.** `{"error":{"code":400,...}}`
   arrives with a 200 status. Status-code checks alone are not a reachability
   test. This produced four false positives during recon before it was caught,
   and the probe script now inspects every payload.
2. **Empty is ambiguous almost everywhere.** `{"count":0}`, `"features":[]` and
   "no data for your country" are frequently the same response. Section 9.4.
3. **Postcodes need URL encoding.** `curl` hides this; `urllib` does not.
4. **Sentinel values.** `LENGTH: -1.0` means unknown, `"NoData"` is a string not
   a number, `BROAD_D` is sometimes a single space rather than empty.
5. **Windows console encoding.** BGS returns `±` in `precision`; cp1252 mangles
   it. Force UTF-8 on stdout.
6. **`maxRecordCount` 2000** on both BGS and EA ArcGIS services.

---

## 9. Flagged and dropped

### 9.1 Dropped -- DiGMapGB-50 bulk data (costs money)

The 1:50,000 geology **dataset** is licensed commercially: "subject to the
number of users, licence fee and data preparation fee", under BGS Digital Data
Licence terms. Dropped, per the project rule.

Sources: <https://www.bgs.ac.uk/datasets/bgs-geology-50k-digmapgb/> and
<https://www.bgs.ac.uk/information-hub/licensing/>. Both checked 2026-08-18.
BGS direct licensing enquiries to iprdigital@bgs.ac.uk; nothing here should be
taken as a statement of their current commercial terms.

This does **not** affect the 1:50k **WMS**, which is separately OGL (section 4.1).
Worth keeping the distinction clear so nobody later assumes the whole thing is off limits.

### 9.2 Flagged -- third-party elevation APIs

Not dropped on licensing (no key, no cost), but flagged on **fitness**: they
measure a different thing to the EA DTM. See 7.2.

### 9.3 Dropped -- OS Data Hub APIs (needs a key)

**Verified, not assumed:** `https://api.os.uk/search/names/v1/find` returns
**HTTP 401** without an API key. Dropped.

The OS **Downloads** API is genuinely open (`https://api.os.uk/downloads/v1/products`
returns 200 with no key, and OS Terrain 50 is listed there under OGL). But it
serves bulk zip files for offline use, not point queries, so it is out of scope
for a lightweight CLI. Noted in case an offline mode is ever wanted.

### 9.4 The England-only problem -- the most important finding

The EA services cover **England only**. BGS covers **Great Britain**. Verified at
Edinburgh:

| Service | Response at Edinburgh | Safe to interpret? |
|---|---|---|
| BGS SOBI | 71 boreholes within 250 m | Yes, BGS is GB-wide |
| BGS geology 50k | returns Scottish geology | Yes |
| EA LIDAR (ImageServer) | `"value":"NoData"` | Yes, distinguishable |
| EA LIDAR (WMS) | `"features":[]` | **No, ambiguous** |
| EA Flood Zone 2 and 3 | `{"count":0}` | **No, ambiguous** |
| EA Surface Water | `{"count":0}` | **No, ambiguous** |

A Scottish or Welsh site returns `count: 0` from the flood endpoints, which
reads exactly like "not in a flood zone". Rendered naively that becomes **"no
flood risk identified"** for a site nobody checked. For a tool whose entire
premise is attributing claims to sources, that is the worst available failure.

Mitigation: `postcodes.io` returns `country`, so the country is known before any
EA call is made. The tool must gate on it and say "not assessed: Environment
Agency data covers England only" rather than reporting an absence of risk.

Scotland is served by SEPA and Wales by NRW. Neither was investigated this
session.

Wording alone does not fix this. See **section 10.4** for the structural
requirement: "not assessed" must be a distinct state in the data model and a
visibly distinct block in the output, not a normal section that happens to be
empty.

---

## 10. Decisions taken

Settled at the end of session 1. Recorded here with reasoning so they can be
revisited on evidence rather than re-argued from scratch.

### 10.1 Boreholes: **ArcGIS route**

`GeoIndex_Onshore/boreholes/MapServer/0/query`, not the OGC API.

Takes eastings/northings natively so no transform sits in the query path;
performs a true radius search; and `PRECISION` turns out to be available here
too once named in `outFields`, which removed the OGC API's main advantage. It is
also not beta.

The decider was search shape. A bbox of half-width *r* reaches *r*&#8730;2 at its
corners, so a "250 m" bbox returns records up to 354 m away. A tool that
attributes every claim to a stated search radius must actually use that radius.

*Revisit if:* the OGC API leaves beta and gains `bbox-crs` support for EPSG:27700.

### 10.2 Terrain: **DEFRA WMS** -- REVERSED, see 10.9

> **Superseded.** Measurement during the build session showed the WMS
> rate-limits to roughly one request per second and answers with a bare `403`.
> The decision below was sound on the evidence available when it was taken; the
> reasoning is kept because the trade-off it weighs is still the right one, only
> the measurement changed. See **10.9**.

`environment.data.gov.uk/spatialdata/lidar-composite-digital-terrain-model-dtm-1m/wms`,
not the ImageServer.

Both return the same value to within a millimetre. The WMS returns an ambiguous
empty array where the ImageServer returns an explicit `"NoData"`, but that
ambiguity **is** solvable at our end: the country is already known from
postcodes.io, so "in England and empty" can be reported honestly as "no LIDAR
coverage at this point". The ImageServer's weakness -- an opaque
`utility.arcgis.com/usrsvcs/<hash>/` proxy URL -- is not something we can
mitigate.

*Accepted cost:* we lose per-tile provenance (tile name, resolution, composite
year). Attribution falls back to the dataset level: "EA LIDAR Composite 1 m DTM,
OGL v3".

*Revisit if:* the WMS proves unreliable, or per-tile provenance turns out to
matter more than expected in the drafted output.

### 10.3 Geology: **1:50k only, drop 1:625k**

The 1:50k WMS is OGL, covers GB, returns GeoJSON, and is finer. Carrying 625k as
well would mean a second response format (`text/plain`, no GeoJSON), a second
licence to track, and a genuine correctness hazard: at SE1 it reports *Alluvium*
where 50k reports *Kempton Park Gravel Member*, which are different deposits with
different engineering behaviour.

Section 4.2 stays in this document as a researched fallback, not as code.

*Revisit if:* BGS changes 1:50k WMS access or licensing.

### 10.4 Outside England: **partial report with a structurally explicit gap**

Return geology and boreholes, which are valid GB-wide, and state plainly that
flood risk and terrain were **not assessed** because Environment Agency data
covers England only, pointing at SEPA (Scotland) and NRW (Wales).

Refusing outright would discard good BGS coverage for a site the user asked
about. Reporting `count: 0` as "no flood risk" is the one genuinely unacceptable
option (section 9.4).

**Constraint: this is a structural distinction, not a wording one.**

Correct wording is necessary but not sufficient. A flood section that renders in
its normal form and simply happens to contain no findings reads, to anyone
skimming, as "they checked and it was fine". The gap has to be *visible as a
gap*, and it has to survive skimming.

Three states must therefore be distinguishable in the data model, not collapsed
into an empty list or a null:

| State | Meaning | Example |
|---|---|---|
| `assessed`, findings | queried, results returned | SE1: Flood Zone 3 (tidal) |
| `assessed`, none | queried successfully, genuinely nothing there | NE19: no flood zone at this point |
| **`not_assessed`** | never queried, or out of coverage | Edinburgh: EA covers England only |

This is precisely the distinction the sparse control point exists to test. NE19
and Edinburgh both produce zero flood features, and they must not render alike:
NE19 is a finding, Edinburgh is an absence of one.

`not_assessed` must carry a reason and must not be rendered by the same code path
that renders a populated section. It should also propagate upward, so a summary
or header shows the report is partial without the reader reaching the section
itself.

**This extends beyond jurisdiction.** A timed-out or failing EA call is also
"we do not know", and must land in `not_assessed` with a different reason -- never
silently as an empty section. Given that ArcGIS returns errors under HTTP 200
(section 8.1), a client that only checks status codes would produce exactly the
false reassurance this rule exists to prevent.

*Not planned:* SEPA and NRW equivalents. Adding them would exceed the tool budget.

### 10.5 Search radius is reconciled against location precision

A radius search only means something relative to a point. Searching 250 m around
the centre of a 1 km grid square covers roughly a fifth of the area the reference
denotes, and the true site may be 700 m from anything found -- yet the results
would be reported as "within 250 m of the site". That is the same false
precision as reporting an unchecked flood zone as "no risk", arrived at by
arithmetic instead of jurisdiction.

So precision is a **behaviour**, not a caveat:

- Location uncertainty is the half-diagonal of the denoted square (707 m for a
  1 km reference, not 500 m), or for postcodes derives from the OS positional
  quality indicator.
- The requested radius is **widened** to cover it, and the resulting claim is
  restated in terms of what was genuinely searched.
- Beyond a 1 km effective radius the search is **refused** as
  `insufficient_location_precision` rather than answered misleadingly.

The 1 km ceiling is not arbitrary: BGS caps responses at 2000 records and SE1
already returns 1109 boreholes within 1 km (section 5.4), so a wider search
risks silent truncation -- trading one false precision for another.

| Input | Uncertainty | 250 m requested | Outcome |
|---|---|---|---|
| 1 m grid reference | 0.7 m | 251 m | proceed, claim unchanged |
| 100 m grid reference | 71 m | 321 m | proceed, claim restated |
| 1 km grid reference | 707 m | 957 m | proceed, claim restated |
| Postcode, sector mean | 5000 m | 5250 m | **refused** |

### 10.6 Geology reports per layer, and admits what it cannot see

Four decisions, all following from the same principle as 10.4.

**Per-layer results, not one flat list of units.** A single list cannot express
"artificial ground was checked and there is none here" -- that absence is
indistinguishable from never having asked. Each of bedrock, superficial,
artificial ground and mass movement therefore carries its own
assessed-or-not result.

**Faults are a standing gap**, reason `not_queryable_at_a_point`. This is a
limitation of our method rather than of coverage, and it is the first case where
the tool reports its *own* blind spot rather than a source's. A desk study that
silently omits faults invites the reader to assume they were considered.

**No bedrock means off the map**, not "no rock". Bedrock is a complete onshore
coverage, so an empty result puts the whole geology report into
`outside_coverage`. Verified: the one probe returning nothing (E456000 N77000)
is genuinely offshore, with ONS confirming no country.

**Location uncertainty is sampled, not just noted.** Where precision exceeds
50 m, the four corners of the uncertainty square are queried too and any
divergence is reported *by name*:

> The location is uncertain to ±500 m and spans more than one mapped unit:
> superficial deposits spans Alluvium and Langley Silt Member. The site may sit
> on any of these.

That is real output for `TQ 32 80`. Naming the units matters: "spans more than
one mapped unit" sends the reader off to look it up, while naming them is
directly actionable. Bedrock stays London Clay across all five points there, so
a layer is only reported as varying when it genuinely does.

Sampling costs five calls instead of one, but only when it can change the
answer. A good postcode or a 1 m grid reference stays at a single call, since
corners ten metres apart would re-report the same polygon.

### 10.7 Boreholes report an exact count and an honest sample

**Default radius 250 m.** The standard desk-study screening distance, and it
keeps the precision gate usable: 250 m plus the 707 m uncertainty of a 1 km grid
reference is 957 m, just inside the ceiling. A 500 m default would refuse every
kilometre-precision reference outright.

**The count is exact; the listing is a sample.** A separate count query gives the
true total, then records are fetched (paginating past the 2000 cap where needed),
sorted by distance client-side, and the nearest 20 listed. An explicit flag marks
the listing as a subset, so twenty entries can never be read as twenty records
existing.

**Distance does more work than the count.** The summary names the nearest record
and how far the listing reaches, because a count alone is misleading in both
directions -- a thousand records mean little if the nearest is 240 m away, and
one record on the site is worth more than fifty at the edge. Real output:

> 89 borehole records within 260 m of the postcode centroid, widened from 250 m
> to cover a location uncertainty of 10 m, nearest at 4 m; the closest 20 are
> listed, out to 59 m.

Contrast `TQ 32 80`, which returns 1,331 records but with the nearest at 58 m.
Same tool, opposite screening conclusion, and the counts alone would not have
distinguished them.

**No country gate.** SOBI is Great Britain wide, verified returning 153 records
at Edinburgh. Zero records is a real finding here, unlike the Environment Agency
sources where zero can mean out of area.

**Site investigation reports are excluded** on the 5 km indexing grounds in 5.5.

### 10.8 Flood gates on country, reports proximity, and labels the zone honestly

**The country gate runs before any request.** Both services answer an out-of-area
query with zero results, so asking at all outside England invites a false
negative. Verified: Edinburgh and Cardiff both produce a gap naming the country
and pointing at SEPA or NRW, and the summary states the gap rather than an
absence of risk.

**Every dataset stays visible, even when clear.** All six are reported for every
lookup, including those with nothing at the location. Dropping a clear dataset
would make "checked, clear" indistinguishable from "never checked" -- the same
failure as 10.4, one level down.

**Proximity is reported alongside presence.** A point query alone renders SE1 as
having no surface water risk, when extents are mapped 100 m away and 32 sit
within 250 m. Each dataset is therefore checked at the point and, where absent,
counted within the precision-widened radius. The count is skipped where the
extent already covers the point, since presence is the stronger statement.

**The zone label carries its own caveat.** Findings are named
`Flood Zone 3 (undefended floodplain extent)` rather than `Flood Zone 3`, and the
summary appends the full defences caveat whenever a zone applies at or near the
site. SE1 is in Flood Zone 3 and sits behind the Thames Barrier, so a bare zone
name overstates real risk. Naming the zone for what it is means the qualification
survives being quoted out of context.

Real output for SE1:

> At this location: Flood Zone 3 (undefended floodplain extent); Flood Zone 2
> (undefended floodplain extent). Mapped within 260 m: Surface water flooding,
> high risk (3.3% annual chance); ... Flood Zones show the undefended floodplain
> [...] not an assessment of residual risk.

### 10.9 Terrain: **ImageServer**, reversing 10.2 on measurement

**The DEFRA WMS is rate-limited past usefulness.** A burst of twelve requests
returned one success and eleven `403`s. Fresh connections gave
`[200, 200, 403, 200, 403, ...]`, so it is throttling at roughly one request per
second, not rejecting the client. Crucially it answers with a bare **`403`, not a
`429`** -- and a throttled response misread as "no data" is exactly the
confidently-wrong failure this project exists to prevent.

**The ImageServer wins on every axis that matters here:**

| | DEFRA WMS | ImageServer |
|---|---|---|
| Burst of 8 | throttled | 8/8 clean |
| Ring of 8 points | 8 requests | **1** (`getSamples`) |
| No coverage | empty array, ambiguous | explicit `"NoData"` |
| Provenance | none | tile, resolution, return, composite year |
| URL | durable gov.uk | proxied `usrsvcs/<hash>` |

The stable-URL argument that won 10.2 was correct in principle and is simply
outweighed: an endpoint that cannot serve the calls we need is not a usable
endpoint. A WMS fallback was considered and rejected -- two response shapes and
two failure modes to test, for a fallback that would itself be throttled into
uselessness the moment it was needed. The WMS stays documented in 7.1.

**`getSamples` silently drops uncovered points** rather than returning `NoData`
for them: SE1 plus Edinburgh returns one sample, not two. Each sample carries its
`location`, so results are matched back by coordinate rather than by position.

**Elevation alone is thin**, so a ring of eight points is sampled. Its radius is
`max(50 m, location uncertainty)`, doing two jobs:

- **50 m ring** on a precisely located site: local slope, reported as a gradient.
- **Widened ring** on a coarse reference: how much ground level varies across the
  area the reference denotes.

**Which of the two applied is reported, and a widened ring refuses to state a
gradient at all.** This was a real bug caught by running it: at `TQ 32 80` the
ring spans 2.3 to 17.2 m AOD, but dividing 14.9 m by a 1414 m diameter produced
"Effectively level". The number was arithmetically right and the sentence
completely wrong. A widened ring now reports uncertainty and explicitly declines
to infer slope.

**The ring is a sample, not a survey**, and says so every time. Eight points can
miss a scarp between them, and the same range could be one steep face or gentle
undulation. That caveat is attached to every relief statement, including
"effectively level" ones, since that is where a reader is most likely to
over-trust it.

**Coverage is not simply "England".** The composite reaches into intertidal and
nearshore areas -- a point 2.5 km off the Isle of Wight returns -1.55 m, not
`NoData`. Genuine `NoData` needs the mid Irish Sea or deep Channel. So "no LIDAR"
means ground level is unknown, never that the ground is at zero.

### 10.10 The model advises; it never states a fact

Measured, not assumed. Given the findings plainly and asked to summarise,
qwen2.5:7b **relocated a finding** ("surface water mapped within 260 m" became
"high probability of surface water flooding" at the site) and **invented a
mechanism** ("London Clay ... could exacerbate flood impacts due to their
permeability characteristics", which is unsupported and backwards).

So every finding, figure, caveat and gap renders deterministically from the
Pydantic models. The model's only output is a "suggested next checks" list, where
a fabrication is a bad suggestion rather than a false fact. Two checks run before
its output is shown:

- **Grounding:** every number must appear in the findings it was given.
- **Absence denylist:** it may never originate "no risk", "not at risk" and
  similar. A phrase already in the findings is quotation and allowed, since
  dataset labels legitimately read "high risk (3.3% annual chance)".

Over 18 runs (`scripts/measure_advisor.py`): 13 passed, 5 withheld for an
ungrounded figure, and **0 absence claims**. The denylist caught nothing, because
confining the model to advice already removes the behaviour it guards against --
the absence claims came from the summarising prompt. Containment is what earns
its keep, at roughly one run in three.

An unreachable Ollama is a gap with a reason, not a silent omission: the same
three-state treatment applied to our own component.

### 10.11 Tool budget

The 4-5 tool limit maps onto **locate, geology, boreholes, flood, terrain** --
exactly 5. That leaves no room for the landslide, mine plans or AGS collections
found in passing (section 11). Adding any of them means dropping one of the five.

---

## 11. Found in passing, not investigated

The BGS OGC API carries collections that are squarely relevant to desk studies
but outside this session's scope: `landslideindex` (National Landslide Database),
`mine_plans`, `agsboreholeindex` (digital AGS geotechnical data), and
`bgsgeology625k*` as GeoJSON. The `GeoIndex_Onshore` ArcGIS folder additionally
exposes `hazards`, `radon`, `hydrogeology` and `buried_valleys`.

Recorded here so they are not rediscovered later. Adding any of them would
exceed the tool budget as it stands.

---

## 12. Reproducing these findings

```
python scripts/probe_sources.py
```

Stdlib only, no dependencies, no key, no configuration. Exits non-zero if any
source has moved or broken. Last run: 11/11 passed, 2026-08-18.
