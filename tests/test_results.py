"""The three-state result model.

These tests exist to stop one specific regression: a gap ever becoming
indistinguishable from an empty result.
"""

import pytest
from pydantic import BaseModel, TypeAdapter, ValidationError

from deskbot.results import (
    Assessed,
    NotAssessed,
    NotAssessedReason,
    SourceRef,
    SourceResult,
    not_assessed,
)


class Borehole(BaseModel):
    reference: str


SOURCE = SourceRef(
    name="BGS SOBI",
    url="https://map.bgs.ac.uk/example",
    licence="OGL v3",
    attribution="Contains British Geological Survey materials © UKRI 2026",
)


class TestThreeStatesAreDistinct:
    def test_assessed_with_findings(self):
        result = Assessed[Borehole](findings=[Borehole(reference="TQ38SW10")], source=SOURCE)
        assert result.status == "assessed"
        assert not result.is_empty

    def test_assessed_with_nothing_found_is_a_real_result(self):
        # NE19: the borehole index was queried and there is genuinely nothing
        # within the search radius. This is a finding, not a gap.
        result = Assessed[Borehole](findings=[], source=SOURCE)
        assert result.status == "assessed"
        assert result.is_empty

    def test_not_assessed_is_not_an_empty_result(self):
        # Edinburgh: the EA flood endpoints would answer count:0 here, which is
        # why this must never be built as Assessed(findings=[]).
        gap = not_assessed(
            NotAssessedReason.OUTSIDE_COVERAGE,
            "Environment Agency flood data covers England only.",
            SOURCE,
        )
        assert gap.status == "not_assessed"

    def test_the_two_empty_looking_states_never_compare_equal(self):
        assessed_empty = Assessed[Borehole](findings=[], source=SOURCE)
        gap = not_assessed(NotAssessedReason.OUTSIDE_COVERAGE, "England only.", SOURCE)
        assert assessed_empty != gap
        assert assessed_empty.status != gap.status


class TestConflationIsUnrepresentable:
    """The point of the discriminated union: not a convention, an impossibility."""

    def test_gap_has_no_findings_attribute_at_all(self):
        gap = not_assessed(NotAssessedReason.SOURCE_UNAVAILABLE, "Timed out.")
        assert not hasattr(gap, "findings")
        with pytest.raises(AttributeError):
            _ = gap.findings

    def test_gap_cannot_be_constructed_with_findings(self):
        # Pydantic rejects the unknown field rather than silently dropping it,
        # so 'not assessed, but here are some results' cannot be expressed.
        with pytest.raises(ValidationError):
            NotAssessed(
                reason=NotAssessedReason.OUTSIDE_COVERAGE,
                detail="England only.",
                findings=[],
            )

    def test_assessment_cannot_be_constructed_without_a_source(self):
        # Every claim must be attributable.
        with pytest.raises(ValidationError):
            Assessed[Borehole](findings=[])

    def test_gap_requires_a_reason_and_a_detail(self):
        with pytest.raises(ValidationError):
            NotAssessed(reason=NotAssessedReason.OUTSIDE_COVERAGE)


class TestSerialisation:
    """A gap must survive a round trip through the wire as a gap."""

    adapter = TypeAdapter(SourceResult[Borehole])

    def test_discriminator_routes_to_the_right_variant(self):
        assessed = self.adapter.validate_python(
            {"status": "assessed", "findings": [{"reference": "X"}], "source": SOURCE.model_dump()}
        )
        assert isinstance(assessed, Assessed)

        gap = self.adapter.validate_python(
            {
                "status": "not_assessed",
                "reason": "outside_coverage",
                "detail": "England only.",
            }
        )
        assert isinstance(gap, NotAssessed)

    def test_empty_assessment_round_trips_without_becoming_a_gap(self):
        original = Assessed[Borehole](findings=[], source=SOURCE)
        restored = self.adapter.validate_python(original.model_dump())
        assert isinstance(restored, Assessed)
        assert restored.is_empty

    def test_gap_round_trips_without_becoming_an_empty_assessment(self):
        original = not_assessed(
            NotAssessedReason.INSUFFICIENT_LOCATION_PRECISION, "Too coarse.", SOURCE
        )
        restored = self.adapter.validate_python(original.model_dump())
        assert isinstance(restored, NotAssessed)
        assert restored.reason is NotAssessedReason.INSUFFICIENT_LOCATION_PRECISION


class TestReasons:
    def test_every_reason_means_we_do_not_know(self):
        # Guards against a future 'no_risk' or 'clear' creeping in, which would
        # smuggle a finding into the vocabulary of gaps.
        assert set(NotAssessedReason) == {
            NotAssessedReason.OUTSIDE_COVERAGE,
            NotAssessedReason.INSUFFICIENT_LOCATION_PRECISION,
            NotAssessedReason.SOURCE_UNAVAILABLE,
            NotAssessedReason.NOT_QUERYABLE_AT_A_POINT,
            NotAssessedReason.WITHHELD_UNVERIFIED,
            NotAssessedReason.NOT_REQUESTED,
        }

    def test_gap_can_still_name_the_dataset_it_could_not_consult(self):
        gap = not_assessed(NotAssessedReason.OUTSIDE_COVERAGE, "England only.", SOURCE)
        assert gap.source is not None
        assert gap.source.name == "BGS SOBI"
