from django.db import models
from django.utils.text import slugify

from core.models import TimeStampModel, SlugModel, Category, StatusModel
from Supplier.models import Material

from django.utils import timezone

from django.db.models import Sum, Avg, Value

from django.db.models.functions import Coalesce
from decimal import Decimal

from user.models import User, BusinessProfile

from Product.models import Product
from django.db.models import F

from core.utils.owner import get_owner

from Supplier.models import Material, STATUS_CHOICES

# Create your models here.

class EmployeeQuerySet(models.QuerySet):
    def total_daily_rate(self):
        return self.aggregate(total_daily_rate=Sum('daily_rate'))['total_daily_rate'] or 0
    
    def average_daily_rate(self):
        return self.aggregate(average_daily_rate=Avg('daily_rate'))['average_daily_rate'] or 0
    

class EmployeeManager(models.Manager.from_queryset(EmployeeQuerySet)):
    """Default manager hides archived employees (status='inactive') but keeps the helpers."""
    def get_queryset(self):
        return super().get_queryset().exclude(status='inactive')
    
class Employee(TimeStampModel, SlugModel):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='employees')
    staff_user = models.ForeignKey(User, on_delete=models.SET_NULL, related_name='employee_profile', null=True, blank=True)
    business = models.ForeignKey(BusinessProfile, on_delete=models.SET_NULL, related_name='employees', null=True, blank=True)
    name = models.CharField(max_length=255)
    daily_rate = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    is_locked = models.BooleanField(default=False, db_index=True)
    locked_at = models.DateTimeField(null=True, blank=True)
    default_opening_cash = models.DecimalField(
        max_digits=10, decimal_places=2, 
        null=True, blank=True,
        help_text="Override business default for this employee. Leave blank to use business default."
    )
    
    is_cashier = models.BooleanField(
        default=False,
        help_text="Handles a cash drawer (gets the starting-cash + cash-count flow). Off = attendance & payroll only.",
    )

    status = models.CharField(max_length=20, choices=STATUS_CHOICES, db_index=True, default='active')
    
    objects = EmployeeManager()                  # active only + helpers  (was EmployeeQuerySet.as_manager())
    all_objects = EmployeeQuerySet.as_manager()  # everything incl. archived + helpers
    
    class Meta:
        unique_together = ('user', 'business', 'slug')
    
    def __str__(self):
        return f"{self.staff_user} "
    
    def save(self, *args, **kwargs):
        base_slug = slugify(self.name)  # or whatever name field
        slug = base_slug
        counter = 1
        
        # include business in collision check
        while Employee.objects.filter(user=self.user, business=self.business, slug=slug).exclude(id=self.id).exists():
            slug = f"{base_slug}-{counter}"
            counter += 1
        
        self.slug = slug
        
        super().save(*args, **kwargs)
    
class Shift(TimeStampModel):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='shift_logs')
    business = models.ForeignKey(BusinessProfile, on_delete=models.SET_NULL, related_name='shifts', null=True, blank=True)
    date = models.DateField(db_index=True)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='created_shift_logs')
    
    def __str__(self):
        return f"{self.id} - {self.amount} — {self.date}"
    
    def save(self, *args, **kwargs):
        if not self.date:
            self.date = timezone.localdate()
        super().save(*args, **kwargs)


