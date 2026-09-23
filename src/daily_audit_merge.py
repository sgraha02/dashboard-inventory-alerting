"""Dashboard Inventory — Daily Audit MERGE.

Picks up new/updated dashboards from `system.access.audit` since the last run.
Idempotent — safe to re-run.
"""

from pyspark.sql import SparkSession

spark = SparkSession.builder.getOrCreate()

# Read the target table from job parameter or widget
dbutils.widgets.text("target_table", "uapdev.sandbox_silver.dashboard_inventory_t", "Target Table")
TARGET_TABLE = dbutils.widgets.get("target_table")
print(f"Target table: {TARGET_TABLE}")

# ---------------------------------------------------------------------------
# Dynamic lookback: queries events since the last audit MERGE inserted
# a row. Falls back to full history (2025-09-05) on the first run.
# ---------------------------------------------------------------------------

MERGE_SQL = """
WITH ws_env AS (
  SELECT
    CAST(workspace_id AS BIGINT) AS workspace_id,
    workspace_name,
    CASE
      WHEN LOWER(workspace_name) RLIKE '(^|-)prod(-|$)' THEN 'Production'
      WHEN LOWER(workspace_name) RLIKE '(^|-)(cat|test)(-|$)' THEN 'Test'
      WHEN LOWER(workspace_name) RLIKE '(^|-)sit(-|$)' OR LOWER(workspace_name) LIKE '%devsit%' THEN 'SIT'
      WHEN LOWER(workspace_name) RLIKE '(^|-)dev(-|$)' THEN 'Dev'
      ELSE 'Unclassified'
    END AS workspace_environment,
    CASE
      WHEN LOWER(workspace_name) RLIKE '(^|-)sit(-|$)' OR LOWER(workspace_name) LIKE '%devsit%' THEN true
      ELSE false
    END AS is_deprecated
  FROM system.access.workspaces_latest
  WHERE status = 'RUNNING'
),

audit_raw AS (
  SELECT
    request_params['dashboard_id'] AS dashboard_id,
    user_identity.email            AS user_email,
    CAST(workspace_id AS BIGINT)   AS workspace_id,
    action_name,
    event_time
  FROM system.access.audit
  WHERE action_name IN ('createDashboard', 'updateDashboard', 'trashDashboard')
    AND request_params['dashboard_id'] IS NOT NULL
    AND event_time > COALESCE(
      (SELECT MAX(seed_timestamp)
       FROM IDENTIFIER(:target_table)
       WHERE source = 'AUDIT_LOG'),
      TIMESTAMP '2025-09-05'
    )
),

dashboard_agg AS (
  SELECT
    dashboard_id,
    MIN(CASE WHEN action_name = 'createDashboard' THEN user_email END) AS creator_user_name,
    MIN(CASE WHEN action_name = 'createDashboard' THEN event_time END) AS create_time,
    MAX(event_time)                   AS update_time,
    MAX_BY(action_name, event_time)   AS last_action,
    MIN_BY(workspace_id, event_time)  AS workspace_id
  FROM audit_raw
  GROUP BY dashboard_id
),

audit_enriched AS (
  SELECT
    d.dashboard_id,
    d.creator_user_name,
    d.create_time,
    d.update_time,
    CAST(NULL AS STRING)  AS parent_path,
    CASE WHEN d.last_action = 'trashDashboard' THEN 'TRASHED' ELSE 'ACTIVE' END AS lifecycle_status,
    w.workspace_name,
    d.workspace_id,
    w.workspace_environment,
    w.is_deprecated,
    CAST(CURRENT_DATE() AS DATE) AS first_seen_date,
    'AUDIT_LOG'           AS source,
    CASE WHEN d.create_time IS NOT NULL THEN CURRENT_TIMESTAMP() ELSE NULL END AS seed_timestamp
  FROM dashboard_agg d
  LEFT JOIN ws_env w ON d.workspace_id = w.workspace_id
)

MERGE INTO IDENTIFIER(:target_table) AS tgt
USING audit_enriched AS src
ON tgt.dashboard_id = src.dashboard_id

WHEN MATCHED THEN UPDATE SET
  tgt.update_time           = GREATEST(tgt.update_time, src.update_time),
  tgt.lifecycle_status      = src.lifecycle_status,
  tgt.parent_path           = COALESCE(tgt.parent_path, src.parent_path),
  tgt.creator_user_name     = COALESCE(tgt.creator_user_name, src.creator_user_name),
  tgt.workspace_name        = COALESCE(tgt.workspace_name, src.workspace_name),
  tgt.workspace_id          = COALESCE(tgt.workspace_id, src.workspace_id),
  tgt.workspace_environment = COALESCE(tgt.workspace_environment, src.workspace_environment),
  tgt.is_deprecated         = COALESCE(tgt.is_deprecated, src.is_deprecated)

WHEN NOT MATCHED AND src.create_time IS NOT NULL THEN INSERT *
"""

result = spark.sql(MERGE_SQL, args={"target_table": TARGET_TABLE})
result.show()
