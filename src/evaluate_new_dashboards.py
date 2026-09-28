# Databricks notebook source
# DBTITLE 1,Header
# MAGIC %md
# MAGIC # Evaluate New Dashboards
# MAGIC Runs after the daily audit merge to check for new dashboards per environment tier.
# MAGIC Notifies when new dashboards are found — fires every day regardless of prior state.

# COMMAND ----------

# DBTITLE 1,Parameters
dbutils.widgets.text("target_table", "", "Target Table")
TARGET_TABLE = dbutils.widgets.get("target_table")

# COMMAND ----------

# DBTITLE 1,Evaluate environments and report
ENVIRONMENTS = [
    {"name": "Sit_Dashboard_Alert",  "filter": "SIT",        "prefix": "[SIT-DEPRECATED]",
     "body": "\u26a0\ufe0f DEPRECATION NOTICE: This workspace is slated for DECOMMISSION. "
             "New dashboards should NOT be created here."},
    {"name": "Cat_Dashboard_Alert",  "filter": "Test",       "prefix": "[TEST]",
     "body": "FOR AWARENESS: Test environment activity."},
    {"name": "Dev_Dashboard_Alert",  "filter": "Dev",        "prefix": "[DEV]",
     "body": "FOR AWARENESS: Development activity."},
    {"name": "Prod_Dashboard_Alert", "filter": "Production", "prefix": "[PROD]",
     "body": "ACTION REQUIRED: Review for compliance with dashboard guidelines "
             "(naming, data sources, permissions)."},
]

triggered = []

for env in ENVIRONMENTS:
    count = spark.sql(f"""
        SELECT COUNT(*) AS new_dashboards
        FROM {TARGET_TABLE}
        WHERE CAST(seed_timestamp AS DATE) = CURRENT_DATE()
          AND workspace_environment = '{env["filter"]}'
          AND lifecycle_status = 'ACTIVE'
    """).first()["new_dashboards"]

    if count > 0:
        triggered.append({"env": env, "count": count})
        print(f"\U0001f514 {env['prefix']} {count} new dashboard(s) in {env['filter']}")
        print(f"   {env['body']}")
    else:
        print(f"\u2705 No new dashboards in {env['filter']}")

if not triggered:
    print("\n\u2705 All clear \u2014 no new dashboards today.")
    dbutils.notebook.exit("OK")
else:
    summary = ", ".join(f"{t['env']['filter']}={t['count']}" for t in triggered)
    print(f"\n\U0001f514 New dashboards found: {summary}")
    dbutils.notebook.exit(f"TRIGGERED: {summary}")