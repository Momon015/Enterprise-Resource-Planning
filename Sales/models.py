from django.db import models

from Product.models import Product

from user.models import User, BusinessProfile

from django.db.models import Sum, Avg

from Employee.models import Employee

from decimal import Decimal, ROUND_DOWN

from django.utils import timezone

from core.models import TimeStampModel, AbstractDocumentSequence
from django.core.exceptions import ValidationError

from core.utils.owner import get_owner

# Create your models here.

class SaleQuerySet(models.QuerySet):
    def active(self):
        """Only real, countable sales — excludes voids AND non-completed drafts.
        Use for all revenue/count aggregations."""
        return self.filter(is_void=False, status='completed')

    def drafts(self):
        """Pending + canceled — the draft list (never in the sales record)."""
        return self.exclude(status='completed')

    
    def total_revenue(self):
        return self.active().aggregate(total_revenue=Sum('total_revenue'))['total_revenue']

    def average_total_revenue(self):
        return self.active().aggregate(average_total_revenue=Avg('total_revenue'))['average_total_revenue']

class SaleSequence(AbstractDocumentSequence):
    """SI- series — one continuous run per business."""
    pass

class VoidSequence(AbstractDocumentSequence):
    """VD- series — voids are numbered documents, not just a flag.

    RMO 24-2023 Annex D-2 prints "Beg. VOID #" and "End. VOID #" on every Z reading
    alongside the SI and RETURN runs, so a void has to carry its own accountable
    number. p.4(k) says the same thing from the other direction: void, cancellation
    and refund papers are SUPPLEMENTARY INVOICES — which is also why they must print
    "THIS DOCUMENT IS NOT VALID FOR CLAIM OF INPUT TAX".

    Separate from the SI run on purpose. Voiding does not consume a sales invoice
    number; it issues a different kind of document about one.
    """
    pass
 