class ShiftEmployee(TimeStampModel):
    shift = models.ForeignKey(Shift, on_delete=models.CASCADE, related_name='shift_employees')
    employee = models.ForeignKey(Employee, on_delete=models.SET_NULL, related_name='shift_employees', null=True, blank=True)
    name = models.CharField(max_length=255) # snapshot
    daily_rate = models.DecimalField(max_digits=10, decimal_places=2)
    is_cashier = models.BooleanField(default=False) # snapshot
    drawer_session = models.ForeignKey(
        'DrawerSession', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='shift_employees'
    )

    # ── Timecard ──────────────────────────────────────────
    clock_in       = models.DateTimeField(null=True, blank=True, db_index=True)
    clock_out      = models.DateTimeField(null=True, blank=True)

    # ── Opening cash (snapshotted at clock-in from business settings) ──
    # Staff cannot edit. Owner can change via shift_detail (creates OpeningCashChange row).
    opening_cash   = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0'))
    opening_bills  = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0'))
    opening_coins  = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0'))

    # Staff confirms drawer matches opening_cash at clock-in
    staff_confirmed_opening    = models.BooleanField(default=False)
    staff_confirmed_opening_at = models.DateTimeField(null=True, blank=True)

    # ── Closing counts (filled at clock-out — only when reconciliation enabled) ──
    counted_cash   = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    counted_bills  = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    counted_coins  = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    counted_gcash  = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    counted_bank   = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)

    closing_note   = models.TextField(blank=True, help_text="Explain any variance.")

    # ── Generic owner-edit audit (for non-opening-cash edits) ──
    edited_by      = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='edited_shift_employees'
    )
    edited_at      = models.DateTimeField(null=True, blank=True)
    
    # ── Owner closed this shift on the staff's behalf (they forgot to time out) ──
    closed_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='owner_closed_shifts'
    )
    close_reason = models.TextField(
        blank=True,
        help_text="Owner's required reason for timing out the staff on their behalf."
    )
    close_acknowledged = models.BooleanField(default=False)
    close_acknowledged_at = models.DateTimeField(null=True, blank=True)
    
    # Staff's response to an owner-close: Acknowledge, or Flag for Review with a reason.
    CLOSE_DISPUTE_REASONS = [
        ('counted_different', "I counted a different amount"),
        ('please_recount',    "Please recount the drawer"),
        ('cash_not_recorded', "A cash in/out wasn't recorded"),
        ('did_time_out',      "I didn't forget — I timed out"),
        ('other',             "Other"),
    ]
    close_dispute_reason   = models.CharField(max_length=30, choices=CLOSE_DISPUTE_REASONS, blank=True)
    close_dispute_note     = models.TextField(blank=True)
    close_dispute_resolved = models.BooleanField(default=False)

    def __str__(self):
        return f"{self.employee}"

    def save(self, *args, **kwargs):
        # Auto-sum bills + coins into the canonical totals when split is used
        if self.opening_bills or self.opening_coins:
            self.opening_cash = (self.opening_bills or Decimal('0')) + (self.opening_coins or Decimal('0'))
        if self.counted_bills is not None or self.counted_coins is not None:
            self.counted_cash = (self.counted_bills or Decimal('0')) + (self.counted_coins or Decimal('0'))
        super().save(*args, **kwargs)

    # ── Status helpers ────────────────────────────────────
    
    @property
    def has_handover_mismatch(self):
        return (any(h.needs_review for h in self.handovers_in.all())
                or any(h.needs_review for h in self.handovers_out.all()))

    @property
    def is_counted(self):
        return (self.counted_cash is not None
                or self.counted_gcash is not None
                or self.counted_bank is not None)

    @property
    def close_disputed(self):
        return bool(self.close_dispute_reason)

    @property
    def close_dispute_unresolved(self):
        return self.close_disputed and not self.close_dispute_resolved

    @property
    def close_needs_ack(self):
        """Owner closed this shift on the staff's behalf and the staff hasn't confirmed yet."""
        return self.closed_by_id is not None and not self.close_acknowledged


    @property
    def is_active(self):
        """Currently clocked in but not yet out."""
        return self.clock_in is not None and self.clock_out is None

    @property
    def hours_worked(self):
        if not self.clock_in or not self.clock_out:
            return None
        delta = self.clock_out - self.clock_in
        return round(delta.total_seconds() / 3600, 2)

    # ── Expected (computed from sales during shift window) ──

    def _shift_window_end(self):
        return self.clock_out or timezone.now()

    def _payments_total(self, method):
        from Sales.models import SalesPayment
        if not self.clock_in:
            return Decimal('0')
        return SalesPayment.objects.filter(
            business=self.shift.business,
            method=method,
            created_at__gte=self.clock_in,
            created_at__lte=self._shift_window_end(),
        ).exclude(sale__is_void=True).aggregate(t=Sum('amount'))['t'] or Decimal('0')

    def _refunds_total(self, method):
        from Sales.models import SalesReturn
        if not self.clock_in:
            return Decimal('0')
        return SalesReturn.objects.filter(
            business=self.shift.business,
            refund_method=method,
            created_at__gte=self.clock_in,
            created_at__lte=self._shift_window_end(),
        ).aggregate(t=Sum('refund_total'))['t'] or Decimal('0')

    def _purchase_payments_total(self, method):
        """Cash leaving the drawer for supplier purchases during this shift.
        COD is physically cash, so it folds into the cash bucket."""
        from Expense.models import PurchasePayment
        if not self.clock_in:
            return Decimal('0')
        methods = ['cash', 'cod'] if method == 'cash' else [method]
        return PurchasePayment.objects.filter(
            business=self.shift.business,
            method__in=methods,
            created_at__gte=self.clock_in,
            created_at__lte=self._shift_window_end(),
        ).exclude(purchase__is_void=True).aggregate(t=Sum('amount'))['t'] or Decimal('0')

    def _payouts_total(self):
        """Owner cash withdrawals during this shift (CashPayout rows)."""
        return self.cash_payouts.filter(
            created_at__gte=self.clock_in,
            created_at__lte=self._shift_window_end(),
        ).aggregate(t=Sum('amount'))['t'] or Decimal('0')


    @property
    def expected_cash(self):
        return (
            self.opening_cash
            + self._payments_total('cash')
            - self._refunds_total('cash')
            - self._purchase_payments_total('cash')   # supplier paid from drawer
            - self._payouts_total()                   # owner personal/petty withdrawals
        )



    @property
    def expected_gcash(self):
        return self._payments_total('gcash') - self._refunds_total('gcash')

    @property
    def expected_bank(self):
        return self._payments_total('bank') - self._refunds_total('bank')

    # ── Variance ──────────────────────────────────────────

    @property
    def cash_variance(self):
        if self.counted_cash is None:
            return None
        return self.counted_cash - self.expected_cash

    @property
    def gcash_variance(self):
        if self.counted_gcash is None:
            return None
        return self.counted_gcash - self.expected_gcash

    @property
    def bank_variance(self):
        if self.counted_bank is None:
            return None
        return self.counted_bank - self.expected_bank

    @property
    def total_variance(self):
        return sum(
            v for v in [self.cash_variance, self.gcash_variance, self.bank_variance]
            if v is not None
        )

    # ── Pending opening-cash changes (unacknowledged) ──

    @property
    def has_pending_opening_change(self):
        return self.opening_cash_changes.filter(acknowledged=False).exists()
    
    @property
    def hours_worked_display(self):
        """Human-friendly duration, e.g. '3 hrs 48 mins' / '4 hrs' / '45 mins'."""
        if not self.clock_in or not self.clock_out:
            return None
        total_minutes = int((self.clock_out - self.clock_in).total_seconds() // 60)
        hours, minutes = divmod(total_minutes, 60)
        parts = []
        if hours:
            parts.append(f"{hours} hr{'s' if hours != 1 else ''}")
        if minutes:
            parts.append(f"{minutes} min{'s' if minutes != 1 else ''}")
        return " ".join(parts) if parts else "0 mins"
    

class DrawerSession(TimeStampModel):
    """One shared cash drawer for a business across a day's cashier shifts.
    Only created when business.shared_cash_drawer is on. Single-till mode never makes one."""
    STATUS_CHOICES = [('open', 'Open'), ('closed', 'Closed')]

    business       = models.ForeignKey(BusinessProfile, on_delete=models.CASCADE, related_name='drawer_sessions')
    date           = models.DateField(db_index=True)
    opening_cash   = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0'))
    status         = models.CharField(max_length=10, choices=STATUS_CHOICES, default='open', db_index=True)
    current_holder = models.ForeignKey(
        'ShiftEmployee', on_delete=models.SET_NULL, null=True, blank=True, related_name='+'
    )
    opened_at      = models.DateTimeField(default=timezone.now)
    closed_at      = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-date', '-opened_at']

    def __str__(self):
        return f"Drawer {self.business_id} · {self.date} · {self.status}"

    @property
    def is_open(self):
        return self.status == 'open'

class Handover(TimeStampModel):
    """Recount when a shared drawer passes from one cashier to the next.
    The incoming cashier physically recounts (blind — they don't see the outgoing claim);
    counted_amount becomes their opening cash. A mismatch vs the outgoing cashier's claimed
    count flags the outgoing."""
    drawer_session = models.ForeignKey(DrawerSession, on_delete=models.CASCADE, related_name='handovers')
    from_shift     = models.ForeignKey(ShiftEmployee, on_delete=models.SET_NULL, null=True, blank=True, related_name='handovers_out')
    to_shift       = models.ForeignKey(ShiftEmployee, on_delete=models.SET_NULL, null=True, blank=True, related_name='handovers_in')
    claimed_amount = models.DecimalField(max_digits=10, decimal_places=2)   # outgoing cashier's closing CASH count
    counted_amount = models.DecimalField(max_digits=10, decimal_places=2)   # incoming cashier's physical CASH recount
    reviewed    = models.BooleanField(default=False)
    reviewed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['created_at']

    def __str__(self):
        return f"Handover drawer {self.drawer_session_id}: claimed ₱{self.claimed_amount} / counted ₱{self.counted_amount}"

    @property
    def variance(self):
        return self.counted_amount - self.claimed_amount

    @property
    def has_mismatch(self):
        return self.variance != 0
    
    @property
    def needs_review(self):
        return self.has_mismatch and not self.reviewed


# ──────────────────────────────────────────────────────────────
# Opening cash: defaults can be overridden for a specific date.
# ──────────────────────────────────────────────────────────────

class OpeningCashOverride(TimeStampModel):
    """One-day override of business default opening cash. Auto-expires by date check."""
    business   = models.ForeignKey(
        BusinessProfile, on_delete=models.CASCADE, related_name='cash_overrides'
    )
    date       = models.DateField(db_index=True)
    amount     = models.DecimalField(max_digits=10, decimal_places=2)
    note       = models.CharField(
        max_length=255, blank=True,
        help_text="e.g. 'Holiday rush' or 'Payday weekend'"
    )
    created_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='created_cash_overrides'
    )

    class Meta:
        unique_together = ('business', 'date')
        ordering = ['-date']

    def __str__(self):
        return f"{self.business.business_name} {self.date} → ₱{self.amount}"


