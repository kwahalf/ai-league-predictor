"""The interface every backtested model implements (Elo 2.3, Dixon-Coles 2.4, blend 2.5).

The harness drives a model one weekly window at a time:

1. ``fit(history, as_of)``: ``history`` is every played match with ``match_date < as_of``, oldest
   first, as ``MatchRecord`` objects whose odds are cut down to pre-match quotes from
   ``B365``/``BW``/``AVG`` (see ``inputs.history_record``). ``as_of`` is the Monday that starts
   the window. ``fit`` is called once per window with the *whole* history, so a model must not
   carry state from an earlier call that this call's history doesn't justify (caching work over
   the shared prefix is fine).
2. ``predict(match)`` for each match in the window: ``match`` is a ``MatchView`` (teams,
   date/kick-off, season, pre-match odds; no outcome). Returns ``(p_home, p_draw, p_away)``,
   each in [0, 1], summing to 1.

A model never receives a ``MatchRecord`` for a match it predicts, and never receives closing odds.
"""

from __future__ import annotations

from datetime import date
from typing import Protocol, Sequence, runtime_checkable

from ..data import MatchRecord
from .inputs import MatchView

Probabilities = tuple[float, float, float]
"""(p_home, p_draw, p_away)."""


@runtime_checkable
class Model(Protocol):
    name: str
    """Short identifier used in reports, e.g. ``"trivial"`` or ``"elo"``."""

    def fit(self, history: Sequence[MatchRecord], as_of: date) -> None:
        """Fit on matches played strictly before ``as_of`` (the window's Monday)."""
        ...

    def predict(self, match: MatchView) -> Probabilities:
        """Return (p_home, p_draw, p_away) for one match in the window."""
        ...
