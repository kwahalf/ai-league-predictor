"""Data-quality checks run after every ingest (plan step 1.4).

- ``models``: the activity's input and report, plus the activity name. Pure; the workflow imports it.
- ``checks``: the rules and thresholds, and the messages. Pure: judges the facts the database
  returns (``football_quality_check`` RPC, migration ``20261111150000_football_quality.sql``).
- ``activities``: ``football_quality_check``, which fetches the facts, judges them, and fails with a
  non-retryable ``DataQualityError`` naming the offending rows if any check fails.

Kept import-light on purpose: the workflow sandbox imports this package.
"""
