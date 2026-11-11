"""Ingest of football-data.co.uk CSVs into Supabase (plan step 1.3).

- ``workflow.IngestWorkflow``: per season, fetch then load; then fixtures.csv. Deterministic.
- ``activities``: download + raw copy in Storage (``football_fetch_csv``), parse + upsert
  (``football_load_csv``). All I/O lives here and in ``gateway``.
- ``payload``: pure conversion of parsed records to the RPC's JSON, including the odds cleaning
  rules (prices <= 1.0 stored as missing).
- ``cli``: backfill, one-off runs and the daily schedule.

Kept import-light on purpose: the workflow sandbox imports this package.
"""
