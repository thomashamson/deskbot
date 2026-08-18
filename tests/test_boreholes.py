"""Borehole index search.

Network tests hit the real BGS GeoIndex. Counts are archival records rather than
live readings, so they are stable, but assertions on totals use bounds rather
than exact equality where the index could grow.
"""

import pytest

from deskbot.boreholes import (
    DEFAULT_RADIUS_M,
    BoreholeRecord,
    BoreholeReport,
    ScanAvailability,
    _depth,
    _record,
    _scan_availability,
    _url_or_none,
    _year,
    boreholes,
    sobi_source,
)
from deskbot.locate import Country, InputKind, Location
from deskbot.results import Assessed, NotAssessed, NotAssessedReason


def _at(easting: int, northing: int, precision_m: float = 0.7071) -> Location:
    """Default precision is that of a 1 m grid reference: the half-diagonal of a
    1 m square, not 1 m."""
    return Location(
        easting=easting,
        northing=northing,
        precision_m=precision_m,
        precision_basis="test point",
        country=Country.ENGLAND,
        input_raw="test",
        input_kind=InputKind.GRID_REFERENCE,
        normalised_input="test",
    )


SE1 = _at(532785, 180244)
REDESDALE = _at(393851, 587167)
EDINBURGH = _at(325200, 673900)
SECTOR_MEAN = _at(532785, 180244, precision_m=5000.0)


class TestSentinelValues:
    """The index spells 'unknown' several different ways."""

    @pytest.mark.parametrize("raw", [-1, -1.0, 0, None, "", "rubbish"])
    def test_non_positive_and_unparseable_depths_are_unknown(self, raw):
        assert _depth(raw) is None

    def test_real_depths_survive(self):
        assert _depth(35.0) == 35.0
        assert _depth(1.3) == 1.3

    @pytest.mark.parametrize("raw", [None, "", "Not Available", "-"])
    def test_missing_years_are_none(self, raw):
        assert _year(raw) is None

    def test_real_year_parses(self):
        assert _year("1859") == 1859

    def test_not_available_is_a_string_not_a_url(self):
        # SCAN_URL carries this literal for a sixth of records. Treated as a URL
        # it would render as a broken link.
        assert _url_or_none("Not Available") is None
        assert _url_or_none(None) is None
        assert _url_or_none("https://api.bgs.ac.uk/sobi-scans/v1/x") is not None


class TestScanAvailability:
    def test_bgs_api_scan_is_free(self):
        url = "https://api.bgs.ac.uk/sobi-scans/v1/borehole/scans/items/13601099"
        assert _scan_availability(url) is ScanAvailability.FREE_ONLINE

    def test_shop_link_is_a_purchase_not_a_free_record(self):
        assert _scan_availability("http://shop.bgs.ac.uk/GeoRecords") is ScanAvailability.PURCHASE

    def test_no_url_means_no_scan(self):
        assert _scan_availability(None) is ScanAvailability.NONE


class TestRecordParsing:
    def test_distance_is_measured_from_the_query_point(self):
        record = _record(
            {"REFERENCE": "TQ38SW1", "EASTING": 532885, "NORTHING": 180244, "LENGTH": 10.0},
            532785,
            180244,
        )
        assert record is not None
        assert record.distance_m == pytest.approx(100.0)

    def test_record_without_a_reference_is_dropped(self):
        assert _record({"EASTING": 1, "NORTHING": 2}, 0, 0) is None

    def test_unknown_depth_renders_as_unknown_not_minus_one(self):
        record = _record({"REFERENCE": "X", "EASTING": 0, "NORTHING": 0, "LENGTH": -1.0}, 0, 0)
        assert record is not None
        assert record.depth_m is None
        assert "depth unknown" in record.describe()
        assert "-1" not in record.describe()

    def test_undated_record_says_so(self):
        record = _record({"REFERENCE": "X", "EASTING": 0, "NORTHING": 0}, 0, 0)
        assert record is not None
        assert "undated" in record.describe()


def _report(total: int, listed: int, first_m: float, last_m: float) -> BoreholeReport:
    from deskbot.precision import resolve_search_radius

    radius = resolve_search_radius(250, 1.0, basis="test point")
    findings = [
        BoreholeRecord(
            reference=f"R{i}",
            easting=0,
            northing=0,
            distance_m=first_m + (last_m - first_m) * i / max(listed - 1, 1),
        )
        for i in range(listed)
    ]
    return BoreholeReport(
        records=Assessed[BoreholeRecord](
            findings=findings, source=sobi_source(), query="within 250 m"
        ),
        search=radius,
        total_within_radius=total,
    )