class Sale(TimeStampModel):
    
    VOID_REASON_CHOICES = [
        ('Wrong price',            'Wrong price'),
        ('Wrong quantity',         'Wrong quantity'),
        ('Forgot to apply discount','Forgot to apply discount'),
        ('Wrong item',             'Wrong item'),
        ('Test / accidental entry','Test / accidental entry'),
        ('Other',                  'Other'),
    ]
    
    # ── Draft / payment-confirmation status ───────────────
    STATUS_PENDING   = 'pending'
    STATUS_CANCELED  = 'canceled'
    STATUS_COMPLETED = 'completed'
    STATUS_CHOICES = [
        (STATUS_PENDING,   'Pending'),      # GCash/Bank not yet confirmed received
        (STATUS_CANCELED,  'Canceled'),     # payment never landed — kept, never a real sale
        (STATUS_COMPLETED, 'Completed'),    # confirmed received — the real sale
    ]

    CANCEL_REASON_CHOICES = [
        ('Payment not received', 'Payment not received'),
        ('Customer left',        'Customer left'),
        ('Duplicate / mistake',  'Duplicate / mistake'),
        ('Other',                'Other'),
    ]

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='sales', null=True, blank=True)
    # NULL until the sale is COMPLETED. A parked draft has no books date because it is
    # not yet in the books — it is an intent (items + amount + payment method) and
    # nothing more. Stamped in save() at the moment status becomes completed, so a
    # draft parked Monday and confirmed Wednesday books to WEDNESDAY, the day it
    # actually became a sale. See the reference note in save().
    date = models.DateField(db_index=True, null=True, blank=True)
    total_revenue = models.DecimalField(max_digits=10, decimal_places=6, null=True, blank=True)
    total_salary_cost = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    line_count = models.PositiveIntegerField(default=0)
    reference = models.CharField(max_length=255, null=True, blank=True)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, related_name='created_sales', null=True, blank=True)
    business = models.ForeignKey(BusinessProfile, on_delete=models.SET_NULL, related_name='sales', null=True, blank=True)
    is_locked = models.BooleanField(default=False, db_index=True)
    
    # ── Void (cancellation, not a return) ─────────────────
    is_void     = models.BooleanField(default=False, db_index=True)
    # The void's own accountable number (VD-0000000001), issued at void time from a
    # series separate to SI. NULL on every sale that was never voided. Drives the
    # "Beg./End. VOID #" pair on the Z reading.
    void_reference = models.CharField(max_length=255, null=True, blank=True, db_index=True)
    void_reason = models.CharField(max_length=255, blank=True)
    voided_by   = models.ForeignKey(User, on_delete=models.SET_NULL, related_name='voided_sales', null=True, blank=True)
    voided_at   = models.DateTimeField(null=True, blank=True)
    
    # ── Draft status + Cancel (a draft whose payment never landed — NOT a void) ──
    # "Draft" = any status that isn't 'completed'; drafts stay OUT of the sales record.
    status          = models.CharField(max_length=10, choices=STATUS_CHOICES,
                                        default=STATUS_COMPLETED, db_index=True)
    canceled_reason = models.CharField(max_length=255, blank=True)
    canceled_by     = models.ForeignKey(User, on_delete=models.SET_NULL,
                                        related_name='canceled_sales', null=True, blank=True)
    canceled_at     = models.DateTimeField(null=True, blank=True)
    
    # ── Intended payment for a PENDING draft (consumed by finalize on confirm) ──
    pending_method = models.CharField(max_length=20, blank=True)   # gcash / bank
    pending_status = models.CharField(max_length=10, blank=True)   # full / partial
    pending_amount = models.DecimalField(max_digits=10, decimal_places=6, null=True, blank=True)
    pending_note   = models.CharField(max_length=255, blank=True)


    discount_percent = models.DecimalField(max_digits=5, decimal_places=2, default=0)   # whole-order % (sales are %-only)
    discount_amount  = models.DecimalField(max_digits=10, decimal_places=6, default=0)  # computed peso, stored for the receipt

    objects = SaleQuerySet.as_manager()
    
    def __str__(self):
        return f"Date: {self.date} - {self.total_revenue}"
    
    def quantity_item(self):
        return sum(item.quantity for item in self.sale_items.all())
    
    @property
    def is_draft(self):
        """Not yet a real sale (pending or canceled) — kept OUT of the sales record."""
        return self.status != self.STATUS_COMPLETED
    
    @property
    def is_pending(self):
        return self.status == self.STATUS_PENDING
    
    @property
    def is_canceled(self):
        return self.status == self.STATUS_CANCELED
    
    def save(self, *args, **kwargs):
        if not self._state.adding and self.is_locked:
            # void_reference belongs here for the same reason the rest do: a posted sale
            # is immutable, but VOIDING it is an append, and the void carries its own
            # document number. Omit it and stamping the number raises on every void.
            allowed = {'is_void', 'void_reason', 'voided_by', 'voided_at', 'is_locked',
                       'void_reference'}
            uf = kwargs.get('update_fields')
            if uf is None or not set(uf) <= allowed:
                raise ValueError("Posted sale is immutable — append a void/return/adjust instead.")

        # ── Books date + accountable invoice number: COMPLETED sales only ──────
        # Both are stamped here, together, at the instant the sale becomes real.
        #
        # A draft used to claim `SI-…` the moment it was parked, which produced two
        # problems at once. (1) A cancelled draft left a number sitting in the
        # accountable series that never became an invoice. (2) Worse, the series
        # stopped being chronological — park draft A, sell B and C, then confirm A,
        # and the customer receives SI-1 after SI-3 has already gone out.
        #
        # RMO 24-2023 p.4 note: "If the system generates transaction number, SI/OR
        # number should be a different series." A number assigned before the sale
        # exists IS a transaction number, so drawing it from the SI run collided
        # with exactly that. Assigning at completion keeps the SI series
        # chronological AND free of numbers that were never issued — correct under
        # both the strict and lenient readings of "sequential series of accountable
        # documents", which matters because the RMO never defines the term.
        #
        # A cancelled draft therefore keeps date=None and reference=None forever.
        # It is a real record of an abandoned intent, not a gap: it never held a
        # number, so none went missing.
        stamped = []
        if self.status == self.STATUS_COMPLETED:
            if not self.date:
                self.date = timezone.localdate()
                stamped.append('date')

            if not self.reference and self.business:
                self.reference, _, _ = SaleSequence.issue(self.business, 'SI')
                stamped.append('reference')

        # A caller passing update_fields cannot know we just stamped these — and
        # confirm_sale_draft does exactly that. Without this, date/reference would be
        # set on the instance and silently never written. Widen the list to match
        # what actually changed rather than making every call site remember.
        update_fields = kwargs.get('update_fields')
        if stamped and update_fields is not None:
            kwargs['update_fields'] = list(set(update_fields) | set(stamped))

        super().save(*args, **kwargs)
        
    @property
    def subtotal(self):
        """Gross line total before the whole-order discount (net + discount back-out)."""
        return (self.total_revenue or Decimal('0')) + self.discount_amount
    
    def vat_summary(self):
        """PH 12% VAT breakdown, VAT-inclusive, discount-aware.
        Buckets each line by its snapshot vat_class, applies the whole-order
        discount proportionally, then extracts 12% from the VATable bucket."""
        from decimal import Decimal, ROUND_HALF_UP
        cents = Decimal('0.01')
        keep = (Decimal('100') - (self.discount_percent or 0)) / Decimal('100')

        buckets = {'vatable': Decimal('0'), 'exempt': Decimal('0'), 'zero': Decimal('0')}
        for item in self.sale_items.all():
            cls = item.vat_class if item.vat_class in buckets else 'vatable'
            buckets[cls] += (item.price_at_sale or Decimal('0')) * item.quantity

        # whole-order discount hits every bucket proportionally
        for k in buckets:
            buckets[k] = (buckets[k] * keep).quantize(cents, ROUND_HALF_UP)

        vatable_incl = buckets['vatable']
        vatable_base = (vatable_incl / Decimal('1.12')).quantize(cents, ROUND_HALF_UP)
        return {
            'vatable':      vatable_base,                       # VAT-exclusive VATable sales
            'vat':          vatable_incl - vatable_base,        # the 12%
            'exempt':       buckets['exempt'],
            'zero':         buckets['zero'],
            'vatable_incl': vatable_incl,                       # VAT-inclusive VATable
            'total':        vatable_incl + buckets['exempt'] + buckets['zero'],
        }

    @property
    def amount_paid(self):
        return self.payments.aggregate(t=models.Sum('amount'))['t'] or Decimal('0')
    
    @property
    def settlement_status(self):
        """'unpaid' (utang), 'partial', or 'paid' — drives the per-receipt Method chip."""
        paid = self.amount_paid
        total = self.total_revenue or Decimal('0')
        if paid <= 0:
            return 'unpaid'
        return 'partial' if paid < total else 'paid'

    @property
    def settlement_display(self):
        """Label: 'Debt', a method name (Cash/GCash…), 'Mixed', or 'Partial · X'."""
        status = self.settlement_status
        if status == 'unpaid':
            return 'Debt'
        methods = {p.get_method_display() for p in self.payments.all()}
        label = next(iter(methods)) if len(methods) == 1 else 'Mixed'
        return f'Partial · {label}' if status == 'partial' else label

    @property
    def payment_method_code(self):
        """Which method settled this sale — a single method code (cash/gcash/
        bank/credit), 'mixed' when more than one, or None when nothing's paid
        yet. Drives the payment-method icon in the sales list/detail."""
        methods = {p.method for p in self.payments.all()}
        if not methods:
            return None
        if len(methods) == 1:
            return next(iter(methods))
        return 'mixed'

    @property
    def settlement_badge(self):
        """Paid-status chip data (label / icon / level / amount) shared by the
        Status column and the detail page. Void is handled separately by the
        caller. 'Paid' = settled at the counter (one payment, dated the sale
        day); 'Fully Paid' = installments, mixed methods, or credit cleared on
        a later date."""
        if self.is_fully_paid:
            payments = list(self.payments.all())
            settled_at_counter = len(payments) == 1 and payments[0].date == self.date
            label = 'Paid' if settled_at_counter else 'Fully Paid'
            return {'label': label, 'icon': 'bi-check-circle-fill',
                    'level': 'success', 'amount': None}
        if self.amount_paid > 0:
            return {'label': 'Partial', 'icon': '',
                    'level': 'warning', 'amount': self.amount_paid}
        return {'label': 'Debt', 'icon': 'bi-clock-history', 'level': 'danger', 'amount': None}


    @property
    def amount_refunded(self):
        """Total of all refunds — for net_revenue calc."""
        return self.returns.aggregate(t=models.Sum('refund_total'))['t'] or Decimal('0')

    @property
    def amount_refunded_cash(self):
        """Cash handed back — money that left the drawer. Doesn't reduce outstanding
        (it was already settled; that's the only way cash can be refunded at all).

        Sums the refund_cash COLUMN, not rows whose method == 'cash'. A single return can
        be part credit and part cash, and filtering by the method string would silently
        count the whole of a mixed refund as one or the other.
        """
        return self.returns.aggregate(t=models.Sum('refund_cash'))['t'] or Decimal('0')

    @property
    def amount_refunded_credit(self):
        """Knocked off what the customer owes — no money moved."""
        return self.returns.aggregate(t=models.Sum('refund_credit'))['t'] or Decimal('0')

    @property
    def net_revenue(self):
        """Revenue minus ALL refunds — for accounting / reports. Voided or
        non-completed draft (pending/canceled) counts as 0."""
        if self.is_void or self.status != 'completed':
            return Decimal('0')
        return (self.total_revenue or Decimal('0')) - self.amount_refunded

    @property
    def has_returnable_items(self):
        """False once every unit has already been returned.

        Without this the Return form still opened on a fully-returned sale, showing a
        table where every row said "Fully returned" and no input existed — and submitting
        it just bounced with "Pick at least one item to return." Offer the action only
        when there is something left to act on.
        """
        if self.is_void or self.status != 'completed':
            return False
        return any(i.returnable_quantity > 0 for i in self.sale_items.all())

    @property
    def return_summary(self):
        """Return activity on this sale — None when nothing came back.

        A return is NOT a void. The sale really happened, and its revenue stays in the
        period it was rung up; the refund lands on the RETURN's own date instead (a
        June 29 sale refunded on July 4 reduces JULY). So this never changes what the
        sale was worth — it only says the goods came back later.

        The chip this feeds sits BESIDE the settlement badge, never replacing it.
        "Paid" and "Returned" are INDEPENDENT facts: the customer really did hand over
        the money, and the goods really did come back. Collapsing them into one chip
        throws half the story away — which is exactly why a fully-refunded sale used to
        read as a clean "Paid" row with nothing to show for it.
        """
        if self.is_void or self.status != 'completed':
            return None

        returns = list(self.returns.all())
        if not returns:
            return None

        refunded = sum((r.refund_total or Decimal('0')) for r in returns)
        total    = self.total_revenue or Decimal('0')
        full     = total > 0 and refunded >= total

        return {
            'full':    full,
            # "Partly returned", not "Partial" — the settlement badge already says
            # "Partial" for a part-paid sale, and two chips reading "Partial" side by
            # side would be unreadable.
            'label':   'Returned' if full else 'Partly returned',
            'detail':  'Fully returned' if full else 'Partly returned',
            'amount':  refunded,
            'count':   len(returns),
            # Exactly one return -> the chip can link straight at it. Several -> there's
            # no single row to point to, so the caller lists them on the detail page.
            'only':    returns[0] if len(returns) == 1 else None,
            'returns': returns,
        }

    @property
    def outstanding(self):
        """What customer still owes — voided or draft (pending/canceled) owes
        nothing (a draft was never a real, posted sale)."""
        if self.is_void or self.status != 'completed':
            return Decimal('0')
        return (self.total_revenue or Decimal('0')) - self.amount_paid - self.amount_refunded_credit


    @property
    def is_fully_paid(self):
        return self.outstanding <= Decimal('0')

