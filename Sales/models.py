from django.db import models

from Product.models import Product

from user.models import User, BusinessProfile

from django.db.models import Sum, Avg

from Employee.models import Employee

from decimal import Decimal

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
    date = models.DateField(db_index=True)
    total_revenue = models.DecimalField(max_digits=10, decimal_places=6, null=True, blank=True)
    total_salary_cost = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    line_count = models.PositiveIntegerField(default=0)
    reference = models.CharField(max_length=255, null=True, blank=True)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, related_name='created_sales', null=True, blank=True)
    business = models.ForeignKey(BusinessProfile, on_delete=models.SET_NULL, related_name='sales', null=True, blank=True)
    is_locked = models.BooleanField(default=False, db_index=True)
    
    # ── Void (cancellation, not a return) ─────────────────
    is_void     = models.BooleanField(default=False, db_index=True)
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
            allowed = {'is_void', 'void_reason', 'voided_by', 'voided_at', 'is_locked'}
            uf = kwargs.get('update_fields')
            if uf is None or not set(uf) <= allowed:
                raise ValueError("Posted sale is immutable — append a void/return/adjust instead.")

        if not self.date:
            self.date = timezone.localdate()
    
        if not self.reference and self.business:
            self.reference, _, _ = SaleSequence.issue(self.business, 'SI')

        
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
        """Cash refunds — money already left our pocket, doesn't reduce outstanding."""
        return self.returns.filter(refund_method='cash').aggregate(
            t=models.Sum('refund_total'))['t'] or Decimal('0')

    @property
    def amount_refunded_credit(self):
        """Store-credit refunds — reduces customer's outstanding (they don't owe that amount)."""
        return self.returns.filter(refund_method='credit').aggregate(
            t=models.Sum('refund_total'))['t'] or Decimal('0')

    @property
    def net_revenue(self):
        """Revenue minus ALL refunds — for accounting / reports. Voided or
        non-completed draft (pending/canceled) counts as 0."""
        if self.is_void or self.status != 'completed':
            return Decimal('0')
        return (self.total_revenue or Decimal('0')) - self.amount_refunded

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
    REFUND_METHOD_CHOICES = [
        ('cash',   'Cash refund'),
        ('credit', 'Store credit'),
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