# ──────────────────────────────────────────────────────────────
# Opening cash change log per shift.
# Owner can edit opening_cash via shift_detail; each change is a row here.
# Staff must acknowledge each change. Same-day window — expires next day.
# ──────────────────────────────────────────────────────────────

class OpeningCashChange(TimeStampModel):
    """Audit log of opening_cash edits on a shift. Each change requires staff ack."""
    shift           = models.ForeignKey(
        ShiftEmployee, on_delete=models.CASCADE,
        related_name='opening_cash_changes'
    )
    old_amount      = models.DecimalField(max_digits=10, decimal_places=2)
    new_amount      = models.DecimalField(max_digits=10, decimal_places=2)
    note            = models.TextField(help_text="Owner's required reason for change.")
    changed_by      = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='opening_cash_changes_made'
    )

    acknowledged    = models.BooleanField(default=False)
    acknowledged_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['created_at']

    def __str__(self):
        return f"₱{self.old_amount} → ₱{self.new_amount} on shift {self.shift_id}"

    @property
    def is_expired(self):
        if self.acknowledged:
            return False
        return not self.shift.is_active   # expires once the staff times out


    @property
    def status(self):
        if self.acknowledged:
            return 'acknowledged'
        if self.is_expired:
            return 'expired'
        return 'pending'

