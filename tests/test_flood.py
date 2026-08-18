"""Flood risk lookup.

The England gate is the most important thing tested here: both Environment
Agency services answer an out-of-area query with zero results, so a missing gate
turns a Scottish site into "no flood risk".
"""

import pytest

from deskbot.flood import (
    DEFENCES_CAVEAT,
    FloodDataset,
    FloodPresence,
    FloodReport,
    flood,
    flood_map_source,
    surface_water_source,
)
from deskbot.locate import Country, InputKind, Location
from deskbot.results import Assessed, NotAssessed, NotAssessedReason


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
EDINBURGH = _at(325200, 673900, country=Country.SCOTLAND)
CARDIFF = _at(318000, 176000, country=Country.WALES)
SECTOR_MEAN = _at(532785, 180244, precision_m=5000.0)


def _presence(**kwargs) -> FloodPresence:
    base = {
        "dataset": FloodDataset.FLOOD_ZONE_3,
        "label": "Flood Zone 3 (undefended floodplain extent)",
        "definition": "test",
        "at_point": False,
    }
    return FloodPresence(**(base | kwargs))


class TestEnglandGate:
    """Zero results outside England must never read as zero risk."""

    @pytest.mark.parametrize("location", [EDINBURGH, CARDIFF])
    def test_outside_england_is_a_gap_not_a_clean_result(self, location):
        report = flood(location)
        assert isinstance(report.planning, NotAssessed)
        assert isinstance(report.surface_water, NotAssessed)
        assert report.planning.reason is NotAssessedReason.OUTSIDE_COVERAGE

    def test_gap_names_the_country_and_the_alternative(self):
        report = flood(EDINBURGH)
        assert "Scotland" in report.planning.detail
        assert "SEPA" in report.planning.detail

    def test_gap_carries_no_findings(self):
        report = flood(EDINBURGH)
        assert not hasattr(report.planning, "findings")
        assert not hasattr(report.surface_water, "findings")

    def test_summary_states_the_gap_rather_than_absence_of_risk(self):
        described = flood(EDINBURGH).describe()
        assert "not been assessed" in described
        assert "no flood" not in described.lower()

    def test_gate_runs_before_any_request(self):
        # No client is supplied and no network is reachable in this assertion:
        # a gap must be produced from the country alone.
        report = flood(EDINBURGH)
        assert report.search is None


class TestPrecisionGate:
    def test_unusable_precision_refuses(self):
        report = flood(SECTOR_MEAN)
        assert isinstance(report.planning, NotAssessed)
        assert report.planning.reason is NotAssessedReason.INSUFFICIENT_LOCATION_PRECISION


class TestPresenceWording:
    def test_present_at_the_point(self):
        presence = _presence(at_point=True, source_types=("Tidal Models",))
        assert "present at this location" in presence.describe()
        assert "Tidal Models" in presence.describe()

    def test_nearby_but_not_here_is_distinct_from_absent(self):
        nearby = _presence(at_point=False, within_radius=32)
        absent = _presence(at_point=False, within_radius=0)
        assert nearby.nearby
        assert not absent.nearby
        assert "not at this location, but mapped within" in nearby.describe()
        assert "not mapped at or near" in absent.describe()

    def test_within_radius_is_not_counted_when_present_at_the_point(self):
        presence = _presence(at_point=True, within_radius=None)
        assert not presence.nearby


class TestDefencesCaveat:
    """A zone must not be reportable without its qualification."""

    def test_label_names_what_the_zone_actually_is(self):
        from deskbot.flood import _LAYERS

        assert "undefended" in _LAYERS[FloodDataset.FLOOD_ZONE_3].label
        assert "undefended" in _LAYERS[FloodDataset.FLOOD_ZONE_2].label

    def test_summary_carries_the_caveat_when_a_zone_is_found(self):
        report = FloodReport(
            planning=Assessed[FloodPresence](
                findings=[_presence(at_point=True, ignores_defences=True)],
                source=flood_map_source(),
            ),
            surface_water=Assessed[FloodPresence](findings=[], source=surface_water_source()),
        )
        assert DEFENCES_CAVEAT in report.describe()

    def test_caveat_is_absent_when_no_zone_applies(self):
        report = FloodReport(
            planning=Assessed[FloodPresence](
                findings=[_presence(at_point=False, within_radius=0)],
                source=flood_map_source(),
            ),
            surface_water=Assessed[FloodPresence](findings=[], source=surface_water_source()),
        )
        assert DEFENCES_CAVEAT not in report.describe()

    def test_caveat_applies_to_a_nearby_zone_too(self):
        report = FloodReport(
            planning=Assessed[FloodPresence](
                findings=[_presence(at_point=False, within_radius=4, ignores_defences=True)],
                source=flood_map_source(),
            ),
            surface_water=Assessed[FloodPresence](findings=[], source=surface_water_source()),
        )
        assert DEFENCES_CAVEAT in report.describe()


@pytest.mark.network
class TestRealLookups:
    def test_southwark_is_in_flood_zones_2_and_3(self):
        report = flood(SE1)
        assert isinstance(report.planning, Assessed)
        found = {p.dataset for p in report.at_point}
        assert FloodDataset.FLOOD_ZONE_3 in found
        assert FloodDataset.FLOOD_ZONE_2 in found

    def test_southwark_zone_is_tidal(self):
        report = flood(SE1)
        zone3 = next(p for p in report.at_point if p.dataset is FloodDataset.FLOOD_ZONE_3)
        assert "Tidal" in " ".join(zone3.source_types)

    def test_southwark_summary_includes_the_defences_caveat(self):
        # SE1 is Flood Zone 3 but sits behind the Thames Barrier.
        assert DEFENCES_CAVEAT in flood(SE1).describe()

    def test_southwark_surface_water_is_nearby_but_not_here(self):
        # Nothing at the point, but extents are mapped within 250 m. Reporting
        # only the point would render this site as simply not at risk.
        report = flood(SE1)
        surface = {p.dataset for p in report.nearby_only}
        assert surface & set(
            {
                FloodDataset.SURFACE_WATER_HIGH,
                FloodDataset.SURFACE_WATER_MEDIUM,
                FloodDataset.SURFACE_WATER_LOW,
            }
        )

    def test_every_dataset_stays_visible_even_when_clear(self):
        # A dataset that found nothing must still appear, or 'checked and clear'
        # becomes indistinguishable from 'never checked'.
        report = flood(SE1)
        reported = {p.dataset for p in report.planning.findings} | {
            p.dataset for p in report.surface_water.findings
        }
        assert reported == set(FloodDataset)

    def test_rural_point_is_assessed_and_clear(self):
        report = flood(REDESDALE)
        assert isinstance(report.planning, Assessed)
        assert report.at_point == ()
        assert "No mapped flood extent covers this location" in report.describe()

    def test_flood_storage_area_is_checked_and_absent(self):
        report = flood(SE1)
        storage = next(
            p for p in report.planning.findings if p.dataset is FloodDataset.FLOOD_STORAGE_AREA
        )
        assert not storage.at_point

    def test_attribution_is_present_for_both_sources(self):
        report = flood(SE1)
        assert "Environment Agency" in report.planning.source.attribution
        assert "Environment Agency" in report.surface_water.source.attribution

    def test_surface_water_records_its_publication_date(self):
        # Vintage matters: this is a 2022 dataset, not a live reading.
        report = flood(_at(532600, 180100))
        dated = [p for p in report.surface_water.findings if p.published is not None]
        for presence in dated:
            assert presence.published.year >= 2018