class SaleItem(models.Model):
    name = models.CharField(max_length=255, null=True, blank=True)
    sale = models.ForeignKey(Sale, on_delete=models.CASCADE, related_name='sale_items', null=True, blank=True)
    product = models.ForeignKey(Product, on_delete=models.SET_NULL, related_name='sale_items', null=True, blank=True)
    price_at_sale = models.DecimalField(max_digits=10, decimal_places=6, null=True, blank=True)
    cost_price = models.DecimalField(max_digits=10, decimal_places=6, default=1.00)
    quantity = models.PositiveIntegerField(default=1)
    unsold_quantity = models.PositiveIntegerField(default=0) # will not be used, I didnt remove it due to migrations
    supplier_name = models.CharField(max_length=150, null=True, blank=True) # snapshot
    
    session = models.ForeignKey(
        'Product.ServiceSession', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='sale_items',
    )
    
    vat_class = models.CharField(max_length=8, null=True, blank=True)   # snapshot of product.vat_class at sale time

    def __str__(self):
        if self.name:
            return f"{self.name} x {self.quantity}"
        return '-'
    def save(self, *args, **kwargs):
        if not self.price_at_sale:
            self.price_at_sale = self.product.selling_price

        if self.product:
            self.name = self.product.name
            if self.session_id:
                self.name = f"{self.product.name} ({self.session.label})"
            if not self.vat_class:                          # NEW — freeze VAT treatment at sale time
                self.vat_class = self.product.vat_class

        super().save(*args, **kwargs)


        
    # def clean(self):
    #     if self.product.prepared_quantity > self.quantity:
    #         raise ValidationError('Quantity should not exceed to prepared quantity.')
    
    @property
    def total_cost_per_item(self):
        return self.cost_price * self.quantity

    @property
    def unsold_product_cost(self):
        return self.cost_price * self.unsold_quantity
    
    @property
    def total_sold_per_item(self):
        return self.price_at_sale * self.quantity

    @property
    def effective_unit_price(self):
        """What the customer ACTUALLY paid for ONE of these.

        `price_at_sale` is the STICKER price. The whole-order discount is stored only on
        the Sale (`discount_percent`) and is never written down onto the line, so a 20%
        order discount means every item is 20% off — spread proportionally, exactly the
        rule Sale.vat_summary() already applies to its VAT buckets.

        Anything that pays money BACK must price the line through here, never through
        `price_at_sale`, or it refunds more than was ever collected. That was a real
        bug: the return form prefilled the sticker price, so a partial return of a
        discounted sale silently over-refunded, and a FULL return totalled more than
        the sale and got rejected by the refund ceiling.

        Rounds DOWN to centavos on purpose: it keeps the sum of the lines at or under
        `total_revenue`, so a full return can always be processed and can never trip the
        `max_refund` guard on a rounding remainder.
        """
        price = self.price_at_sale or Decimal('0')
        pct = (self.sale.discount_percent or Decimal('0')) if self.sale_id else Decimal('0')
        if pct <= 0:
            return price
        keep = (Decimal('100') - pct) / Decimal('100')
        return (price * keep).quantize(Decimal('0.01'), rounding=ROUND_DOWN)

    @property
    def net_sale_value(self):
        return (self.total_sold_per_item) - self.unsold_product_cost
    
    
    @property
    def total_returned_quantity(self):
        return self.return_items.aggregate(
            t=models.Sum('quantity'))['t'] or 0

    @property
    def returnable_quantity(self):
        return self.quantity - self.total_returned_quantity

    
        
