"""Plain-text report of a ``BacktestResult``: per-season scores, overall, and calibration."""

from __future__ import annotations

from typing import Sequence

from ..metrics import CalibrationBucket
from .harness import BacktestResult, Scores, Summary


def _season_label(season: str) -> str:
    return f"20{season[:2]}/{season[2:]}" if len(season) == 4 and season.isdigit() else season


def _fmt(scores: Scores | None) -> str:
    if scores is None:
        return f"{'-':>7} {'-':>7} {'-':>7}"
    return f"{scores.rps:7.4f} {scores.log_loss:7.4f} {scores.brier:7.4f}"


def _row(summary: Summary) -> str:
    same = summary.model_on_market_matches
    return (
        f"{_season_label(summary.season):<8} {summary.n_scored:>6}  {_fmt(summary.model)}  "
        f"{(summary.market.n if summary.market else 0):>6}  {_fmt(summary.market)}  "
        f"{(f'{same.rps:7.4f}' if same else '-'):>9}  {summary.n_without_market:>9}"
    )


def _edge(value: float) -> str:
    """A bucket edge with as many decimals as it needs (0.05 stays 0.05, 0.1 stays 0.1)."""
    return f"{round(value, 6):.6f}".rstrip("0").rstrip(".") if value else "0.0"


def _calibration(title: str, buckets: Sequence[CalibrationBucket]) -> list[str]:
    lines = [title, f"  {'bucket':<14} {'forecasts':>9} {'mean p':>7} {'observed':>8}"]
    for i, b in enumerate(buckets):
        close = "]" if i == len(buckets) - 1 else ")"  # the last bucket includes 1.0
        mean = f"{b.mean_predicted:7.3f}" if b.mean_predicted is not None else f"{'-':>7}"
        obs = f"{b.observed_frequency:8.3f}" if b.observed_frequency is not None else f"{'-':>8}"
        label = f"[{_edge(b.lower)}, {_edge(b.upper)}{close}"
        lines.append(f"  {label:<14} {b.count:>9} {mean} {obs}")
    return lines


def format_report(result: BacktestResult) -> str:
    unscored = result.overall.n_predicted - result.overall.n_scored
    header = (
        f"{'season':<8} {'scored':>6}  {'RPS':>7} {'logloss':>7} {'Brier':>7}  "
        f"{'mkt n':>6}  {'mkt RPS':>7} {'logloss':>7} {'Brier':>7}  "
        f"{'model RPS':>9}  {'no market':>9}"
    )
    lines = [
        f"Walk-forward backtest: model={result.model_name}, "
        f"warm-up {', '.join(_season_label(s) for s in result.warmup_seasons)}, "
        f"{result.n_windows} weekly windows, {result.overall.n_predicted} predictions"
        + (f" ({unscored} without a result, not scored)" if unscored else ""),
        "Model columns score every scored match; market columns the matches with pre-match odds "
        "(B365/BW/AVG consensus); 'model RPS' is the model on those same matches.",
        "",
        header,
        "-" * len(header),
        *(_row(s) for s in result.seasons),
        "-" * len(header),
        _row(result.overall),
        "",
        *_calibration(f"Calibration, model ({result.model_name}):", result.model_calibration),
        "",
        *_calibration("Calibration, market consensus:", result.market_calibration),
    ]
    return "\n".join(lines)
