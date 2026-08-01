# Anomaly detection

`detector.py` fits Prophet and scores each daily slice. `pipeline.py` loads metric series from ClickHouse and writes the full forecast to `metric_baselines`. `scheduler.py` runs the pipeline only for completed event days. `storage.py` preserves the auditable forecast record.