# ──────────────────────────────────────────────────────────────
# Cash payout — owner-recorded cash leaving the drawer for
# non-sale purposes (personal use, change float, petty cash).
# Business expenses are NOT recorded here — they go through Purchase.
# ──────────────────────────────────────────────────────────────

class CashPayout(TimeStampModel):
    """Mid-shift cash withdrawal by owner. Reduces expected cash. Requires staff ack."""
    PURPOSE_CHOICES = [
        ('business_expense', 'For the business (added to expenses)'),
        ('owner_drawing',    'Owner took personal use'),
        ('change_float',     'For change money'),
        ('petty_cash',       'Small expense'),
        ('other',            'Other'),
    ]



    shift           = models.ForeignKey(
        ShiftEmployee, on_delete=models.CASCADE, related_name='cash_payouts'
    )
    amount          = models.DecimalField(max_digits=10, decimal_places=2)
    purpose         = models.CharField(
        max_length=30, choices=PURPOSE_CHOICES, default='owner_drawing'
    )
    note            = models.TextField(blank=True)
    created_by      = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='created_cash_payouts'
    )

    acknowledged    = models.BooleanField(default=False)
    acknowledged_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['created_at']

    def __str__(self):
        return f"₱{self.amount} {self.get_purpose_display()} on shift {self.shift_id}"

    @property
    def is_expired(self):
        """Expires once the staff times out (their shift is the review window)."""
        if self.acknowledged:
            return False
        return not self.shift.is_active


    @property
    def status(self):
        if self.acknowledged:
            return 'acknowledged'
        if self.is_expired:
            return 'expired'
        return 'pending'
    
    @property
    def is_business_expense(self):
        return self.purpose == 'business_expense'

    @property
    def needs_ack(self):
        # Business expenses are booked to the books, not pocketed → no staff ack
        return self.purpose != 'business_expense'

