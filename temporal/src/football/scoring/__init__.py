"""Scoring of forecasts against results, stored in Supabase (plan step 2.1b).

- ``market``: pure scoring of the bookmakers' margin-free 1X2 (each source, and the consensus of
  the model-input sources) with the 2.1a metrics.
- ``activities``: ``football_score_market`` reads one season, scores it and writes
  ``market_scores`` through the ``football_upsert_market_scores`` RPC (idempotent).
- ``workflow.MarketScoreWorkflow``: runs that activity per season. Deterministic.
- ``cli``: ``python -m src.football.scoring.cli market`` for the backfill.

Kept import-light on purpose: the workflow sandbox imports this package.
"""
