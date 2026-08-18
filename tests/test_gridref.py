"""Grid reference parsing. Pure arithmetic, no network."""

import math

import pytest

from deskbot import gridref
from deskbot.gridref import InvalidGridReferenceError


class TestKnownPoints:
    """Ground truth cross-checked against postcodes.io during reconnaissance."""

    def test_southwark_one_metre_reference(self):
        # SE1 9GF resolves to E532785 N180244 via the ONS Postcode Directory.
        ref = gridref.parse("TQ 32785 80244")
        assert (ref.easting, ref.northing) == (532785, 180244)
        assert ref.square_size_m == 1

    def test_redesdale_one_metre_reference(self):
        # NE19 centroid, E393851 N587167. BGS/EA both served tile NY98nw here.
        ref = gridref.parse("NY 93851 87167")
        assert (ref.easting, ref.northing) == (393851, 587167)

    def test_hundred_km_square_origins(self):
        # TQ is 500 km east, 100 km north of the false origin at SV.
        assert gridref.parse("TQ 00 00").square_size_m == 1000
        ref = gridref.parse("TQ 0000000000")
        assert (ref.easting, ref.northing) == (500000, 100000)


class TestPrecision:
    """A reference denotes a square; its size is the uncertainty."""

    @pytest.mark.parametrize(
        ("text", "size"),
        [
            ("TQ 32 80", 1000),
            ("TQ 327 802", 100),
            ("TQ 3278 8024", 10),
            ("TQ 32785 80244", 1),
        ],
    )
    def test_digit_count_sets_square_size(self, text, size):
        assert gridref.parse(text).square_size_m == size

    def test_precision_is_half_diagonal_not_half_side(self):
        # A corner of a 1 km square is 707 m from its centre, not 500 m.
        # Understating this would defeat the point of carrying it.
        ref = gridref.parse("TQ 32 80")
        assert ref.precision_m == pytest.approx(1000 * math.sqrt(2) / 2)
        assert ref.precision_m == pytest.approx(707.1, abs=0.1)

    def test_coarse_reference_resolves_to_square_centre(self):
        # TQ 32 80 denotes the square from E532000-533000, N180000-181000.
        ref = gridref.parse("TQ 32 80")
        assert (ref.easting, ref.northing) == (532500, 180500)

    def test_one_metre_reference_is_not_offset(self):
        # A 1 m square's centre rounds back to the stated coordinate, so
        # precise references survive the centring unchanged.
        assert gridref.parse("TQ 32785 80244").easting == 532785


class TestFormats:
    @pytest.mark.parametrize(
        "text",
        ["TQ 32785 80244", "TQ3278580244", "tq 32785 80244", "  TQ 32785 80244  "],
    )
    def test_accepted_spellings_agree(self, text):
        ref = gridref.parse(text)
        assert (ref.easting, ref.northing) == (532785, 180244)

    def test_normalised_form_is_canonical(self):
        assert gridref.parse("tq3278580244").normalised == "TQ 32785 80244"


class TestRejections:
    def test_letter_i_is_not_in_the_grid(self):
        with pytest.raises(InvalidGridReferenceError, match="not used in the National Grid"):
            gridref.parse("IQ 32785 80244")

    def test_square_outside_great_britain(self):
        with pytest.raises(InvalidGridReferenceError, match="covering Great Britain"):
            gridref.parse("AA 12345 67890")

    def test_odd_digit_count_cannot_be_split(self):
        with pytest.raises(InvalidGridReferenceError, match="even"):
            gridref.parse("TQ 123")

    def test_too_many_digits(self):
        with pytest.raises(InvalidGridReferenceError, match="between 2 and 10"):
            gridref.parse("TQ 123456 789012")

    @pytest.mark.parametrize("text", ["SE1 9GF", "", "not a location", "12345"])
    def test_non_references_rejected(self, text):
        assert not gridref.looks_like_grid_reference(text)
        with pytest.raises(InvalidGridReferenceError):
            gridref.parse(text)

    def test_postcode_is_not_mistaken_for_a_grid_reference(self):
        # 'SE1 9GF' starts with two letters; the router must not claim it.
        assert not gridref.looks_like_grid_reference("SE1 9GF")
