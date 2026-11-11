"""Walk-forward backtest harness for match models (plan step 2.2). Standard library only.

- ``model.Model``: the interface models implement (``fit(history, as_of)``, ``predict(view)``).
- ``inputs``: the leakage boundary; what a model may see (``MatchView``, ``history_record``).
- ``harness.run_backtest``: the weekly walk-forward loop, scored with the 2.1a metrics against the
  market consensus benchmark.
- ``cli``: ``python -m src.football.backtest --model trivial`` (run from ``temporal/``).
"""

from .harness import (
    DEFAULT_WARMUP_SEASONS,
    BacktestError,
    BacktestResult,
    MatchPrediction,
    Scores,
    Summary,
    run_backtest,
    summarise,
    week_start,
)
from .inputs import (
    MODEL_ODDS_SNAPSHOT,
    MODEL_ODDS_SOURCES,
    MatchView,
    history_record,
    match_view,
    model_input_odds,
)
from .market import margin_free_1x2, market_consensus
from .model import Model, Probabilities
from .report import format_report
from .trivial import TRIVIAL_PROBABILITIES, TrivialModel

__all__ = [
    "DEFAULT_WARMUP_SEASONS",
    "MODEL_ODDS_SNAPSHOT",
    "MODEL_ODDS_SOURCES",
    "TRIVIAL_PROBABILITIES",
    "BacktestError",
    "BacktestResult",
    "MatchPrediction",
    "MatchView",
    "Model",
    "Probabilities",
    "Scores",
    "Summary",
    "TrivialModel",
    "format_report",
    "history_record",
    "margin_free_1x2",
    "market_consensus",
    "match_view",
    "model_input_odds",
    "run_backtest",
    "summarise",
    "week_start",
]
