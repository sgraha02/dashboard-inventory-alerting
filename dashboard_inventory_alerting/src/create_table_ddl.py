# Databricks notebook source
# MAGIC %md
# MAGIC # Dashboard Inventory — Table DDL
# MAGIC Creates the `dashboard_inventory_t` Delta table (idempotent).

# COMMAND ----------

dbutils.widgets.text("target_table", "uapdev.sandbox_silver.dashboard_inventory_t", "Target Table")
TARGET_TABLE = dbutils.widgets.get("target_table")

spark.sql(f"""
CREATE TABLE IF NOT EXISTS {TARGET_TABLE} (
  dashboard_id          STRING        NOT NULL  COMMENT 'Lakeview dashboard UUID (primary key)',
  creator_user_name     STRING                  COMMENT 'Email/UPN of the original creator',
  create_time           TIMESTAMP               COMMENT 'Dashboard creation timestamp (UTC)',
  update_time           TIMESTAMP               COMMENT 'Last modification timestamp (UTC)',
  parent_path           STRING                  COMMENT 'Workspace folder path containing the dashboard',
  lifecycle_status      STRING                  COMMENT 'ACTIVE or TRASHED',
  workspace_name        STRING                  COMMENT 'Databricks workspace display name',
  workspace_id          BIGINT                  COMMENT 'Databricks workspace numeric ID',
  workspace_environment STRING                  COMMENT 'Environment tier: Production, Test, Dev, SIT',
  is_deprecated         BOOLEAN                 COMMENT 'True for SIT workspaces slated for decommission',
  first_seen_date       DATE                    COMMENT 'Date the dashboard first appeared in inventory',
  source                STRING                  COMMENT 'How the row was created: API_SEED or AUDIT_LOG',
  seed_timestamp        TIMESTAMP               COMMENT 'Timestamp of the seed/sync run that wrote this row'
)
USING DELTA
COMMENT 'Cross-workspace Lakeview dashboard inventory. Seeded via API, maintained via audit MERGE.'
TBLPROPERTIES (
  'delta.enableChangeDataFeed' = 'true'
)
""")

print(f"\u2705 Table {TARGET_TABLE} is ready.")
