# Alerts

`anomaly_alert.sql` is the query to paste into a HyperDX scheduled alert. It keeps only the latest model run for each series, requires at least 5,000 daily requests, and requires a revenue anomaly to represent at least 0.5% of global daily revenue.

Test it locally:

```powershell
python alerts/check_alerts.py --day 2026-07-05
```
