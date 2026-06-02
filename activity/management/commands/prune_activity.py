from django.core.management.base import BaseCommand
from activity.models import ActivityEvent


class Command(BaseCommand):
    help = "Delete ActivityEvent rows older than N days (default 7)."

    def add_arguments(self, parser):
        parser.add_argument(
            '--days',
            type=int,
            default=7,
            help='Retention window in days. Events older than this are deleted.',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show how many events would be deleted without actually deleting.',
        )

    def handle(self, *args, **options):
        days = options['days']
        dry_run = options['dry_run']

        if dry_run:
            from django.utils import timezone
            from datetime import timedelta
            cutoff = timezone.now() - timedelta(days=days)
            count = ActivityEvent.objects.filter(created_at__lt=cutoff).count()
            self.stdout.write(
                self.style.WARNING(f"[dry-run] Would delete {count} event(s) older than {days} days.")
            )
            return

        deleted, _ = ActivityEvent.prune_old(days=days)
        self.stdout.write(
            self.style.SUCCESS(f"Pruned {deleted} ActivityEvent row(s) older than {days} days.")
        )
