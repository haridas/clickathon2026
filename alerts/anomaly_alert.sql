/* HyperDX / ClickHouse alert candidates. Bind target as a ClickHouse Date. */
WITH latest_runs AS (
    SELECT day, metric, dim_name, dim_value, argMax(model_run_id, fitted_at) AS model_run_id
    FROM metric_baselines
    GROUP BY day, metric, dim_name, dim_value
),
global_revenue AS (
    SELECT b.y AS value
    FROM metric_baselines AS b
    INNER JOIN latest_runs AS r
        ON b.day = r.day AND b.metric = r.metric AND b.dim_name = r.dim_name
        AND b.dim_value = r.dim_value AND b.model_run_id = r.model_run_id
    WHERE b.day = {target:Date} AND b.metric = 'revenue'
      AND b.dim_name = 'global' AND b.dim_value = 'all'
    LIMIT 1
)
SELECT
    b.day, b.metric, b.dim_name, b.dim_value, b.y AS actual, b.yhat AS expected,
    b.yhat_lower, b.yhat_upper, b.residual, b.z, m.requests,
    if(abs(b.z) >= 3, 'high', 'medium') AS severity,
    if(b.residual > 0, 'spike', 'drop') AS direction
FROM metric_baselines AS b
INNER JOIN latest_runs AS r
    ON b.day = r.day AND b.metric = r.metric AND b.dim_name = r.dim_name
    AND b.dim_value = r.dim_value AND b.model_run_id = r.model_run_id
INNER JOIN metrics_daily AS m
    ON b.day = m.day AND b.dim_name = m.dim_name AND b.dim_value = m.dim_value
CROSS JOIN global_revenue AS g
WHERE b.day = {target:Date}
  AND b.is_anomaly = 1
  AND m.requests >= 5000
  AND (b.metric != 'revenue' OR abs(b.residual) >= g.value * 0.005)
ORDER BY abs(b.z) DESC
LIMIT 10;
