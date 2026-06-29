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
        """Excludes voided sales — use for all revenue/count aggregations."""
        return self.filter(is_void=False)
    
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
    
    discount_percent = models.DecimalField(max_digits=5, decimal_places=2, default=0)   # whole-order % (sales are %-only)
    discount_amount  = models.DecimalField(max_digits=10, decimal_places=6, default=0)  # computed peso, stored for the receipt


    objects = SaleQuerySet.as_manager()
    
    def __str__(self):
        return f"Date: {self.date} - {self.total_revenue}"
    
    def quantity_item(self):
        return sum(item.quantity for item in self.sale_items.all())
    
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
        """Label: 'Utang / Debt', a method name (Cash/GCash…), 'Mixed', or 'Partial · X'."""
        status = self.settlement_status
        if status == 'unpaid':
            return 'Utang / Debt'
        methods = {p.get_method_display() for p in self.payments.all()}
        label = next(iter(methods)) if len(methods) == 1 else 'Mixed'
        return f'Partial · {label}' if status == 'partial' else label


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
        """Revenue minus ALL refunds — for accounting / reports. Voided = 0."""
        if self.is_void:
            return Decimal('0')
        return (self.total_revenue or Decimal('0')) - self.amount_refunded

    @property
    def outstanding(self):
        """What customer still owes — voided sale owes nothing."""
        if self.is_void:
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
    reference = models.CharField(max_length=255, blank=True)  # auto SRR-YYYY-NNNN
    
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

        if not self.reference:
            year = timezone.now().year
            last = SalesReturn.objects.filter(
                business=self.business, date__year=year
            ).order_by('-reference').first()
            if last and last.reference:
                last_n = int(last.reference.split('-')[-1])
                next_n = last_n + 1
            else:
                next_n = 1
            self.reference = f"SRR-{year}-{next_n:04d}"

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