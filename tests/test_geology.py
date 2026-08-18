"""Geology at a point, per layer.

Network tests hit the real BGS WMS. Values are the ones verified during
reconnaissance and are stable published map data, not live readings.
"""

import pytest

from deskbot.geology import (
    GeologyLayer,
    GeologyUnit,
    LayerVariation,
    LocationVariation,
    faults_gap,
    geology,
)
from deskbot.locate import Country, InputKind, Location
from deskbot.results import Assessed, NotAssessed, NotAssessedReason


def _at(easting: int, northing: int, precision_m: float = 1.0) -> Location:
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
CANARY_WHARF = _at(537500, 180400)
BLACK_VEN = _at(335500, 93000)
OFFSHORE = _at(456000, 77000)
# TQ 32 80: a 1 km square whose corners straddle two superficial units.
COARSE_SE1 = _at(532500, 180500, precision_m=707.1)


class TestFaultsAreAlwaysAGap:
    def test_faults_gap_is_not_assessed(self):
        gap = faults_gap()
        assert isinstance(gap, NotAssessed)
        assert gap.reason is NotAssessedReason.NOT_QUERYABLE_AT_A_POINT

    def test_gap_explains_the_limitation_is_ours(self):
        detail = faults_gap().detail
        assert "not been assessed" in detail
        assert "may cross the site" in detail

    def test_gap_carries_no_findings(self):
        assert not hasattr(faults_gap(), "findings")


class TestBlankNormalisation:
    """BGS returns a single space for some fields rather than omitting them."""

    def test_blank_and_placeholder_fields_become_none(self):
        from deskbot.geology import _unit_from_feature

        unit = _unit_from_feature(
            {
                "layerName": "BGS.50k.Bedrock",
                "properties": {
                    "LEX_D": "London Clay Formation",
                    "BROAD_D": " ",
                    "RANK": "Not Applicable",
                    "GP_EQ_D": "No Parent",
                    "MAX_EPOCH": "Not Defined",
                    "RCS_D": "Clay, silt and sand",
                },
            }
        )
        assert unit is not None
        assert unit.broad_lithology is None
        assert unit.rank is None
        assert unit.group is None
        assert unit.max_epoch is None
        assert unit.lithology == "Clay, silt and sand"

    def test_feature_without_a_unit_name_is_dropped(self):
        from deskbot.geology import _unit_from_feature

        assert _unit_from_feature({"layerName": "BGS.50k.Bedrock", "properties": {}}) is None


class TestVariationReporting:
    """Divergence must name the units, not just report that they differ."""

    def test_describe_names_two_units(self):
        variation = LayerVariation(
            layer=GeologyLayer.SUPERFICIAL,
            units=("Alluvium", "Langley Silt Member"),
        )
        assert variation.describe() == (
            "superficial deposits spans Alluvium and Langley Silt Member"
        )

    def test_describe_names_three_units(self):
        variation = LayerVariation(layer=GeologyLayer.BEDROCK, units=("A", "B", "C"))
        assert variation.describe() == "bedrock spans A, B and C"

    def test_unsampled_says_nothing(self):
        assert LocationVariation(points_sampled=1).describe() is None

    def test_sampled_and_consistent_is_distinct_from_unsampled(self):
        checked = LocationVariation(points_sampled=5, offset_m=500)
        assert checked.checked
        assert not checked.varies
        assert "consistent" in checked.describe()

    def test_sampled_and_varying_names_the_units(self):
        variation = LocationVariation(
            points_sampled=5,
            offset_m=500,
            layers=(
                LayerVariation(
                    layer=GeologyLayer.SUPERFICIAL,
                    units=("Alluvium", "Langley Silt Member"),
                ),
            ),
        )
        described = variation.describe()
        assert "Alluvium" in described
        assert "Langley Silt Member" in described
        assert "may sit on any of these" in described


