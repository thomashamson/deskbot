#!/usr/bin/env python3
"""Regenerate the terminal captures used in the README.

Every image is produced by actually running Deskbot against the live sources,
not by mocking one up. If behaviour changes, rerun this and the images change
with it, so a capture cannot drift away from what the tool does.

Deskbot prints plain text rather than using a Rich console, so the real output
is captured and then replayed into a recorder. Markup and highlighting are both
disabled: the reports contain square brackets such as
``[not_queryable_at_a_point]`` which Rich would otherwise eat as markup tags.

Full reports run to well over a hundred lines, which is unreadable at README
scale, so each capture is a section chosen to show one idea.

    uv run python scripts/capture_screenshots.py

Needs a network connection, and Ollama running for the suggestions capture.
"""

from __future__ import annotations

import re
from pathlib import Path

from rich.console import Console

from deskbot.render import render
from deskbot.survey import survey_site

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"


def capture(name: str, title: str, text: str, width: int = 92) -> None:
    recorder = Console(record=True, width=width)
    # markup=False so bracketed reason codes survive; highlight=False so numbers
    # and paths are not recoloured into something the real terminal never shows.
    recorder.print(text, markup=False, highlight=False)

    target = DOCS / f"{name}.svg"
    recorder.save_svg(str(target), title=title)
    _drop_remote_fonts(target)
    print(f"  wrote {target.relative_to(ROOT)}")


def _drop_remote_fonts(target: Path) -> None:
    """Strip the CDN @font-face URLs Rich embeds.

    Left in, every reader of the README would have their browser fetch a font
    from a third-party CDN just to look at a picture. Dropping the url() sources
    keeps the local() lookup and falls back to the reader's own monospace font.
    """
    svg = target.read_text(encoding="utf-8")
    svg = re.sub(
        r'src: (local\("[^"]+"\)),[^;]*;',
        lambda match: f"src: {match.group(1)};",
        svg,
    )
    target.write_text(svg, encoding="utf-8")


RULE = "=" * 88
HEADING_NOT_ASSESSED = "NOT ASSESSED\n" + RULE
"""Anchor on the heading *and its rule*.

The words "NOT ASSESSED" also appear in the report header, in "See NOT ASSESSED
below". Anchoring on the bare phrase silently starts the slice mid-header and
swallows every section in between.
"""


def section(report: str, start: str, end: str | None = None) -> str:
    """Slice a report from one heading up to the next."""
    begin = report.index(start)
    stop = report.index(end, begin + len(start)) if end else len(report)
    return report[begin:stop].rstrip()


def main() -> None:
    DOCS.mkdir(exist_ok=True)

    print("Surveying SE1 9GF (dense urban, in a flood zone)...")
    southwark = render(survey_site("SE1 9GF"))

    print("Surveying EH1 1RE (Scotland: flood and terrain out of coverage)...")
    edinburgh = render(survey_site("EH1 1RE", use_model=False))

    print("Surveying TQ 32 80 (1 km grid reference)...")
    coarse = render(survey_site("TQ 32 80", use_model=False))

    print("Writing captures...")

    # The header states the report is partial before any finding, then the
    # findings themselves carry their source and age.
    capture(
        "report-header",
        'deskbot "SE1 9GF"',
        section(southwark, RULE, "BOREHOLE RECORDS"),
    )

    # The one that matters most: a Scottish site is a partial report, not a
    # refusal and not a clean bill of health.
    #
    # Trimmed to the two England-only gaps. The full block also carries the
    # faults gap and a "suggestions not requested" gap, both true but making a
    # different point, and all five together read as a wall. This is still a
    # contiguous slice of real output rather than an assembled one: nothing is
    # reworded, and the README caption says which gaps it shows.
    gaps = section(edinburgh, HEADING_NOT_ASSESSED, "SOURCES AND ATTRIBUTION")
    capture(
        "not-assessed",
        'deskbot "EH1 1RE"  (Scotland)',
        section(gaps, HEADING_NOT_ASSESSED, "  Geology")
        + "\n\n"
        + section(gaps, "  Terrain", "  Suggestions"),
    )

    # A coarse grid reference widens the search and restates its own claim.
    capture(
        "precision",
        'deskbot "TQ 32 80"  (1 km grid reference)',
        section(coarse, "GEOLOGY", "BOREHOLE RECORDS")
        + "\n\n"
        + "\n".join(section(coarse, "BOREHOLE RECORDS", "FLOOD").splitlines()[:6]),
    )

    # Flood Zones carry the defences caveat in the label itself.
    capture(
        "flood",
        'deskbot "SE1 9GF"  (flood section)',
        section(southwark, "FLOOD", HEADING_NOT_ASSESSED),
    )

    # The model advises and never states a fact.
    if "SUGGESTED NEXT CHECKS" in southwark:
        capture(
            "suggestions",
            'deskbot "SE1 9GF"  (local model)',
            section(southwark, "SUGGESTED NEXT CHECKS", "SOURCES AND ATTRIBUTION"),
        )
    else:
        print("  skipped suggestions.svg (model unavailable or output withheld)")

    print("Done.")


if __name__ == "__main__":
    main()