# ──────────────────────────────────────────────────────────────
# SALES PAYMENTS — for utang / customer credit tracking (FUTURE)
# Defined now for symmetry; not actively wired in v1.
# ──────────────────────────────────────────────────────────────

class SalesPayment(TimeStampModel):
    PAYMENT_METHOD_CHOICES = [
        ('cash', 'Cash'),
        ('gcash', 'GCash'),
        ('bank', 'Bank Transfer'),
        ('credit', 'Store Credit'),
    
    ]

    sale = models.ForeignKey(Sale, on_delete=models.CASCADE, related_name='payments')
    amount = models.DecimalField(max_digits=10, decimal_places=6)
    date = models.DateField(db_index=True)
    method = models.CharField(max_length=20, choices=PAYMENT_METHOD_CHOICES, default='cash')
    note = models.CharField(max_length=255, blank=True)
    business = models.ForeignKey(
        BusinessProfile, on_delete=models.SET_NULL,
        related_name='sales_payments', null=True, blank=True)

    created_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='created_sales_payments')
    
    class Meta:
        ordering = ['date', 'created_at']
        
    def __str__(self):
        return f"₱{self.amount} payment for sale {self.sale.reference}"
    
    def save(self, *args, **kwargs):
        if not self.date:
            self.date = timezone.localdate()
            
        super().save(*args, **kwargs)

