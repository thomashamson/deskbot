"""Ground level and relief.

Network tests hit the EA LIDAR ImageServer. Elevations are from a published 2025
composite, so they are stable values rather than live readings.
"""

import pytest

from deskbot.locate import Country, InputKind, Location
from deskbot.results import Assessed, NotAssessed, NotAssessedReason
from deskbot.terrain import (
    RING_BASE_M,
    SAMPLING_CAVEAT,
    GroundLevel,
    Relief,
    terrain,
)


def _at(
    easting: int,
    northing: int,
    *,
    precision_m: float = 0.7071,
    country: Country = Country.ENGLAND,
) -> Location:
    return Location(
        easting=easting,
        northing=northing,
        precision_m=precision_m,
        precision_basis="test point",
        country=country,
        input_raw="test",
        input_kind=InputKind.GRID_REFERENCE,
        normalised_input="test",
    )


SE1 = _at(532785, 180244)
REDESDALE = _at(393851, 587167)
CHEDDAR = _at(346500, 154000)
EDINBURGH = _at(325200, 673900, country=Country.SCOTLAND)
# Deep English Channel: the composite reaches into intertidal areas, so a point
# merely offshore still returns a value. This one genuinely has no coverage.
NO_COVERAGE = _at(500000, 20000)
COARSE = _at(532500, 180500, precision_m=707.1)
SECTOR_MEAN = _at(532785, 180244, precision_m=5000.0)


def _relief(**kwargs) -> Relief:
    base = {
        "ring_radius_m": 50.0,
        "points_requested": 8,
        "points_sampled": 8,
        "min_m": 4.0,
        "max_m": 6.0,
        "max_gradient": 0.0,
        "widened_for_uncertainty": False,
    }
    return Relief(**(base | kwargs))


class TestGates:
    def test_outside_england_is_a_gap(self):
        report = terrain(EDINBURGH)
        assert isinstance(report.ground_level, NotAssessed)
        assert report.ground_level.reason is NotAssessedReason.OUTSIDE_COVERAGE
        assert "Scotland" in report.ground_level.detail

    def test_gap_carries_no_findings(self):
        assert not hasattr(terrain(EDINBURGH).ground_level, "findings")

    def test_unusable_precision_refuses(self):
        report = terrain(SECTOR_MEAN)
        assert isinstance(report.ground_level, NotAssessed)
        assert report.ground_level.reason is NotAssessedReason.INSUFFICIENT_LOCATION_PRECISION


class TestSamplingCaveat:
    """The ring samples around the site; it does not survey it."""

    def test_caveat_is_always_present_on_relief(self):
        assert SAMPLING_CAVEAT in _relief().describe()

    def test_caveat_is_present_even_when_level(self):
        # 'Effectively level' is exactly where a reader might over-trust it.
        described = _relief(min_m=5.0, max_m=5.05, max_gradient=0.0005).describe()
        assert "Effectively level" in described
        assert SAMPLING_CAVEAT in described

    def test_caveat_names_the_scarp_failure_mode(self):
        assert "steep face between samples" in SAMPLING_CAVEAT
        assert "not a survey" in SAMPLING_CAVEAT


