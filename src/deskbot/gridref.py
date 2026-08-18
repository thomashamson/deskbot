"""Ordnance Survey National Grid reference parsing.

Pure arithmetic on the 100 km letter squares. No network, no dependencies.

A grid reference denotes a *square*, not a point, and the square's size depends
on how many digits were given. That size is the whole reason this module returns
a precision alongside the coordinates: a two-figure reference is a kilometre
across, which is wider than any borehole search we would run.

By OS convention a reference denotes the south-west corner of its square. We
resolve to the *centre* instead, because the coordinate is used as a
representative query point and the corner is a systematically biased one. The
square size is preserved so the bias we removed can still be reported.
"""

from __future__ import annotations

import math
import re

from pydantic import BaseModel, ConfigDict

# 'I' is not used in the National Grid.
_LETTERS = "ABCDEFGHJKLMNOPQRSTUVWXYZ"

# The GB grid is 7 x 13 hundred-kilometre squares from the false origin at SV.
_MAX_E100KM = 6
_MAX_N100KM = 12

_GRIDREF_RE = re.compile(r"^([A-Z]{2})\s*([0-9]+(?:\s+[0-9]+)?)$")


class InvalidGridReferenceError(ValueError):
    """The input is not a usable OS National Grid reference."""


class GridReference(BaseModel):
    """A parsed National Grid reference, resolved to the centre of its square."""

    model_config = ConfigDict(frozen=True)

    easting: int
    northing: int
    """Centre of the denoted square, in EPSG:27700 metres."""

    square_size_m: int
    """Side length of the square the reference denotes: 1, 10, 100 or 1000 m."""

    normalised: str
    """Canonical form, e.g. 'TQ 32785 80244'."""

    @property
    def precision_m(self) -> float:
        """Radius within which the true location lies, from the square centre.

        The half-diagonal, not the half-side: a point in the corner of a 1 km
        square is 707 m from the centre, not 500 m. Understating this would
        defeat the purpose of carrying it.
        """
        return self.square_size_m * math.sqrt(2) / 2


def _letters_to_100km(letters: str) -> tuple[int, int]:
    """Convert a two-letter square to its 100 km indices from the false origin."""
    try:
        l1 = _LETTERS.index(letters[0])
        l2 = _LETTERS.index(letters[1])
    except ValueError as exc:
        raise InvalidGridReferenceError(
            f"{letters!r} contains a letter not used in the National Grid "
            "(the letter 'I' is skipped)"
        ) from exc

    e100km = ((l1 - 2) % 5) * 5 + (l2 % 5)
    n100km = (19 - (l1 // 5) * 5) - (l2 // 5)

    if not (0 <= e100km <= _MAX_E100KM and 0 <= n100km <= _MAX_N100KM):
        raise InvalidGridReferenceError(
            f"{letters!r} is not a National Grid square covering Great Britain"
        )
    return e100km, n100km


def parse(raw: str) -> GridReference:
    """Parse an OS grid reference.

    Accepts the usual written forms, with or without internal spaces::

        TQ 32785 80244   1 m square
        TQ 3278 8024     10 m
        TQ 327 801       100 m
        TQ 32 80         1 km

    Raises:
        InvalidGridReferenceError: if the reference cannot be parsed, uses a letter
            outside the grid, names a square outside Great Britain, or gives an
            odd number of digits (which cannot be split into easting and
            northing).
    """
    text = raw.strip().upper()
    match = _GRIDREF_RE.match(text)
    if match is None:
        raise InvalidGridReferenceError(
            f"{raw!r} is not a grid reference; expected two letters followed by "
            "an even number of digits, e.g. 'TQ 32785 80244'"
        )

    letters, digits_part = match.groups()
    digits = digits_part.replace(" ", "")

    if len(digits) % 2 != 0:
        raise InvalidGridReferenceError(
            f"{raw!r} has {len(digits)} digits; a grid reference needs an even "
            "number so it can split into easting and northing"
        )
    if not 2 <= len(digits) <= 10:
        raise InvalidGridReferenceError(
            f"{raw!r} has {len(digits)} digits; expected between 2 and 10"
        )

    e100km, n100km = _letters_to_100km(letters)

    half = len(digits) // 2
    e_digits, n_digits = digits[:half], digits[half:]

    # 5 digits per axis addresses 1 m; each digit dropped multiplies the square.
    square_size_m = 10 ** (5 - half)

    # South-west corner of the denoted square, then offset to its centre.
    sw_easting = e100km * 100_000 + int(e_digits) * square_size_m
    sw_northing = n100km * 100_000 + int(n_digits) * square_size_m
    offset = square_size_m // 2

    return GridReference(
        easting=sw_easting + offset,
        northing=sw_northing + offset,
        square_size_m=square_size_m,
        normalised=f"{letters} {e_digits} {n_digits}",
    )


def looks_like_grid_reference(raw: str) -> bool:
    """Cheap check used to route input, without committing to a parse."""
    return _GRIDREF_RE.match(raw.strip().upper()) is not None
