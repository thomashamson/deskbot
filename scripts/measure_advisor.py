#!/usr/bin/env python3
"""Measure how often the local model has to be overruled.

The advisor is allowed to suggest and forbidden to assert. This script measures
how often it tries to assert anyway, so the guard's value is a number rather
than an anecdote.

It fetches the real findings for a few sites once, then runs the model repeatedly
against those fixed facts and counts what the check rejects.

    python scripts/measure_advisor.py            # 5 runs per site
    python scripts/measure_advisor.py --runs 20

Reported per run:
  passed              output was grounded and shown
  absence_claim       tried to assert something was absent or safe
  ungrounded_number   produced a figure not present in the findings
  empty               returned nothing usable
"""

from __future__ import annotations

import argparse
import collections
import sys

from deskbot import advisor
from deskbot.survey import _facts_for_model, survey_site

SITES = [
    ("SE1 9GF", "dense urban, in a flood zone"),
    ("EH1 1RE", "Scotland: flood and terrain not assessed"),
    ("NY 93851 87167", "rural England, little nearby data"),
]


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=int, default=5, help="runs per site (default: 5)")
    parser.add_argument("--model", default=advisor.DEFAULT_MODEL)
    parser.add_argument("--host", default=advisor.DEFAULT_HOST)
    args = parser.parse_args()

    totals: collections.Counter[str] = collections.Counter()
    examples: dict[str, str] = {}
    attempted_absence = 0
    total_runs = 0

    for location, note in SITES:
        print(f"\n{location}  ({note})")
        try:
            survey = survey_site(location, use_model=False)
        except Exception as exc:
            print(f"  could not survey: {exc}")
            continue
        facts = _facts_for_model(survey)

        for run in range(args.runs):
            total_runs += 1
            result = advisor.suggest(facts, model=args.model, host=args.host)
            if hasattr(result, "findings"):
                totals["passed"] += 1
                print(f"  run {run + 1}: passed ({len(result.findings)} suggestions)")
                continue

            detail = result.detail
            kind = (
                "absence_claim"
                if "absence of risk" in detail
                else "ungrounded_number"
                if "figures not present" in detail
                else "unavailable"
                if result.reason.value == "source_unavailable"
                else "empty"
            )
            totals[kind] += 1
            if kind == "absence_claim":
                attempted_absence += 1
            examples.setdefault(kind, detail)
            print(f"  run {run + 1}: WITHHELD [{kind}]")

    print("\n" + "=" * 70)
    print(f"{total_runs} runs of {args.model}")
    for kind, count in totals.most_common():
        share = 100 * count / total_runs if total_runs else 0
        print(f"  {kind:20} {count:3}  ({share:.0f}%)")

    print(f"\nThe model attempted an absence claim in {attempted_absence} of {total_runs} runs.")
    print("Each of those would have read as reassurance if it had been shown.")

    for kind, detail in examples.items():
        print(f"\n  example [{kind}]:\n    {detail[:300]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
