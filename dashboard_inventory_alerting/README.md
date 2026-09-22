# Dashboard Inventory & Alerting

Tracks Lakeview dashboard creation across all Databricks workspaces and alerts by environment tier (PROD, TEST, DEV, SIT).

## Architecture

```
system.access.audit (all workspaces)
        │ daily MERGE (scheduled job)
        ▼
dashboard_inventory_t (Delta table)
        │ queried by
        ▼
4 SQL Alerts: [PROD] [TEST] [DEV] [SIT-DEPRECATED]
```

## Project Structure

* `src/create_table_ddl.py` — Creates the inventory Delta table (idempotent)
* `src/api_seed.py` — Seeds the table via the Lakeview API (MVP single-workspace or full cross-workspace with SP)
* `src/daily_audit_merge.py` — Daily incremental MERGE from `system.access.audit`
* `src/create_alerts.py` — Programmatically creates the 4 environment-tier SQL Alerts
* `resources/sample_job.job.yml` — Job definitions (daily audit MERGE + one-time initial setup)

## Bundle Variables

| Variable | Description | Default |
|---|---|---|
| `catalog` | Unity Catalog catalog | (required) |
| `schema` | Target schema | (required) |
| `table_name` | Inventory table name | `dashboard_inventory_t` |
| `secret_scope` | Secret scope with SP credentials | (empty = MVP mode) |

## Deployment

### First-time setup

```bash
# Deploy the bundle (dev target)
databricks bundle deploy --target dev

# Run the initial setup job (creates table, seeds data, creates alerts)
databricks bundle run initial_setup --target dev
```

### Ongoing

The `daily_audit_merge` job runs automatically at 8:00 AM ET. To deploy updates:

```bash
databricks bundle deploy --target prod
```

### Targets

| Target | Mode | Description |
|---|---|---|
| `dev` | development | Schedules paused by default |
| `prod` | production | Live schedules, restricted permissions |

Set `catalog` and `schema` per target or at deploy time:

```bash
databricks bundle deploy --target dev --var catalog=uapdev --var schema=sandbox_silver
```

Or add them permanently to a target in `databricks.yml`:

```yaml
targets:
  dev:
    variables:
      catalog: uapdev
      schema: sandbox_silver
```