class TestSummarySentence:
    """A count alone cannot say whether the site is data-rich."""

    def test_nothing_found_is_stated_plainly(self):
        report = _report(total=0, listed=0, first_m=0, last_m=0)
        assert report.describe() == "No borehole records within 250 m."

    def test_complete_listing_reports_the_nearest(self):
        report = _report(total=3, listed=3, first_m=12, last_m=80)
        described = report.describe()
        assert "3 borehole records within 250 m" in described
        assert "nearest at 12 m" in described
        assert "closest" not in described  # nothing was withheld

    def test_truncated_listing_reports_nearest_and_reach(self):
        report = _report(total=1109, listed=20, first_m=12, last_m=84)
        described = report.describe()
        assert "1,109 borehole records" in described
        assert "nearest at 12 m" in described
        assert "closest 20 are listed, out to 84 m" in described

    def test_sample_flag_tracks_the_totals(self):
        assert _report(1109, 20, 12, 84).listing_is_sample
        assert not _report(3, 3, 12, 80).listing_is_sample

    def test_nearest_and_reach_are_none_when_empty(self):
        report = _report(total=0, listed=0, first_m=0, last_m=0)
        assert report.nearest_m is None
        assert report.listed_to_m is None


class TestPrecisionGate:
    def test_unusable_precision_refuses_before_any_query(self):
        # No network call should be needed: the gate runs first.
        report = boreholes(SECTOR_MEAN)
        assert isinstance(report.records, NotAssessed)
        assert report.records.reason is NotAssessedReason.INSUFFICIENT_LOCATION_PRECISION
        assert report.search is None
        assert report.describe() is None

    def test_refusal_carries_no_findings(self):
        assert not hasattr(boreholes(SECTOR_MEAN).records, "findings")


@pytest.mark.network
class TestRealSearches:
    def test_southwark_is_data_rich(self):
        report = boreholes(SE1)
        assert isinstance(report.records, Assessed)
        assert report.total_within_radius is not None
        assert report.total_within_radius >= 80
        assert report.nearest_m is not None and report.nearest_m < 50

    def test_listing_is_capped_but_total_is_exact(self):
        report = boreholes(SE1, limit=5)
        assert len(report.records.findings) == 5
        assert report.total_within_radius >= 80
        assert report.listing_is_sample

    def test_records_are_sorted_nearest_first(self):
        report = boreholes(SE1)
        distances = [r.distance_m for r in report.records.findings]
        assert distances == sorted(distances)

    def test_summary_names_the_nearest_and_the_reach(self):
        report = boreholes(SE1, limit=20)
        described = report.describe()
        assert "nearest at" in described
        assert "out to" in described

    def test_rural_point_is_assessed_with_nothing_found(self):
        # Redesdale genuinely has no records within 250 m. This is a finding,
        # not a gap: the index was searched.
        report = boreholes(REDESDALE)
        assert isinstance(report.records, Assessed)
        assert report.total_within_radius == 0
        assert report.records.is_empty
        assert "No borehole records" in report.describe()

    def test_scotland_is_covered_because_bgs_is_gb_wide(self):
        # Unlike the Environment Agency sources, there is no country gate here.
        report = boreholes(EDINBURGH)
        assert isinstance(report.records, Assessed)
        assert report.total_within_radius > 0

    def test_default_radius_is_reported_honestly(self):
        report = boreholes(SE1)
        assert report.search is not None
        assert report.search.requested_m == DEFAULT_RADIUS_M
        assert not report.search.widened  # a 1 m precision point barely moves it

    def test_coarse_location_widens_and_says_so(self):
        coarse = _at(532500, 180500, precision_m=707.1)
        report = boreholes(coarse)
        assert isinstance(report.records, Assessed)
        assert report.search.effective_m == pytest.approx(957.1, abs=0.1)
        assert "widened" in report.records.query

    def test_free_scans_and_purchase_links_are_distinguished(self):
        report = boreholes(SE1, limit=100)
        kinds = {r.scan for r in report.records.findings}
        assert ScanAvailability.FREE_ONLINE in kinds
        for record in report.records.findings:
            if record.scan is ScanAvailability.NONE:
                assert record.scan_url is None

    def test_attribution_is_present(self):
        report = boreholes(SE1)
        assert "British Geological Survey" in report.records.source.attribution
