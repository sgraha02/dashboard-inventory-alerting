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
| `secret_scope` | Secret scope with SP credentials for API seeding | `""` (empty = MVP mode) |
| `run_as_service_principal` | Application ID of SP for job and alert `run_as` (prod) | `""` (deployer identity) |
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

To deploy prod with a `run_as` service principal:

```bash
databricks bundle deploy -t prod \
  --var="catalog=my_catalog,schema=my_schema,table_name=dashboard_inventory_t" \
  --var="warehouse_id=<id>" \
  --var="run_as_service_principal=<application-id>"
```

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

## Service Principal Roles

This bundle uses **two distinct service principal identities** that serve
different purposes. They can be the same SP or different SPs depending on your
security model.

### 1. Run-As SP (`run_as_service_principal`)

**Purpose:** Determines the execution identity for all jobs and alerts
(table reads/writes, warehouse access, Unity Catalog permissions).

* Set via the `run_as_service_principal` bundle variable.
* **Jobs:** The prod target's `run_as` block passes this application ID so
  that jobs execute as the SP regardless of who deploys.
* **Alerts:** Each alert YAML includes a `run_as` block using the same
  variable, so alerts also execute their queries as the SP.
* In dev mode this is left empty — jobs and alerts run as the deployer.

**Permissions required:**

* `USE CATALOG` / `USE SCHEMA` on the target catalog and schema
* `SELECT` and `MODIFY` on the inventory table
* `SELECT` on `system.access.audit` and `system.access.workspaces_latest`
* Access to the SQL warehouse specified by `warehouse_id`

### 2. API-Seeding SP (`secret_scope`)

**Purpose:** Authenticates REST API calls to the Lakeview API across multiple
workspaces. Used **only** by the `api_seed` notebook at runtime — it does not
affect job ownership or Unity Catalog permissions.

The `api_seed` notebook retrieves the following keys from the secret scope at
runtime and creates an OAuth token to call each workspace's Lakeview API:

| Secret Key | Description |
|---|---|
| `sp-client-id` | Azure AD application (client) ID — **identifies which SP** authenticates to the Lakeview API |
| `sp-client-secret` | Azure AD client secret (credential) for the SP |
| `sp-tenant-id` | Azure AD tenant ID where the SP is registered |

**The `sp-client-id` is the critical key** — it determines which service
principal identity is used for cross-workspace API calls. This SP must be
registered in each target workspace and granted permission to list Lakeview
dashboards.

**Permissions required (per workspace):**

* The SP identified by `sp-client-id` must be added to each workspace
* The SP must have access to the Lakeview dashboards API
  (`GET /api/2.0/lakeview/dashboards`)

### How They Work Together

```
bundle deploy -t prod --var="run_as_service_principal=<app-id-A>"
│
├─► Jobs run as SP-A (run_as)           ◄── owns table writes, reads audit logs
│     │
│     └─► api_seed notebook
│           │
│           └─► reads secret_scope
│                 sp-client-id = <app-id-B>   ◄── authenticates to Lakeview API
│                 sp-client-secret = ...
│                 sp-tenant-id = ...
│
└─► Alerts run as SP-A (run_as)         ◄── executes alert queries as the SP
```

SP-A and SP-B can be the same service principal if it has both the Unity
Catalog/warehouse permissions and Lakeview API access across all workspaces.

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
