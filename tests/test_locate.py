"""Resolving input to a point, and the England-only gate.

Network tests are marked and hit the real public endpoints. Run without them
using ``-m "not network"``.
"""

import pytest

from deskbot.locate import (
    Country,
    InputKind,
    Location,
    OutsideUnitedKingdomError,
    UnknownPostcodeError,
    UnparseableLocationError,
    locate,
)
from deskbot.results import NotAssessed, NotAssessedReason, SourceRef

EA_SOURCE = SourceRef(
    name="EA Flood Map for Planning",
    url="https://services1.arcgis.com/example",
    licence="OGL v3",
    attribution="© Environment Agency copyright and/or database right 2026",
)


def _location(country: Country) -> Location:
    return Location(
        easting=532785,
        northing=180244,
        precision_m=10.0,
        precision_basis="postcode centroid, OS positional quality 1",
        country=country,
        input_raw="SE1 9GF",
        input_kind=InputKind.POSTCODE,
        normalised_input="SE1 9GF",
    )


class TestEnglandOnlyGate:
    """EA sources answer out-of-area queries with count:0, so the gate must run
    before they are consulted, not after."""

    def test_england_produces_no_gap(self):
        assert _location(Country.ENGLAND).england_only_gap(EA_SOURCE, "Flood zones") is None

    @pytest.mark.parametrize("country", [Country.SCOTLAND, Country.WALES, Country.NORTHERN_IRELAND])
    def test_other_countries_produce_a_gap(self, country):
        gap = _location(country).england_only_gap(EA_SOURCE, "Flood zones")
        assert isinstance(gap, NotAssessed)
        assert gap.reason is NotAssessedReason.OUTSIDE_COVERAGE

    def test_gap_names_the_country_and_the_dataset(self):
        gap = _location(Country.SCOTLAND).england_only_gap(EA_SOURCE, "Flood zones")
        assert gap is not None
        assert "Scotland" in gap.detail
        assert "Flood zones" in gap.detail
        assert "not been assessed" in gap.detail

    def test_gap_points_at_the_devolved_alternative(self):
        # Names the authority without asserting what it publishes: this helper
        # gates terrain as well as flood.
        scotland = _location(Country.SCOTLAND).england_only_gap(EA_SOURCE, "F").detail
        assert "SEPA" in scotland
        assert "flood maps" not in scotland
        assert "Natural Resources Wales" in (
            _location(Country.WALES).england_only_gap(EA_SOURCE, "F").detail
        )

    def test_gap_is_not_an_empty_result(self):
        gap = _location(Country.SCOTLAND).england_only_gap(EA_SOURCE, "Flood zones")
        assert not hasattr(gap, "findings")


class TestInputRouting:
    def test_empty_input_rejected(self):
        with pytest.raises(UnparseableLocationError):
            locate("   ")


@pytest.mark.network
class TestPostcodes:
    def test_southwark(self):
        loc = locate("SE1 9GF")
        assert (loc.easting, loc.northing) == (532785, 180244)
        assert loc.country is Country.ENGLAND
        assert loc.input_kind is InputKind.POSTCODE

    def test_postcode_supplies_latitude_and_longitude_for_free(self):
        # No transform is performed; these come from the ONS directory.
        loc = locate("SE1 9GF")
        assert loc.latitude == pytest.approx(51.5055, abs=0.001)
        assert loc.longitude == pytest.approx(-0.0881, abs=0.001)

    def test_positional_quality_drives_precision(self):
        loc = locate("SE1 9GF")
        assert loc.postcode_quality == 1
        assert loc.precision_m == 10.0

    def test_lowercase_and_unspaced_postcodes_work(self):
        assert locate("se19gf").easting == 532785

    def test_scottish_postcode_is_identified_as_scotland(self):
        assert locate("EH1 1RE").country is Country.SCOTLAND

    def test_unknown_postcode_is_reported_clearly(self):
        with pytest.raises(UnknownPostcodeError):
            locate("ZZ1 1ZZ")

    def test_attribution_is_recorded(self):
        loc = locate("SE1 9GF")
        assert len(loc.sources) == 2
        assert any("Ordnance" in s.attribution or "OS data" in s.attribution for s in loc.sources)


@pytest.mark.network
class TestGridReferences:
    def test_southwark_grid_reference_matches_its_postcode(self):
        loc = locate("TQ 32785 80244")
        assert (loc.easting, loc.northing) == (532785, 180244)
        assert loc.input_kind is InputKind.GRID_REFERENCE
        assert loc.country is Country.ENGLAND

    def test_grid_reference_has_no_latitude(self):
        # Nothing derives WGS84 here; every source queries in EPSG:27700.
        assert locate("TQ 32785 80244").latitude is None

    def test_redesdale_is_england_despite_being_near_the_border(self):
        # ~15 km from Scotland. A nearest-postcode lookup could get this wrong,
        # which is why the country comes from an ONS point-in-polygon.
        assert locate("NY 93851 87167").country is Country.ENGLAND

    def test_carter_bar_is_scotland(self):
        assert locate("NT 37297 04810").country is Country.SCOTLAND

    def test_coarse_reference_carries_its_precision(self):
        loc = locate("TQ 32 80")
        assert loc.precision_m == pytest.approx(707.1, abs=0.1)
        assert "1 km grid square" in loc.precision_basis

    def test_point_outside_the_uk_is_refused(self):
        # North of Shetland, in open sea.
        with pytest.raises(OutsideUnitedKingdomError):
            locate("HP 00000 99999")
