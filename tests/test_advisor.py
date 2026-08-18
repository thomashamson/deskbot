"""The guard on the local model.

The model is advisory only. These tests cover the check that decides whether its
output is shown at all, and the two failures actually observed from it: inventing
a figure, and asserting an absence.
"""

import pytest

from deskbot.advisor import _numbers, _parse_suggestions, check, suggest
from deskbot.results import Assessed, NotAssessed, NotAssessedReason

FACTS = """SITE: SE1 9GF, E532785 N180244, England.
- bedrock: London Clay Formation (Clay, silt and sand)
- boreholes: 89 borehole records within 260 m, nearest at 4 m.
- flood: Surface water flooding, high risk (3.3% annual chance) mapped within 260 m.
NOT ASSESSED:
- Faults: mapped as lines; a point query cannot intersect them.
"""


class TestGrounding:
    def test_output_using_only_supplied_figures_passes(self):
        assert check("- Obtain the 89 borehole logs within 260 m.", FACTS) == []

    def test_invented_figure_is_caught(self):
        violations = check("- Expect around 15 m of made ground.", FACTS)
        assert [v.kind for v in violations] == ["ungrounded_number"]
        assert "15" in violations[0].detail

    def test_list_ordinals_are_not_treated_as_claims(self):
        # "1." and "2." are formatting, not figures.
        assert check("1. Obtain borehole logs.\n2. Check defences.", FACTS) == []

    def test_numbers_helper_strips_markers(self):
        assert _numbers("1. nothing\n2. here") == set()
        assert "89" in _numbers("- there are 89 records")

    def test_empty_output_is_a_violation(self):
        assert [v.kind for v in check("   ", FACTS)] == ["empty"]


class TestAbsenceClaims:
    """The exact phrasing that turns an unassessed gap into false comfort."""

    @pytest.mark.parametrize(
        "text",
        [
            "- The site is at no risk of flooding.",
            "- There is no contamination to consider.",
            "- The site is clear of landslide hazard.",
            "- No further investigation is required.",
            "- The ground is safe from movement.",
        ],
    )
    def test_absence_claims_are_caught(self, text):
        kinds = [v.kind for v in check(text, FACTS)]
        assert "absence_claim" in kinds

    def test_quoting_the_facts_is_allowed(self):
        # 'high risk' is a dataset label present in the facts, so echoing it is
        # quotation. Banning it outright would make the labels unusable.
        assert check("- Review the high risk surface water extent.", FACTS) == []

    def test_same_phrase_is_banned_when_not_in_the_facts(self):
        bare = "SITE: somewhere.\n- bedrock: London Clay Formation"
        violations = check("- The site is at no risk.", bare)
        assert any(v.kind == "absence_claim" for v in violations)

    def test_violation_names_the_offending_phrase(self):
        violations = check("- There is no flood hazard.", FACTS)
        claim = next(v for v in violations if v.kind == "absence_claim")
        assert "no flood" in claim.detail or "there is no" in claim.detail


class TestParsing:
    def test_bullets_and_numbers_are_stripped(self):
        parsed = _parse_suggestions("- Obtain logs\n2. Check defences\n* Confirm levels")
        assert parsed == ["Obtain logs", "Check defences", "Confirm levels"]

    def test_headings_and_blanks_are_dropped(self):
        assert _parse_suggestions("Suggestions:\n\n- Obtain logs\n") == ["Obtain logs"]


class TestUnavailableModel:
    def test_unreachable_ollama_is_a_gap_not_a_silent_omission(self):
        # Deliberately a dead port. The report must still be produced.
        result = suggest(FACTS, host="http://127.0.0.1:1")
        assert isinstance(result, NotAssessed)
        assert result.reason is NotAssessedReason.SOURCE_UNAVAILABLE
        assert "unaffected" in result.detail

    def test_gap_carries_no_findings(self):
        assert not hasattr(suggest(FACTS, host="http://127.0.0.1:1"), "findings")


@pytest.mark.network
class TestAgainstTheRealModel:
    def test_suggestions_are_actions_not_findings(self):
        result = suggest(FACTS)
        if isinstance(result, NotAssessed):
            # Withholding is a valid outcome; it must still explain itself.
            assert result.reason is NotAssessedReason.WITHHELD_UNVERIFIED
            assert result.detail
            return
        assert isinstance(result, Assessed)
        assert result.findings
        assert result.source is not None
        assert "Advisory only" in result.source.attribution

    def test_whatever_is_returned_passes_its_own_check(self):
        result = suggest(FACTS)
        if isinstance(result, Assessed):
            assert check("\n".join(result.findings), FACTS) == []
