from django.core.management.base import BaseCommand
from django.utils import timezone
from datetime import timedelta
from django.core.mail import EmailMultiAlternatives
from django.conf import settings

from subscription.models import CancellationInvoice


class Command(BaseCommand):
    help = "Send day-15 and day-30 reminders for unsettled cancellation balances."

    def handle(self, *args, **options):
        now = timezone.now()
        d15 = now - timedelta(days=15)
        d30 = now - timedelta(days=30)

        for inv in CancellationInvoice.objects.filter(
            status='pending', reminder_day_15_sent=False,
            created_at__lte=d15, amount_due__gt=0,
        ):
            self._remind(inv, 15)
            inv.reminder_day_15_sent = True
            inv.save(update_fields=['reminder_day_15_sent'])

        for inv in CancellationInvoice.objects.filter(
            status='pending', reminder_day_30_sent=False,
            created_at__lte=d30, amount_due__gt=0,
        ):
            self._remind(inv, 30)
            inv.status = 'overdue'
            inv.reminder_day_30_sent = True
            inv.save(update_fields=['status', 'reminder_day_30_sent'])

    def _remind(self, inv, day):
        owner = inv.business.user
        if not owner.email:
            return
        kind = "Final reminder" if day == 30 else "Friendly reminder"
        body = (
            f"Hi {owner.username},\n\n"
            f"{kind}: there's still an open balance of ₱{inv.amount_due} "
            f"from cancelling '{inv.business.business_name}'.\n\n"
            f"It was due {inv.due_at.strftime('%b %d, %Y')}. "
            f"Whenever you're ready, you can settle it — no rush, no extra fees.\n\n"
            f"Questions? Just reply.\n\n— Swift ERP"
        )
        try:
            EmailMultiAlternatives(
                subject=f"[Swift ERP] {kind} — ₱{inv.amount_due} balance",
                body=body, from_email=settings.EMAIL_HOST_USER, to=[owner.email],
            ).send()
        except Exception:
            self.stderr.write(f"Reminder failed for invoice {inv.id}")
