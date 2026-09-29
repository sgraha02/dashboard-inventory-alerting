# Dashboard Inventory & Alerting

Tracks Lakeview dashboard creation across all Databricks workspaces and alerts
by environment tier (Production, Test/CAT, Dev, SIT).

Deployed as a **Declarative Automation Bundle (DAB)**.

## Architecture

```
system.access.audit (all workspaces)
        │
        │  daily MERGE (8:00 AM ET)
        ▼
dashboard_inventory_t (Delta table)
        │
        │  queried by (8:30 AM ET)
        ▼
4 V2 SQL Alerts  ─────────────────►  Email notifications
  Prod_Dashboard_Alert                ("Always" frequency)
  Cat_Dashboard_Alert
  Dev_Dashboard_Alert
  Sit_Dashboard_Alert
```

The daily job also runs an `evaluate_new_dashboards` notebook task that logs
per-environment results in the job output — a reliable record even on days
when V2 alert state-transition logic suppresses re-notification.

## Project Structure

```
dashboard-inventory-alerting/
├── databricks.yml                         # Bundle config & variables
├── resources/
│   ├── initial_setup.job.yml              # One-time: create table → seed → configure alerts
│   ├── dashboard_creation_merge.job.yml   # Daily: audit MERGE → evaluate new dashboards
│   ├── dev_dashboard.alert.yml            # V2 alert — Dev environment
│   ├── sit_dashboard.alert.yml            # V2 alert — SIT environment
│   ├── cat_dashboard.alert.yml            # V2 alert — Test/CAT environment
│   └── prod_dashboard.alert.yml           # V2 alert — Production environment
└── src/
    ├── create_table_ddl.py                # Creates the inventory Delta table (idempotent)
    ├── api_seed.py                        # Seeds via Lakeview API (MVP or cross-workspace SP)
    ├── daily_audit_merge.py               # Incremental MERGE from system.access.audit
    ├── evaluate_new_dashboards.py         # Per-environment new-dashboard check (job task)
    └── configure_alerts.py                # Post-deploy: sets alert notification to "Always"
```

## Jobs

### Initial Setup (`initial_setup`)

Run once after the first `bundle deploy`. Three tasks in sequence:

1. **create_table** — DDL to create the inventory table
2. **api_seed** — Backfills dashboard data via the Lakeview API
3. **configure_alerts** — Patches deployed V2 alerts to set notification
   frequency to "Always" (see [Known Limitation](#known-limitation-v2-alert-notification-frequency))

### Daily Audit MERGE (`daily_audit_merge`)

Runs daily at **8:00 AM ET**. Two tasks:

1. **audit_merge** — MERGEs new/updated dashboards from `system.access.audit`
   into the inventory table. Dynamic lookback from the last `AUDIT_LOG` row's
   `seed_timestamp`. Idempotent — safe to re-run.
2. **evaluate_new_dashboards** — Queries the inventory table per environment
   for dashboards with `seed_timestamp = CURRENT_DATE()`. Exits with
   `TRIGGERED: <summary>` or `OK`.

### V2 Alerts (DAB-managed)

Four V2 alerts run independently at **8:30 AM ET** (30 min after the merge),
each querying the inventory table for new dashboards in its environment:

| Alert | Filter |
|---|---|
| `Prod_Dashboard_Alert` | `workspace_environment = 'Production'` |
| `Cat_Dashboard_Alert` | `workspace_environment = 'Test'` |
| `Dev_Dashboard_Alert` | `workspace_environment = 'Dev'` |
| `Sit_Dashboard_Alert` | `workspace_environment = 'SIT'` |

Alerts use a custom Markdown email template with `@QUERY_RESULT_TABLE` to
show the new dashboards inline.

## Bundle Variables

| Variable | Description | Default |
|---|---|---|
| `catalog` | Unity Catalog catalog | *(required)* |
| `schema` | Target schema | *(required)* |
| `table_name` | Inventory table name | *(required)* |
| `warehouse_id` | SQL warehouse for V2 alert execution | *(required)* |
| `secret_scope` | Secret scope with SP credentials | `""` (empty = MVP mode) |
| `admin_principal` | User/group granted `CAN_MANAGE` on prod | deployer's username |

The `dev` target pre-sets `catalog`, `schema`, `table_name`, and `warehouse_id`
so no `--var` flags are needed for dev deployments.

## Deployment

This bundle is workspace-agnostic — no host is pinned in `databricks.yml`.
Pass a profile (or set `DATABRICKS_HOST`) to choose the target workspace.

### First-time setup

```bash
# 1. Deploy the bundle
databricks bundle deploy -t dev

# 2. Run initial setup (creates table, seeds data, configures alert notifications)
databricks bundle run initial_setup -t dev
```

To seed across all workspaces using a Service Principal:

```bash
databricks bundle run initial_setup -t dev --params secret_scope=<your-scope>
```

The secret scope must contain three keys: `sp-client-id`, `sp-client-secret`,
`sp-tenant-id` (Azure AD app registration credentials for the SP).

### Ongoing

The daily job runs automatically. To deploy updates:

```bash
databricks bundle deploy -t dev

# Re-run configure_alerts after deploy to restore "Always" notification
databricks bundle run initial_setup -t dev
# — or run the notebook directly from the UI
```

### Targets

| Target | Mode | Default Variables |
|---|---|---|
| `dev` | development | `uapdev.sandbox_silver.dashboard_inventory_t`, warehouse `4b6a42a61c9cdd9c` |
| `prod` | production | *(must supply via `--var` or target config)* |

## How New Dashboards Are Detected

The `seed_timestamp` column tracks when a row was **inserted** into the
inventory table:

* **API seed**: all backfilled rows get the run timestamp
* **Daily audit MERGE**: only `NOT MATCHED` (new) rows get `CURRENT_TIMESTAMP()`;
  existing rows keep their original `seed_timestamp`
* **Alert query**: `WHERE CAST(seed_timestamp AS DATE) = CURRENT_DATE()`
  matches only dashboards inserted today

This means the initial seed triggers alerts for the full backfill (expected),
but subsequent days only alert on genuinely new dashboards.

## Known Limitation: V2 Alert Notification Frequency

DABs does not support the `retrigger_seconds` field in the V2 alert schema.
Every `bundle deploy` resets notification frequency to "Once" (notify only on
state transition). The `configure_alerts.py` notebook patches this back to
"Always" (`retrigger_seconds: 1`) by modifying the deployed `.dbalert.json`
workspace files.

**After every deploy**, run the `initial_setup` job or the `configure_alerts`
notebook to restore the "Always" setting.
