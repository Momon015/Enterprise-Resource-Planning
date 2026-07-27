# Scheduled jobs

paKITA has background work that needs to run on a schedule. The logic always lives in a
Django **management command** so it's decoupled from *how* it's triggered — the same
command works from a scheduler, a manual run, or a test.

| Command | What it does | Suggested schedule |
|---|---|---|
| `close_stale_shifts` | Auto-closes shifts a staff member forgot to clock out of (at the business's `closing_time`, or 24h after clock-in for a 24-hour business). Idempotent. | Nightly, just after midnight |

There is also a **lazy safety net**: `close_stale_shifts` runs on every clock-in and every
Timecards page load (scoped to that one business), so a forgotten shift self-heals the next
time anyone interacts even if the scheduled run is missed. The nightly job just makes it
prompt and business-wide.

## Running manually

```
python manage.py close_stale_shifts
```

Safe to run any time — it only ever touches shifts that are still open (clock-out unset).

## Scheduling on Railway

Railway has cron built in — nothing to install (no Celery, no cron server).

1. Add a **service** pointing at this same repo.
2. Set its **start command** to `python manage.py close_stale_shifts`.
3. Give it a **Cron Schedule** in the service settings.

Put the cron on a *separate* service, not the web service — the web service runs gunicorn
forever, whereas a cron service is expected to run once and **exit**, which a management
command does.

### ⚠ Railway cron runs in UTC — adjust for Manila (UTC+8)

Midnight in the Philippines is **16:00 UTC the previous day**. So to fire at ~12:15 AM
Manila, the cron expression is:

```
15 16 * * *
```

Using `0 0 * * *` (midnight UTC) would run at **8:00 AM Manila** — mid-morning, wrong.

| Manila time | UTC cron expression |
|---|---|
| 12:00 AM | `0 16 * * *` |
| 12:15 AM | `15 16 * * *` |
| 1:00 AM  | `0 17 * * *` |
| 3:00 AM  | `0 19 * * *` |

(If you ever set Railway's service timezone to `Asia/Manila`, use the Manila time directly
instead — but the default is UTC.)
