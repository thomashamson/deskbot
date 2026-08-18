"""Command line entry point.

    deskbot "SE1 9GF"
    deskbot "TQ 32785 80244" --radius 500
    deskbot "SE1 9GF" --json | jq .flood
    deskbot "SE1 9GF" --no-llm

Exit codes: 0 on success, 1 if the location could not be resolved, 2 on
interruption. A partial report is still a success: gaps are the expected output
for a site outside England, not a failure of the run.
"""

from __future__ import annotations

import argparse
import contextlib
import logging
import sys

from deskbot import advisor
from deskbot.locate import LocateError
from deskbot.render import render
from deskbot.survey import survey_site


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="deskbot",
        description=(
            "Draft a preliminary desk study indication for a UK postcode or OS "
            "grid reference, from public data, with every claim attributed."
        ),
        epilog=(
            "Not a Phase 1 desk study. Anything that could not be checked is "
            "reported as a gap, never as an absence of findings."
        ),
    )
    parser.add_argument(
        "location",
        help="UK postcode ('SE1 9GF') or OS grid reference ('TQ 32785 80244')",
    )
    parser.add_argument(
        "--radius",
        type=float,
        default=250.0,
        metavar="M",
        help="search radius in metres for boreholes and flood proximity (default: 250)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="emit the full structured survey as JSON instead of a text report",
    )
    parser.add_argument(
        "--no-llm",
        action="store_true",
        help="skip the local model; every finding is produced without it",
    )
    parser.add_argument(
        "--model",
        default=advisor.DEFAULT_MODEL,
        help=f"Ollama model for suggestions (default: {advisor.DEFAULT_MODEL})",
    )
    parser.add_argument(
        "--ollama-host",
        default=advisor.DEFAULT_HOST,
        metavar="URL",
        help=f"Ollama base URL (default: {advisor.DEFAULT_HOST})",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="log withheld model output and other diagnostics to stderr",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    # Attributions contain (c) and other non-ASCII the licences require verbatim;
    # the Windows console defaults to cp1252 and mangles them.
    for stream in (sys.stdout, sys.stderr):
        with contextlib.suppress(AttributeError, OSError):
            stream.reconfigure(encoding="utf-8")

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )

    try:
        survey = survey_site(
            args.location,
            radius_m=args.radius,
            use_model=not args.no_llm,
            model=args.model,
            host=args.ollama_host,
        )
    except LocateError as exc:
        # The one fatal case: without a point, nothing else can be attempted.
        print(f"deskbot: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 2

    if args.json:
        print(survey.model_dump_json(indent=2))
    else:
        print(render(survey))
    return 0


if __name__ == "__main__":
    sys.exit(main())