# ──────────────────────────────────────────────────────────────
# SALES RETURNS — customer returns (defective, changed mind, etc.)
# Per-item triage (resellable → Stock; damaged → Waste).
# ──────────────────────────────────────────────────────────────

class SalesReturnSequence(AbstractDocumentSequence):
    """SRR- series — one continuous run per business."""
    pass

class SalesReturn(TimeStampModel):
    # refund_method is now DERIVED, not chosen (2026-07-12). The refund is split by
    # core.utils.returns.split_refund — debt first, cash second — so a refund that is
    # impossible (cash back on a sale nobody paid for) can't be represented at all.
    # This field is the display summary; refund_cash / refund_credit carry the money.
    REFUND_METHOD_CHOICES = [
        ('cash',   'Cash refund'),
        ('credit', 'Deducted from balance'),
        ('mixed',  'Balance + cash'),
    ]

    REASON_CHOICES = [
        # Real returns
        ('customer_changed_mind', 'Customer changed mind'),
        ('defective',             'Defective'),
        ('wrong_item',            'Wrong item'),
        ('expired',               'Expired'),
        
        # Corrections
        ('amount_correction',     'Amount correction'),
        ('staff_error',           'Staff error'),
        ('other',                 'Other'),
    ]

    original_sale = models.ForeignKey(
        Sale, on_delete=models.PROTECT, related_name='returns')
    
    date = models.DateField(db_index=True)
    reason = models.CharField(max_length=30, choices=REASON_CHOICES, default='customer_changed_mind')
    reason_note = models.CharField(max_length=255, blank=True)
    refund_total = models.DecimalField(max_digits=10, decimal_places=6, default=0)

    # The actual split — refund_total = refund_cash + refund_credit, always.
    #   refund_credit = knocked off what the customer still owes (no money moves)
    #   refund_cash   = money physically handed back
    # A cash figure can only be non-zero once the balance is settled. See split_refund().
    refund_cash   = models.DecimalField(max_digits=10, decimal_places=6, default=0)
    refund_credit = models.DecimalField(max_digits=10, decimal_places=6, default=0)

    # Derived from the split above — for badges only. Never trust it for money.
    refund_method = models.CharField(max_length=20, choices=REFUND_METHOD_CHOICES, default='cash')
    reference = models.CharField(max_length=255, blank=True)  # # auto-generated SRR-0000000001

    business = models.ForeignKey(BusinessProfile, on_delete=models.SET_NULL,
        related_name='sales_returns', null=True, blank=True)
    
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, 
        related_name='created_sales_returns', null=True, blank=True)
        
    class Meta:
        ordering = ['-date', '-created_at']

    def __str__(self):
        return f"{self.reference or '(unsaved)'} — ₱{self.refund_total}"

    def save(self, *args, **kwargs):
        if not self.date:
            self.date = timezone.localdate()

        if not self.reference and self.business:
            self.reference, _, _ = SalesReturnSequence.issue(self.business, 'SRR')
            
        super().save(*args, **kwargs)
        
