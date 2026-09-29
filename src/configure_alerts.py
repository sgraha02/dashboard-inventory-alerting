# Databricks notebook source
# /// script
# [tool.databricks.environment]
# environment_version = "6"
# ///
# DBTITLE 1,Cell 1
# MAGIC %md
# MAGIC # Dashboard Inventory — Configure Alert Notifications
# MAGIC Post-deploy step: patches DABs-deployed V2 alerts to set notification
# MAGIC frequency to **Always** (`seconds_to_retrigger: 1`).
# MAGIC
# MAGIC DABs does not support this field in the alert schema, so it resets to
# MAGIC "Once" on every deploy. Run this after each `bundle deploy`.

# COMMAND ----------

# DBTITLE 1,Cell 2
dbutils.widgets.text("bundle_name", "dashboard_inventory_alerting", "Bundle Name")
dbutils.widgets.text("target", "dev", "Bundle Target")

BUNDLE_NAME = dbutils.widgets.get("bundle_name")
TARGET = dbutils.widgets.get("target")
print(f"Bundle: {BUNDLE_NAME}, Target: {TARGET}")

# COMMAND ----------

# DBTITLE 1,Cell 3
from databricks.sdk import WorkspaceClient
import requests, json, base64

w = WorkspaceClient()
host = w.config.host.rstrip("/")
headers = {**dict(w.config.authenticate()), "Content-Type": "application/json"}
username = w.current_user.me().user_name

# Locate deployed alert files in the bundle's resource directory
resources_path = f"/Users/{username}/.bundle/{BUNDLE_NAME}/{TARGET}/resources"

resp = requests.get(
    f"{host}/api/2.0/workspace/list",
    headers=headers,
    params={"path": resources_path},
    timeout=30,
)
resp.raise_for_status()

alert_files = [
    obj for obj in resp.json().get("objects", [])
    if obj.get("path", "").endswith(".dbalert.json")
]
print(f"Found {len(alert_files)} alert(s) in {resources_path}")

patched = 0
already_set = 0

for obj in alert_files:
    path = obj["path"]
    name = path.rsplit("/", 1)[-1]

    # Export current .dbalert.json
    r = requests.get(
        f"{host}/api/2.0/workspace/export",
        headers=headers,
        params={"path": path, "format": "AUTO"},
        timeout=30,
    )
    if not r.ok:
        print(f"  \u2718 {name}: export failed ({r.status_code})")
        continue

    content = base64.b64decode(r.json()["content"]).decode()
    data = json.loads(content)

    # Check if already configured
    # NOTE: .dbalert.json uses "retrigger_seconds", NOT "seconds_to_retrigger"
    notification = data.setdefault("evaluation", {}).setdefault("notification", {})
    current = notification.get("retrigger_seconds")
    if current == 1:
        print(f"  \u2713 {name}: already set to Always")
        already_set += 1
        continue

    # Patch retrigger_seconds (and remove wrong field name if present)
    notification["retrigger_seconds"] = 1
    notification.pop("seconds_to_retrigger", None)

    # Re-import
    updated = base64.b64encode(json.dumps(data).encode()).decode()
    r2 = requests.post(
        f"{host}/api/2.0/workspace/import",
        headers=headers,
        json={
            "path": path,
            "content": updated,
            "format": "AUTO",
            "overwrite": True,
            "language": "PYTHON",
        },
        timeout=30,
    )
    if r2.ok:
        print(f"  \u2713 {name}: patched to Always")
        patched += 1
    else:
        print(f"  \u2718 {name}: import failed ({r2.status_code})")

print(f"\nDone: {patched} patched, {already_set} already set, "
      f"{len(alert_files) - patched - already_set} failed")
dbutils.notebook.exit(f"OK: {patched} patched, {already_set} already set")