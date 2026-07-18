from django.core.management.base import BaseCommand
from django.utils import timezone
from datetime import timedelta
from django.core.mail import EmailMultiAlternatives
from django.conf import settings

from subscription.models import CancellationInvoice


class Command(BaseCommand):
    help = "Nudge support at day 15 and day 30 about refunds still owed on cancelled yearly plans."

    def handle(self, *args, **options):
        now = timezone.now()
        d15 = now - timedelta(days=15)
        d30 = now - timedelta(days=30)

        for inv in CancellationInvoice.objects.filter(
            status='pending', reminder_day_15_sent=False,
            created_at__lte=d15, refund_amount__gt=0,
        ):
            self._remind(inv, 15)
            inv.reminder_day_15_sent = True
            inv.save(update_fields=['reminder_day_15_sent'])

        for inv in CancellationInvoice.objects.filter(
            status='pending', reminder_day_30_sent=False,
            created_at__lte=d30, refund_amount__gt=0,
        ):
            self._remind(inv, 30)
            inv.reminder_day_30_sent = True
            inv.save(update_fields=['reminder_day_30_sent'])

    def _remind(self, inv, day):
        support = getattr(settings, 'SUPPORT_EMAIL', settings.EMAIL_HOST_USER)
        if not support:
            return
        kind = "OVERDUE" if day == 30 else "Reminder"
        body = (
            f"{kind}: refund still unpaid.\n\n"
            f"A ₱{inv.refund_amount} refund for cancelling '{inv.business.business_name}' "
            f"(owner {inv.business.user.username}) has been pending for {day} days.\n\n"
            f"Target date was {inv.due_at.strftime('%b %d, %Y')}. "
            f"Issue the refund, then mark invoice {inv.id} as refunded.\n"
        )
        try:
            EmailMultiAlternatives(
                subject=f"[paKITA Admin] {kind} — ₱{inv.refund_amount} refund owed",
                body=body, from_email=settings.EMAIL_HOST_USER, to=[support],
            ).send()
        except Exception:
            self.stderr.write(f"Refund reminder failed for invoice {inv.id}")