class TestReliefWording:
    def test_local_slope_and_uncertainty_read_differently(self):
        # Same numbers, different meaning. Conflating them would report a
        # location-confidence range as if it described the site's slope.
        local = _relief(ring_radius_m=50, widened_for_uncertainty=False).describe()
        widened = _relief(ring_radius_m=757, widened_for_uncertainty=True).describe()
        assert "within 50 m" in local
        assert "the location could lie within" in widened
        assert "could lie within" not in local

    def test_gradient_is_reported_as_a_ratio(self):
        described = _relief(min_m=0.0, max_m=10.0, max_gradient=0.2).describe()
        assert "20%" in described
        assert "1 in 5" in described

    def test_missing_coverage_is_declared(self):
        described = _relief(points_sampled=5).describe()
        assert "3 of 8 sample points have no LIDAR coverage" in described

    def test_widened_ring_refuses_to_claim_a_gradient(self):
        # A 15 m spread over a 707 m ring is not "effectively level": the ring
        # covers where the site might be, not the site.
        described = _relief(
            ring_radius_m=707.1,
            min_m=2.3,
            max_m=17.2,
            max_gradient=0.005,
            widened_for_uncertainty=True,
        ).describe()
        assert "Effectively level" not in described
        assert "gradient about" not in described
        assert "uncertain by up to 14.9 m" in described
        assert "No site gradient can be inferred" in described

    def test_narrow_ring_still_reports_level_ground_as_level(self):
        described = _relief(min_m=5.0, max_m=5.05, max_gradient=0.0005).describe()
        assert "Effectively level" in described

    def test_range_is_derived(self):
        assert _relief(min_m=4.0, max_m=16.5).range_m == pytest.approx(12.5)


class TestGroundLevelWording:
    def test_describe_states_bare_earth(self):
        level = GroundLevel(elevation_m=4.77, easting=1, northing=2)
        assert "4.77 m AOD" in level.describe()
        assert "bare earth" in level.describe()


@pytest.mark.network
class TestRealLookups:
    def test_southwark_ground_level(self):
        report = terrain(SE1)
        assert isinstance(report.ground_level, Assessed)
        level = report.ground_level.findings[0]
        assert level.elevation_m == pytest.approx(4.77, abs=0.1)

    def test_provenance_is_recorded(self):
        # The reason for preferring the ImageServer over the WMS.
        level = terrain(SE1).ground_level.findings[0]
        assert level.tile == "TQ38sw_DTM_1m"
        assert level.resolution == "1m"
        assert level.model_type is not None and "Terrain" in level.model_type
        assert level.composite_year is not None and level.composite_year >= 2020

    def test_upland_point_is_much_higher(self):
        level = terrain(REDESDALE).ground_level.findings[0]
        assert level.elevation_m == pytest.approx(263.7, abs=1.0)

    def test_relief_is_sampled_in_one_request(self):
        report = terrain(SE1)
        assert report.relief is not None
        assert report.relief.points_requested == 8
        assert report.relief.ring_radius_m == RING_BASE_M

    def test_flat_urban_site_reads_as_shallow(self):
        report = terrain(SE1)
        assert report.relief.max_gradient < 0.05

    def test_steep_site_reads_as_steep(self):
        # Cheddar Gorge: if the ring cannot tell this from Southwark, it is
        # measuring nothing.
        gorge = terrain(CHEDDAR)
        flat = terrain(SE1)
        assert gorge.relief.max_gradient > flat.relief.max_gradient
        assert gorge.relief.range_m > 10

    def test_coarse_location_widens_the_ring_and_says_so(self):
        report = terrain(COARSE)
        assert report.relief is not None
        assert report.relief.widened_for_uncertainty
        assert report.relief.ring_radius_m == pytest.approx(707.1, abs=0.1)
        assert "the location could lie within" in report.relief.describe()

    def test_precise_location_reports_local_slope_not_uncertainty(self):
        report = terrain(SE1)
        assert not report.relief.widened_for_uncertainty
        assert "within 50 m" in report.relief.describe()

    def test_uncovered_point_is_a_gap_not_zero_elevation(self):
        report = terrain(NO_COVERAGE)
        assert isinstance(report.ground_level, NotAssessed)
        assert report.ground_level.reason is NotAssessedReason.OUTSIDE_COVERAGE
        assert "rather than that the ground is at zero" in report.ground_level.detail

    def test_summary_combines_level_relief_and_caveat(self):
        described = terrain(SE1).describe()
        assert "m AOD" in described
        assert SAMPLING_CAVEAT in described

    def test_attribution_is_present(self):
        report = terrain(SE1)
        assert "Environment Agency" in report.ground_level.source.attribution