class TestUnitDescription:
    def test_describe_includes_lithology(self):
        unit = GeologyUnit(
            layer=GeologyLayer.BEDROCK,
            name="London Clay Formation",
            lithology="Clay, silt and sand",
        )
        assert unit.describe() == "London Clay Formation (Clay, silt and sand)"

    def test_describe_without_lithology_is_just_the_name(self):
        unit = GeologyUnit(layer=GeologyLayer.BEDROCK, name="London Clay Formation")
        assert unit.describe() == "London Clay Formation"


@pytest.mark.network
class TestRealLookups:
    def test_southwark_bedrock_and_superficial(self):
        report = geology(SE1)
        assert isinstance(report.bedrock, Assessed)
        assert report.bedrock.findings[0].name == "London Clay Formation"
        assert report.superficial.findings[0].name == "Kempton Park Gravel Member"

    def test_layers_present_but_empty_are_assessed_not_gaps(self):
        # Southwark has no artificial ground or landslides mapped. That is a
        # real finding: the layers were queried and there is nothing there.
        report = geology(SE1)
        assert isinstance(report.artificial_ground, Assessed)
        assert report.artificial_ground.is_empty
        assert isinstance(report.mass_movement, Assessed)
        assert report.mass_movement.is_empty

    def test_bedrock_unit_carries_attribution_and_provenance(self):
        report = geology(SE1)
        unit = report.bedrock.findings[0]
        assert unit.lexicon_url is not None and "lexicon" in unit.lexicon_url
        assert unit.map_sheet == "ew256_North_London"
        assert "British Geological Survey" in report.bedrock.source.attribution

    def test_artificial_ground_is_found_where_it_exists(self):
        # The infilled West India Docks.
        report = geology(CANARY_WHARF)
        assert isinstance(report.artificial_ground, Assessed)
        assert report.artificial_ground.findings[0].name == "Infilled Ground"

    def test_mass_movement_is_found_where_it_exists(self):
        # Black Ven, Charmouth: an active coastal landslide complex.
        report = geology(BLACK_VEN)
        assert isinstance(report.mass_movement, Assessed)
        assert report.mass_movement.findings[0].name == "Landslide deposits"

    def test_faults_remain_a_gap_even_on_a_successful_lookup(self):
        report = geology(SE1)
        assert isinstance(report.faults, NotAssessed)

    def test_offshore_point_is_a_coverage_gap_not_an_empty_result(self):
        # No bedrock means off the onshore map. Reporting 'no geology found'
        # here would read as a finding.
        report = geology(OFFSHORE)
        assert isinstance(report.bedrock, NotAssessed)
        assert report.bedrock.reason is NotAssessedReason.OUTSIDE_COVERAGE
        assert isinstance(report.superficial, NotAssessed)

    def test_precise_location_is_not_corner_sampled(self):
        report = geology(SE1)
        assert report.variation.points_sampled == 1
        assert not report.variation.checked
        assert report.variation.describe() is None

    def test_coarse_location_is_sampled_and_divergence_is_named(self):
        # TQ 32 80 spans Alluvium and Langley Silt Member.
        report = geology(COARSE_SE1)
        assert report.variation.checked
        assert report.variation.points_sampled == 5
        assert report.variation.offset_m == pytest.approx(500, abs=1)
        assert report.variation.varies

        superficial = [v for v in report.variation.layers if v.layer is GeologyLayer.SUPERFICIAL]
        assert superficial, "expected the superficial layer to vary"
        assert set(superficial[0].units) == {"Alluvium", "Langley Silt Member"}

    def test_bedrock_does_not_vary_across_that_same_square(self):
        # London Clay underlies all five sampled points, so it must not be
        # reported as varying just because a neighbouring layer does.
        report = geology(COARSE_SE1)
        varying = {v.layer for v in report.variation.layers}
        assert GeologyLayer.BEDROCK not in varying

    def test_query_string_records_the_sampling_actually_done(self):
        report = geology(COARSE_SE1)
        assert "corner samples" in report.bedrock.query
