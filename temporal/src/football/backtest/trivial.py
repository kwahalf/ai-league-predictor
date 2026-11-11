"""The trivial model: the same 1X2 probabilities for every match. Exercises the harness and is the
floor any real model must clearly beat (plan steps 2.3 and 2.4)."""

from __future__ import annotations

from datetime import date
from typing import Sequence

from ..data import MatchRecord
from .inputs import MatchView
from .model import Probabilities

TRIVIAL_PROBABILITIES: Probabilities = (0.45, 0.27, 0.28)
"""Home 45%, draw 27%, away 28%: round numbers close to Premier League base rates."""


class TrivialModel:
    """Ignores history and odds; always predicts ``TRIVIAL_PROBABILITIES``."""

    name = "trivial"

    def __init__(self, probabilities: Probabilities = TRIVIAL_PROBABILITIES) -> None:
        self.probabilities = probabilities

    def fit(self, history: Sequence[MatchRecord], as_of: date) -> None:
        pass

    def predict(self, match: MatchView) -> Probabilities:
        return self.probabilities
