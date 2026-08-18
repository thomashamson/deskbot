"""Running every source for one location, and collecting what could not be run.

A :class:`SiteSurvey` is the whole product: five assessments plus the advisory
suggestions, each independently assessed-or-not.

The important property is :attr:`SiteSurvey.gaps`. Every gap across every source
is collectable in one place, so a report can state up front that it is partial
without the reader having to reach the section that is missing. A gap that only
appears deep in the body is a gap a skim-reader will not see.
"""

from __future__ import annotations

import httpx
from pydantic import BaseModel, ConfigDict

from deskbot import advisor
from deskbot.boreholes import BoreholeReport, boreholes
from deskbot.flood import FloodReport, flood
from deskbot.geology import GeologyReport, geology
from deskbot.locate import Location, locate
from deskbot.results import Assessed, NotAssessed, NotAssessedReason, SourceResult, not_assessed
from deskbot.terrain import TerrainReport, terrain


class Gap(BaseModel):
    """One thing that could not be assessed, and where it belongs."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    section: str
    subject: str
    result: NotAssessed

    def describe(self) -> str:
        return f"{self.subject}: {self.result.detail}"


class SiteSurvey(BaseModel):
    """Everything Deskbot can establish about one location."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    location: Location
    geology: GeologyReport
    boreholes: BoreholeReport
    flood: FloodReport
    terrain: TerrainReport
    suggestions: SourceResult[str]

    @property
    def gaps(self) -> tuple[Gap, ...]:
        """Every unassessed thing, in report order.

        Collected rather than derived per section so a partial report can be
        declared in the header.
        """
        found: list[Gap] = []

        def add(section: str, subject: str, result: object) -> None:
            if isinstance(result, NotAssessed):
                found.append(Gap(section=section, subject=subject, result=result))

        add("Geology", "Bedrock", self.geology.bedrock)
        add("Geology", "Superficial deposits", self.geology.superficial)
        add("Geology", "Artificial ground", self.geology.artificial_ground)
        add("Geology", "Mass movement", self.geology.mass_movement)
        found.append(Gap(section="Geology", subject="Faults", result=self.geology.faults))
        add("Terrain", "Ground level", self.terrain.ground_level)
        add("Boreholes", "Borehole records", self.boreholes.records)
        add("Flood", "Flood Map for Planning", self.flood.planning)
        add("Flood", "Surface water flooding", self.flood.surface_water)
        add("Suggestions", "Suggested next checks", self.suggestions)
        return tuple(found)

    @property
    def is_partial(self) -> bool:
        return bool(self.gaps)


def _facts_for_model(survey_without_suggestions: SiteSurvey) -> str:
    """The prompt input: established findings, and what was not established.

    Deliberately the same text the reader sees, so any figure the model produces
    can be checked against what it was actually told.
    """
    survey = survey_without_suggestions
    lines = [
        f"SITE: {survey.location.normalised_input}, "
        f"E{survey.location.easting} N{survey.location.northing}, "
        f"{survey.location.country.value}.",
        "",
        "ESTABLISHED FINDINGS (already reported; do not restate):",
    ]

    geo = survey.geology
    for label, result in (
        ("bedrock", geo.bedrock),
        ("superficial deposits", geo.superficial),
        ("artificial ground", geo.artificial_ground),
        ("mass movement", geo.mass_movement),
    ):
        if isinstance(result, Assessed):
            if result.findings:
                lines.append(f"- {label}: {result.findings[0].describe()}")
            else:
                lines.append(f"- {label}: checked, none mapped here")

    if isinstance(survey.terrain.ground_level, Assessed):
        lines.append(f"- terrain: {survey.terrain.describe()}")
    if isinstance(survey.boreholes.records, Assessed):
        lines.append(f"- boreholes: {survey.boreholes.describe()}")
    if isinstance(survey.flood.planning, Assessed):
        lines.append(f"- flood: {survey.flood.describe()}")

    gaps = [g for g in survey.gaps if g.section != "Suggestions"]
    if gaps:
        lines += ["", "NOT ASSESSED (you may suggest obtaining these; never infer them):"]
        lines += [f"- {g.subject}: {g.result.detail}" for g in gaps]
    return "\n".join(lines)


def survey_site(
    raw_location: str,
    *,
    radius_m: float = 250.0,
    use_model: bool = True,
    model: str = advisor.DEFAULT_MODEL,
    host: str = advisor.DEFAULT_HOST,
    client: httpx.Client | None = None,
) -> SiteSurvey:
    """Run every source for ``raw_location``.

    Raises:
        LocateError: if the input cannot be resolved. Nothing else can proceed
            without a point, so this is the one failure that stops the run.
    """
    owns_client = client is None
    client = client or httpx.Client()
    try:
        location: Location = locate(raw_location, client=client)

        partial = SiteSurvey(
            location=location,
            geology=geology(location, client=client),
            boreholes=boreholes(location, radius_m=radius_m, client=client),
            flood=flood(location, radius_m=radius_m, client=client),
            terrain=terrain(location, client=client),
            suggestions=not_assessed(
                NotAssessedReason.NOT_REQUESTED,
                "Suggestions were not requested.",
            ),
        )

        if not use_model:
            return partial

        suggestions = advisor.suggest(
            _facts_for_model(partial), model=model, host=host, client=client
        )
        return partial.model_copy(update={"suggestions": suggestions})
    finally:
        if owns_client:
            client.close()
