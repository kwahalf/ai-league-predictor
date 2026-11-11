"""Command line: download the season files, run a named model, print the per-season summary.

Run from ``temporal/``::

    python -m src.football.backtest --model trivial

Season files are cached in ``temporal/.cache/football-data/`` (gitignored) and only downloaded
when missing, or when ``--refresh`` is given. Heavy model backtests belong in CI (plan, "Rules
for running agents in parallel"); cheap ones such as ``trivial`` are fine to run locally.
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
import urllib.request
from pathlib import Path
from typing import Callable, Sequence

from ..data import MatchRecord, parse_season_csv
from ..models.dixon_coles import DixonColesModel
from ..models.elo import EloModel
from .harness import DEFAULT_WARMUP_SEASONS, run_backtest
from .model import Model
from .report import format_report
from .trivial import TrivialModel

SEASONS: tuple[str, ...] = (
    "1617", "1718", "1819", "1920", "2021", "2122", "2223", "2324", "2425", "2526",
)  # fmt: skip
"""2016/17 (warm-up) to 2025/26: the 10 completed seasons the backtest runs over."""

SEASON_URL = "https://www.football-data.co.uk/mmz4281/{season}/E0.csv"
DEFAULT_CACHE_DIR = Path(__file__).resolve().parents[3] / ".cache" / "football-data"

MODELS: dict[str, Callable[[], Model]] = {
    "dixon_coles": DixonColesModel,
    "elo": EloModel,
    "trivial": TrivialModel,
}
"""Models the CLI can run, by name. Later steps register theirs here."""


def season_path(cache_dir: Path, season: str) -> Path:
    return cache_dir / f"E0_{season}.csv"


def download_season(season: str, cache_dir: Path, *, refresh: bool = False) -> Path:
    """Fetch one season file into ``cache_dir`` unless it's already there; return its path."""
    path = season_path(cache_dir, season)
    if path.exists() and not refresh:
        return path
    cache_dir.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(
        SEASON_URL.format(season=season), headers={"User-Agent": "ai-league-predictor-backtest"}
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        data = response.read()
    # Write to a temporary file first so an interrupted download never leaves a partial file.
    fd, tmp = tempfile.mkstemp(dir=cache_dir, prefix=path.name, suffix=".part")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return path


def load_seasons(
    seasons: Sequence[str], cache_dir: Path, *, refresh: bool = False
) -> list[MatchRecord]:
    records: list[MatchRecord] = []
    for season in seasons:
        path = download_season(season, cache_dir, refresh=refresh)
        records.extend(parse_season_csv(path.read_bytes(), season))
    return records


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m src.football.backtest",
        description="Walk-forward backtest of a match model over the E0 season files.",
    )
    parser.add_argument("--model", choices=sorted(MODELS), default="trivial")
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE_DIR)
    parser.add_argument("--refresh", action="store_true", help="re-download cached season files")
    args = parser.parse_args(argv)

    records = load_seasons(SEASONS, args.cache_dir, refresh=args.refresh)
    result = run_backtest(records, MODELS[args.model](), warmup_seasons=DEFAULT_WARMUP_SEASONS)
    print(format_report(result))
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