class SalesReturnItem(models.Model):
    sales_return = models.ForeignKey(SalesReturn,
        on_delete=models.CASCADE, related_name='items')
    
    original_sale_item = models.ForeignKey(SaleItem, on_delete=models.SET_NULL,
        related_name='return_items', null=True, blank=True,)  
    
    name = models.CharField(max_length=255)  # snapshot
    quantity = models.PositiveIntegerField(default=1)
    unit_refund = models.DecimalField(max_digits=10, decimal_places=6, default=0)
    resellable = models.BooleanField(default=True)  # True → Stock; False → Waste

    def __str__(self):
        return f"{self.name} × {self.quantity}"

    @property
    def line_total(self):
        return self.unit_refund * self.quantity
    
class SaleEmployee(TimeStampModel):
    """
    Tracks which employees worked during a sale session.
    Currently used for labor / salary cost tracking in summary/dashboard.
    NOTE: Shift assignment will move to a shared cart flow in Phase 2.
    For now, owner logs shift manually after confirming sale.
    """
    
    name = models.CharField(max_length=255, null=True, blank=True)
    sale = models.ForeignKey(Sale, on_delete=models.CASCADE, related_name='sale_employees', null=True, blank=True)
    employee = models.ForeignKey(Employee, on_delete=models.SET_NULL, related_name='sale_employees', null=True, blank=True)
    daily_rate = models.DecimalField(max_digits=10, decimal_places=2)
    
    def __str__(self):
        if self.name:
            return f"Sale Record ID: #{self.sale.id} - {self.name}"
        return 'No employee info'

    def save(self, *args, **kwargs):
        if self.employee:
            self.name = self.employee.name

        super().save(*args, **kwargs)