"""Reconciling search radius against location uncertainty.

The failure this prevents: searching 250 m around the centre of a 1 km grid
square, then reporting the hits as though they sit near the site.
"""

import pytest

from deskbot import gridref
from deskbot.precision import DEFAULT_CEILING_M, SearchRadius, resolve_search_radius
from deskbot.results import NotAssessed, NotAssessedReason, SourceRef

SOURCE = SourceRef(
    name="BGS SOBI",
    url="https://map.bgs.ac.uk/example",
    licence="OGL v3",
    attribution="Contains British Geological Survey materials © UKRI 2026",
)


class TestPreciseLocationsPassThrough:
    def test_one_metre_grid_reference_barely_widens(self):
        ref = gridref.parse("TQ 32785 80244")
        radius = resolve_search_radius(250, ref.precision_m, basis="grid square centre")
        assert isinstance(radius, SearchRadius)
        assert radius.effective_m == pytest.approx(250.7, abs=0.1)
        assert not radius.widened

    def test_unwidened_claim_is_the_plain_one(self):
        radius = resolve_search_radius(250, 0.0, basis="grid square centre")
        assert isinstance(radius, SearchRadius)
        assert radius.claim("grid square centre") == "within 250 m"


class TestUncertainLocationsWiden:
    def test_hundred_metre_reference_widens_the_search(self):
        ref = gridref.parse("TQ 327 802")
        radius = resolve_search_radius(250, ref.precision_m, basis="100 m grid square centre")
        assert isinstance(radius, SearchRadius)
        assert radius.effective_m == pytest.approx(320.7, abs=0.1)
        assert radius.widened

    def test_kilometre_reference_widens_rather_than_misleads(self):
        # 250 m around a 1 km square's centre covers about a fifth of the square
        # the user actually meant. Widening to 957 m guarantees nothing within
        # 250 m of the true site is missed.
        ref = gridref.parse("TQ 32 80")
        radius = resolve_search_radius(250, ref.precision_m, basis="1 km grid square centre")
        assert isinstance(radius, SearchRadius)
        assert radius.effective_m == pytest.approx(957.1, abs=0.1)
        assert radius.widened

    def test_widened_claim_restates_what_was_actually_searched(self):
        ref = gridref.parse("TQ 32 80")
        radius = resolve_search_radius(250, ref.precision_m, basis="1 km grid square centre")
        assert isinstance(radius, SearchRadius)
        claim = radius.claim("1 km grid square centre")
        # It must not still say 'within 250 m of the site'.
        assert "957 m" in claim
        assert "1 km grid square centre" in claim
        assert "widened" in claim


class TestUnusableLocationsAreRefused:
    def test_postcode_sector_mean_refuses_rather_than_answering(self):
        # OS positional quality 60: the coordinate is a postcode SECTOR mean,
        # kilometres across. No radius makes that a site-specific answer.
        result = resolve_search_radius(
            250, 5000.0, basis="postcode centroid, OS positional quality 6", source=SOURCE
        )
        assert isinstance(result, NotAssessed)
        assert result.reason is NotAssessedReason.INSUFFICIENT_LOCATION_PRECISION

    def test_refusal_explains_itself_and_names_the_source(self):
        result = resolve_search_radius(250, 5000.0, basis="postcode sector mean", source=SOURCE)
        assert isinstance(result, NotAssessed)
        assert "5000 m" in result.detail
        assert "more precise location" in result.detail
        assert result.source is SOURCE

    def test_refusal_is_a_gap_not_an_empty_result(self):
        # The whole point: the caller receives an explanation, and cannot
        # accidentally carry on with zero findings.
        result = resolve_search_radius(250, 5000.0, basis="postcode sector mean")
        assert not hasattr(result, "findings")

    def test_boundary_at_the_ceiling(self):
        exactly = resolve_search_radius(DEFAULT_CEILING_M - 100, 100.0, basis="grid square centre")
        assert isinstance(exactly, SearchRadius)

        over = resolve_search_radius(DEFAULT_CEILING_M - 100, 100.1, basis="grid square centre")
        assert isinstance(over, NotAssessed)

    def test_ceiling_is_configurable(self):
        result = resolve_search_radius(250, 707.1, basis="1 km grid square centre", ceiling_m=500)
        assert isinstance(result, NotAssessed)


class TestArgumentValidation:
    def test_zero_radius_rejected(self):
        with pytest.raises(ValueError, match="must be positive"):
            resolve_search_radius(0, 10.0, basis="x")

    def test_negative_uncertainty_rejected(self):
        with pytest.raises(ValueError, match="cannot be negative"):
            resolve_search_radius(250, -1.0, basis="x")
