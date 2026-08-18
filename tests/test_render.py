"""Rendering, and the guarantee that a gap never looks like a finding."""

import pytest

from deskbot.advisor import advisor_source
from deskbot.cli import build_parser, main
from deskbot.render import render
from deskbot.results import Assessed, NotAssessedReason, not_assessed
from deskbot.survey import survey_site


def flat(text: str) -> str:
    """Collapse wrapping before asserting on a phrase.

    The report is hard-wrapped, so a phrase can straddle a newline. That would
    make a positive assertion fail spuriously and, worse, make a negative
    assertion pass while the phrase is actually present.
    """
    return " ".join(text.split())


@pytest.fixture(scope="module")
def southwark():
    return survey_site("SE1 9GF", use_model=False)


@pytest.fixture(scope="module")
def edinburgh():
    return survey_site("EH1 1RE", use_model=False)


class TestCliParser:
    def test_defaults(self):
        args = build_parser().parse_args(["SE1 9GF"])
        assert args.radius == 250.0
        assert not args.json
        assert not args.no_llm

    def test_flags(self):
        args = build_parser().parse_args(["SE1 9GF", "--radius", "500", "--json", "--no-llm"])
        assert args.radius == 500.0
        assert args.json
        assert args.no_llm

    def test_unresolvable_location_exits_nonzero(self, capsys):
        assert main(["not a location", "--no-llm"]) == 1
        assert "deskbot:" in capsys.readouterr().err


class TestSuggestionsSection:
    def test_withheld_suggestions_are_not_silently_dropped(self, southwark):
        survey = southwark.model_copy(
            update={
                "suggestions": not_assessed(
                    NotAssessedReason.WITHHELD_UNVERIFIED,
                    "Suggestions failed verification: invented a figure.",
                    advisor_source("qwen2.5:7b", "http://127.0.0.1:11434"),
                )
            }
        )
        text = render(survey)
        assert "SUGGESTED NEXT CHECKS" not in text
        assert "failed verification" in flat(text)
        assert "withheld_unverified" in flat(text)

    def test_shown_suggestions_are_labelled_advisory(self, southwark):
        survey = southwark.model_copy(
            update={
                "suggestions": Assessed[str](
                    findings=["Obtain the borehole logs."],
                    source=advisor_source("qwen2.5:7b", "http://127.0.0.1:11434"),
                )
            }
        )
        text = render(survey)
        assert "SUGGESTED NEXT CHECKS" in text
        assert "Advisory only" in flat(text)
        assert "not findings" in flat(text)


@pytest.mark.network
class TestEnglishSite:
    def test_findings_are_rendered(self, southwark):
        text = render(southwark)
        assert "London Clay Formation" in flat(text)
        assert "Kempton Park Gravel Member" in flat(text)
        assert "m AOD" in text
        assert "borehole records" in flat(text)

    def test_faults_gap_appears_only_in_the_gaps_block(self, southwark):
        text = render(southwark)
        head, _, gaps = text.partition("NOT ASSESSED")
        assert "Faults" not in head
        assert "Faults" in gaps

    def test_header_declares_the_report_is_partial(self, southwark):
        assert "*** PARTIAL:" in flat(render(southwark))

    def test_defences_caveat_survives_into_the_report(self, southwark):
        assert "ignores flood defences" in flat(render(southwark))

    def test_attribution_is_reproduced(self, southwark):
        text = render(southwark)
        assert "Contains British Geological Survey materials" in flat(text)
        assert "Environment Agency copyright" in flat(text)
        assert "Open Government Licence" in flat(text)

    def test_report_states_it_is_not_a_phase_1(self, southwark):
        assert "not a Phase 1 desk study" in flat(render(southwark))


@pytest.mark.network
class TestScottishSite:
    """The case the whole design exists for."""

    def test_gaps_are_declared_in_the_header(self, edinburgh):
        assert "*** PARTIAL:" in flat(render(edinburgh))
        assert "Absence of a finding below does not mean absence of the thing" in flat(
            render(edinburgh)
        )

    def test_no_flood_section_is_rendered_at_all(self, edinburgh):
        # Not an empty flood section: no flood section. An empty one reads as
        # "checked, nothing found".
        text = render(edinburgh)
        head, _, _gaps = text.partition("NOT ASSESSED")
        assert "\nFLOOD\n" not in head

    def test_gaps_block_names_country_and_alternative(self, edinburgh):
        text = render(edinburgh)
        flattened = flat(text)
        assert "outside_coverage" in flattened
        assert "Scotland" in flattened
        assert "SEPA" in flattened

    def test_report_never_says_there_is_no_flood_risk(self, edinburgh):
        lowered = flat(render(edinburgh)).lower()
        for phrase in ("no flood risk", "not at risk", "no risk of flooding"):
            assert phrase not in lowered

    def test_gb_wide_sources_still_report(self, edinburgh):
        # Geology and boreholes are Great Britain wide, so a Scottish site is a
        # partial report, not a refusal.
        text = render(edinburgh)
        assert "Ballagan Formation" in flat(text)
        assert "borehole records" in flat(text)
