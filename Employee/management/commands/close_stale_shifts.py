"""Auto-close shifts a staff member forgot to clock out of.

Closes every open shift whose cutoff has passed — the business's `closing_time`, or
24 hours after clock-in for a business with no closing time set (open 24 hours). The
logic lives in Employee.utils.close_stale_shifts so the lazy sweep on clock-in/timecard
load uses the exact same rule; this command is the scheduled path.

Idempotent: it only touches shifts with clock_in set and clock_out NULL, so running it
twice does nothing the second time. Safe to run as often as you like.

Schedule it nightly. On Railway, add a service whose start command is:

    python manage.py close_stale_shifts

⚠ Railway cron runs in UTC. The Philippines is UTC+8, so midnight in Manila is 16:00
UTC the previous day. To fire ~12:15 AM Manila, use the cron expression:  15 16 * * *
(Naively using `0 0 * * *` would run at 8 AM Manila.)
"""
from django.core.management.base import BaseCommand

from Employee.utils import close_stale_shifts


class Command(BaseCommand):
    help = "Auto-close shifts left open past their business's closing time (24h cap when open 24 hours)."

    def handle(self, *args, **options):
        count = close_stale_shifts()
        self.stdout.write(self.style.SUCCESS(f"Closed {count} stale shift(s)."))
