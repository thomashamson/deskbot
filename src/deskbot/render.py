"""Rendering a survey as text.

Every fact here comes from the structured models. The reasoning model never
reaches this module except through its own clearly-labelled section.

The rule this file enforces: **a gap never travels through the code path that
renders a finding.** Sections render only what was assessed; everything that was
not assessed is collected into its own block, and the header says so before the
reader has read anything else. A section that quietly lacks content reads as
reassurance, which is the failure the whole project is built to avoid.
"""

from __future__ import annotations

import textwrap
from collections.abc import Iterable

from deskbot.geology import GeologyLayer
from deskbot.results import Assessed, NotAssessed
from deskbot.survey import SiteSurvey

_WIDTH = 88


def _wrap(text: str, indent: str = "  ") -> str:
    return "\n".join(textwrap.wrap(text, _WIDTH, initial_indent=indent, subsequent_indent=indent))


def _bullet(text: str) -> str:
    """A list item that keeps its hang when it wraps."""
    return textwrap.fill(text, _WIDTH, initial_indent="  - ", subsequent_indent="    ")


def _rule(char: str = "-") -> str:
    return char * _WIDTH


def _heading(title: str) -> list[str]:
    return ["", title.upper(), _rule()]


def _header(survey: SiteSurvey) -> list[str]:
    loc = survey.location
    lines = [
        _rule("="),
        "DESKBOT PRELIMINARY DESK STUDY INDICATION",
        _rule("="),
        f"  Site        {loc.normalised_input}  ({loc.input_kind.value.replace('_', ' ')})",
        f"  Grid        E{loc.easting} N{loc.northing}   {loc.country.value}",
        f"  Precision   +/-{loc.precision_m:.0f} m ({loc.precision_basis})",
    ]
    if loc.latitude is not None and loc.longitude is not None:
        lines.append(f"  WGS84       {loc.latitude:.6f}, {loc.longitude:.6f}")

    # Declared before any finding, so a partial report is visible to someone who
    # reads only the top of the page.
    if survey.is_partial:
        count = len(survey.gaps)
        lines += [
            "",
            f"  *** PARTIAL: {count} item{'s' if count != 1 else ''} could NOT be "
            "assessed. See NOT ASSESSED below. ***",
            "  Absence of a finding below does not mean absence of the thing.",
        ]
    return lines


def _geology(survey: SiteSurvey) -> list[str]:
    lines = _heading("Geology")
    report = survey.geology
    labels = {
        GeologyLayer.BEDROCK: "Bedrock",
        GeologyLayer.SUPERFICIAL: "Superficial",
        GeologyLayer.ARTIFICIAL_GROUND: "Artificial ground",
        GeologyLayer.MASS_MOVEMENT: "Mass movement",
    }
    for layer, label in labels.items():
        result = report.layer(layer)
        if isinstance(result, NotAssessed):
            continue  # Rendered in NOT ASSESSED, never as a blank finding.
        if result.is_empty:
            lines.append(f"  {label:18} none mapped here (layer checked)")
        else:
            for unit in result.findings:
                lines.append(f"  {label:18} {unit.describe()}")
                age = ", ".join(x for x in (unit.max_period, unit.max_epoch) if x)
                if age:
                    lines.append(f"  {'':18} age: {age}")
                if unit.lexicon_url:
                    lines.append(f"  {'':18} {unit.lexicon_url}")

    note = report.variation.describe()
    if note:
        lines += ["", _wrap(note)]
    return lines


def _terrain(survey: SiteSurvey) -> list[str]:
    if isinstance(survey.terrain.ground_level, NotAssessed):
        return []
    lines = _heading("Terrain")
    level = survey.terrain.ground_level.findings[0]
    lines.append(f"  {level.describe()}")
    if level.tile:
        lines.append(f"  Tile {level.tile}, {level.resolution}, composite {level.composite_year}")
    if survey.terrain.relief:
        lines += ["", _wrap(survey.terrain.relief.describe())]
    return lines


def _boreholes(survey: SiteSurvey) -> list[str]:
    result = survey.boreholes.records
    if isinstance(result, NotAssessed):
        return []
    lines = _heading("Borehole records")
    summary = survey.boreholes.describe()
    if summary:
        lines.append(_wrap(summary))
    if result.findings:
        lines.append("")
        for record in result.findings:
            scan = {
                "free_online": "free scan",
                "purchase": "purchase only",
                "none": "no scan",
            }[record.scan.value]
            ags = " +AGS" if record.ags_log_url else ""
            lines.append(f"    {record.describe()}  [{scan}{ags}]")
    return lines


def _flood(survey: SiteSurvey) -> list[str]:
    if not survey.flood.assessed:
        return []
    lines = _heading("Flood")
    for result in (survey.flood.planning, survey.flood.surface_water):
        if isinstance(result, Assessed):
            for presence in result.findings:
                lines.append(f"  {presence.describe()}")
    lines += ["", _wrap(survey.flood.describe())]
    return lines


def _suggestions(survey: SiteSurvey) -> list[str]:
    result = survey.suggestions
    if isinstance(result, NotAssessed):
        return []
    lines = _heading("Suggested next checks")
    lines.append(
        _wrap(
            "Generated locally by a language model. Advisory only: these are not "
            "findings, are attributable to no dataset, and have not been verified."
        )
    )
    lines.append("")
    lines += [_bullet(s) for s in result.findings]
    return lines


def _not_assessed(survey: SiteSurvey) -> list[str]:
    if not survey.is_partial:
        return []
    lines = ["", "NOT ASSESSED", _rule("=")]
    lines.append(
        _wrap(
            "The following could not be established. None of these is a finding of "
            "absence: they are things this report does not know."
        )
    )
    section = None
    for gap in survey.gaps:
        if gap.section != section:
            section = gap.section
            lines += ["", f"  {section}"]
        lines.append(f"    {gap.subject}  [{gap.result.reason.value}]")
        lines.append(_wrap(gap.result.detail, indent="      "))
    return lines


def _sources(survey: SiteSurvey) -> list[str]:
    seen: dict[str, str] = {}

    def collect(results: Iterable[object]) -> None:
        for result in results:
            source = getattr(result, "source", None)
            if source is not None:
                seen.setdefault(source.name, source.attribution)

    collect(
        [
            survey.geology.bedrock,
            survey.geology.superficial,
            survey.geology.faults,
            survey.boreholes.records,
            survey.flood.planning,
            survey.flood.surface_water,
            survey.terrain.ground_level,
            survey.suggestions,
        ]
    )
    for source in survey.location.sources:
        seen.setdefault(source.name, source.attribution)

    lines = ["", "SOURCES AND ATTRIBUTION", _rule("=")]
    for name, attribution in sorted(seen.items()):
        lines.append(f"  {name}")
        lines.append(_wrap(attribution, indent="      "))
    return lines


def render(survey: SiteSurvey) -> str:
    """Render the whole survey as plain text."""
    blocks: list[list[str]] = [
        _header(survey),
        _geology(survey),
        _terrain(survey),
        _boreholes(survey),
        _flood(survey),
        _not_assessed(survey),
        _suggestions(survey),
        _sources(survey),
        [
            "",
            _rule("="),
            _wrap(
                "This is a preliminary indication assembled from public data. It is "
                "not a Phase 1 desk study, not a site investigation, and not advice.",
            ),
            _rule("="),
        ],
    ]
    return "\n".join(line for block in blocks for line in block)
