# Databricks notebook source
# MAGIC %md
# MAGIC # Dashboard Inventory — Daily Audit MERGE
# MAGIC Picks up new/updated dashboards from `system.access.audit` since the last run.
# MAGIC Idempotent — safe to re-run.

# COMMAND ----------

# Read the target table from job parameter or widget
dbutils.widgets.text("target_table", "uapdev.sandbox_silver.dashboard_inventory_t", "Target Table")
TARGET_TABLE = dbutils.widgets.get("target_table")
print(f"Target table: {TARGET_TABLE}")

# COMMAND ----------

# MAGIC %sql
# MAGIC -- Dynamic lookback: queries events since the last audit MERGE inserted
# MAGIC -- a row. Falls back to full history (2025-09-05) on the first run.
# MAGIC
# MAGIC WITH ws_env AS (
# MAGIC   SELECT
# MAGIC     CAST(workspace_id AS BIGINT) AS workspace_id,
# MAGIC     workspace_name,
# MAGIC     CASE
# MAGIC       WHEN LOWER(workspace_name) RLIKE '(^|-)prod(-|$)' THEN 'Production'
# MAGIC       WHEN LOWER(workspace_name) RLIKE '(^|-)(cat|test)(-|$)' THEN 'Test'
# MAGIC       WHEN LOWER(workspace_name) RLIKE '(^|-)sit(-|$)' OR LOWER(workspace_name) LIKE '%devsit%' THEN 'SIT'
# MAGIC       WHEN LOWER(workspace_name) RLIKE '(^|-)dev(-|$)' THEN 'Dev'
# MAGIC       ELSE 'Unclassified'
# MAGIC     END AS workspace_environment,
# MAGIC     CASE
# MAGIC       WHEN LOWER(workspace_name) RLIKE '(^|-)sit(-|$)' OR LOWER(workspace_name) LIKE '%devsit%' THEN true
# MAGIC       ELSE false
# MAGIC     END AS is_deprecated
# MAGIC   FROM system.access.workspaces_latest
# MAGIC   WHERE status = 'RUNNING'
# MAGIC ),
# MAGIC
# MAGIC audit_raw AS (
# MAGIC   SELECT
# MAGIC     request_params['dashboard_id'] AS dashboard_id,
# MAGIC     user_identity.email            AS user_email,
# MAGIC     CAST(workspace_id AS BIGINT)   AS workspace_id,
# MAGIC     action_name,
# MAGIC     event_time
# MAGIC   FROM system.access.audit
# MAGIC   WHERE action_name IN ('createDashboard', 'updateDashboard', 'trashDashboard')
# MAGIC     AND request_params['dashboard_id'] IS NOT NULL
# MAGIC     AND event_time > COALESCE(
# MAGIC       (SELECT MAX(seed_timestamp)
# MAGIC        FROM IDENTIFIER(:target_table)
# MAGIC        WHERE source = 'AUDIT_LOG'),
# MAGIC       TIMESTAMP '2025-09-05'
# MAGIC     )
# MAGIC ),
# MAGIC
# MAGIC dashboard_agg AS (
# MAGIC   SELECT
# MAGIC     dashboard_id,
# MAGIC     MIN(CASE WHEN action_name = 'createDashboard' THEN user_email END) AS creator_user_name,
# MAGIC     MIN(CASE WHEN action_name = 'createDashboard' THEN event_time END) AS create_time,
# MAGIC     MAX(event_time)                   AS update_time,
# MAGIC     MAX_BY(action_name, event_time)   AS last_action,
# MAGIC     MIN_BY(workspace_id, event_time)  AS workspace_id
# MAGIC   FROM audit_raw
# MAGIC   GROUP BY dashboard_id
# MAGIC ),
# MAGIC
# MAGIC audit_enriched AS (
# MAGIC   SELECT
# MAGIC     d.dashboard_id,
# MAGIC     d.creator_user_name,
# MAGIC     d.create_time,
# MAGIC     d.update_time,
# MAGIC     CAST(NULL AS STRING)  AS parent_path,
# MAGIC     CASE WHEN d.last_action = 'trashDashboard' THEN 'TRASHED' ELSE 'ACTIVE' END AS lifecycle_status,
# MAGIC     w.workspace_name,
# MAGIC     d.workspace_id,
# MAGIC     w.workspace_environment,
# MAGIC     w.is_deprecated,
# MAGIC     CAST(CURRENT_DATE() AS DATE) AS first_seen_date,
# MAGIC     'AUDIT_LOG'           AS source,
# MAGIC     CASE WHEN d.create_time IS NOT NULL THEN CURRENT_TIMESTAMP() ELSE NULL END AS seed_timestamp
# MAGIC   FROM dashboard_agg d
# MAGIC   LEFT JOIN ws_env w ON d.workspace_id = w.workspace_id
# MAGIC )
# MAGIC
# MAGIC MERGE INTO IDENTIFIER(:target_table) AS tgt
# MAGIC USING audit_enriched AS src
# MAGIC ON tgt.dashboard_id = src.dashboard_id
# MAGIC
# MAGIC WHEN MATCHED THEN UPDATE SET
# MAGIC   tgt.update_time           = GREATEST(tgt.update_time, src.update_time),
# MAGIC   tgt.lifecycle_status      = src.lifecycle_status,
# MAGIC   tgt.parent_path           = COALESCE(tgt.parent_path, src.parent_path),
# MAGIC   tgt.creator_user_name     = COALESCE(tgt.creator_user_name, src.creator_user_name),
# MAGIC   tgt.workspace_name        = COALESCE(tgt.workspace_name, src.workspace_name),
# MAGIC   tgt.workspace_id          = COALESCE(tgt.workspace_id, src.workspace_id),
# MAGIC   tgt.workspace_environment = COALESCE(tgt.workspace_environment, src.workspace_environment),
# MAGIC   tgt.is_deprecated         = COALESCE(tgt.is_deprecated, src.is_deprecated)
# MAGIC
# MAGIC WHEN NOT MATCHED AND src.create_time IS NOT NULL THEN INSERT *;
