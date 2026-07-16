from django.db import models
from django.utils.text import slugify

from core.models import TimeStampModel, SlugModel, Category, StatusModel, AbstractDocumentSequence
from Supplier.models import Material

from django.utils import timezone

from django.db.models import Sum, Avg, Value

from django.db.models.functions import Coalesce
from decimal import Decimal, ROUND_DOWN

from user.models import User, BusinessProfile

from Product.models import Product
from django.db.models import F

from core.utils.owner import get_owner

# Create your models here.

"""
This is a custom queryset for computing the average
and the sum of the total cost make sure u save it to 
the parent model use either objects(recommended) or 
any other name so u can simply called Purchase.objects.
<function_name>().
"""

class PurchaseQuerySet(models.QuerySet):
    def active(self):
        return self.filter(is_void=False)
    
    def purchase_total_cost(self):
        return self.active().aggregate(monthly_cost=Coalesce(Sum('total_cost'), Value(Decimal('0'))))['monthly_cost']
        
    def average_total_cost(self):
        return self.active().aggregate(monthly_average_cost=Coalesce(Avg('total_cost'), Value(Decimal('0'))))['monthly_average_cost']

class PurchaseSequence(AbstractDocumentSequence):
    """PI- series — one continuous run per business."""
    pass
    
class Purchase(TimeStampModel):
    VOID_REASON_CHOICES = [
        ('Wrong price',             'Wrong price'),
        ('Wrong quantity',          'Wrong quantity'),
        ('Wrong item',              'Wrong item'),
        ('Wrong supplier',          'Wrong supplier'),
        ('Test / accidental entry', 'Test / accidental entry'),
        ('Other',                   'Other'),
    ]
    
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='purchases')
    total_cost = models.DecimalField(max_digits=10, decimal_places=6, null=True, blank=True)
    status = models.ForeignKey(StatusModel, on_delete=models.SET_NULL, null=True)
    is_paid = models.BooleanField(default=False)
    line_count = models.PositiveIntegerField(default=0)
    purchase_date = models.DateField(null=True, blank=True, db_index=True)
    reference = models.CharField(max_length=255, null=True, blank=True)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='created_purchases')
    business = models.ForeignKey(BusinessProfile, on_delete=models.SET_NULL, related_name='purchases', null=True, blank=True)
    due_date = models.DateField(null=True, blank=True, db_index=True)
    is_locked = models.BooleanField(default=False, db_index=True)

    # ── Void (cancellation, not a return) ─────────────────
    is_void     = models.BooleanField(default=False, db_index=True)
    void_reason = models.CharField(max_length=255, blank=True)
    voided_by   = models.ForeignKey(User, on_delete=models.SET_NULL,
                      related_name='voided_purchases', null=True, blank=True)
    voided_at   = models.DateTimeField(null=True, blank=True)
    
    discount_percent = models.DecimalField(max_digits=5, decimal_places=2, default=0)   # whole-order % (bulk supplier deal)
    discount_amount  = models.DecimalField(max_digits=10, decimal_places=6, default=0)  # computed peso, stored for the receipt

    
    # save the custom queryset as_manager()
    objects = PurchaseQuerySet.as_manager()
    
    def __str__(self):
        return f"Purchase ID: #{self.id} - {self.formatted_date}, Total Cost: {self.total_cost}"

    def save(self, *args, **kwargs):
        if not self._state.adding and self.is_locked:
            allowed = {'is_void', 'void_reason', 'voided_by', 'voided_at',
                       'status', 'is_paid', 'is_locked'}
            uf = kwargs.get('update_fields')
            if uf is None or not set(uf) <= allowed:
                raise ValueError("Posted purchase is immutable — append a void/return/adjust instead.")
        if not self.purchase_date:
            self.purchase_date = timezone.localdate()
        
        # if self.status and self.status.slug == 'paid':
        #     self.is_paid = True
            
        if not self.reference:
            if not self.reference and self.business:
                self.reference, _, _ = PurchaseSequence.issue(self.business, 'PO')

        # if not self.slug and self.status:
        #     self.slug = self.status.slug
            
        # always update the total cost
        # if self.pk:
        #     self.total_cost = self.total_cost_per_purchase
        super().save(*args, **kwargs)
        
    @property
    def formatted_date(self):
        local_time = timezone.localtime(self.created_at)
        return local_time.strftime("%B %d %Y %I:%M %p")
    
    @property
    def total_cost_per_purchase(self):
        return sum(item.total_price_per_item for item in self.materials.all())
    
    @property
    def total_quantity_items(self):
        return sum(item.quantity for item in self.materials.all())
    
    # ADMIN PANEL
    
    @property
    def total_discount(self):
        return sum(item.discount if item.discount > 0 else 0 for item in self.materials.all())

    @property
    def purchase_items(self):
        return [item.material.name for item in self.materials.all()]
    
    @property
    def quantity_items(self):
        return [item.quantity for item in self.materials.all()]
    
    @property
    def amount_paid(self):
        """Sum of all payments made against this purchase."""
        return self.payments.aggregate(t=models.Sum('amount'))['t'] or Decimal('0')

    @property
    def amount_refunded_credit(self):
        """Knocked off what we owe the supplier — no money moved. Reduces outstanding."""
        return self.returns.aggregate(t=models.Sum('refund_credit'))['t'] or Decimal('0')

    @property
    def amount_refunded_cash(self):
        """Cash the supplier handed back. Doesn't reduce outstanding — the balance was
        already settled, which is the only way cash can come back at all.

        Sums the refund_cash COLUMN, not rows whose method == 'cash': one return can be
        part credit and part cash, and filtering by the method string would count the
        whole of a mixed refund as one or the other.
        """
        return self.returns.aggregate(t=models.Sum('refund_cash'))['t'] or Decimal('0')

    @property
    def amount_refunded(self):
        """Every peso the supplier gave back — cash refunds AND credit notes."""
        return self.returns.aggregate(t=models.Sum('refund_total'))['t'] or Decimal('0')

    @property
    def has_returnable_items(self):
        """False once every unit has already gone back to the supplier. Mirror of
        Sale.has_returnable_items — same bug, same fix: don't offer a Return form that
        has nothing left to return."""
        if self.is_void:
            return False
        return any(i.returnable_quantity > 0 for i in self.materials.all())

    @property
    def return_summary(self):
        """Return activity on this purchase — None when nothing went back.

        The mirror of Sale.return_summary, and it exists for the same reason: a purchase
        we sent back to the supplier still displayed as a clean, fully-Paid order with no
        sign anything had been returned. Sits BESIDE the settlement badge — "we paid" and
        "we sent it back" are independent facts.

        Like the sales side, this does NOT rewrite the purchase: the order really was
        placed on its date, and the refund lands on the RETURN's own date.
        """
        if self.is_void:
            return None

        returns = list(self.returns.all())
        if not returns:
            return None

        refunded = sum((r.refund_total or Decimal('0')) for r in returns)
        total    = self.total_cost or Decimal('0')
        full     = total > 0 and refunded >= total

        return {
            'full':    full,
            'label':   'Returned' if full else 'Partly returned',
            'detail':  'Fully returned' if full else 'Partly returned',
            'amount':  refunded,
            'count':   len(returns),
            'only':    returns[0] if len(returns) == 1 else None,
            'returns': returns,
        }

    @property
    def outstanding(self):
        """What's still owed to the supplier — voided owes nothing."""
        if self.is_void:
            return Decimal('0')
        return (self.total_cost or Decimal('0')) - self.amount_paid - self.amount_refunded_credit


    @property
    def is_fully_paid(self):
        return self.outstanding <= Decimal('0')
    
    @property
    def settlement_status(self):
        """'unpaid' (utang), 'partial', or 'paid' — drives the per-PO Method chip."""
        paid = self.amount_paid
        total = self.total_cost or Decimal('0')
        if paid <= 0:
            return 'unpaid'
        return 'partial' if paid < total else 'paid'

    @property
    def settlement_display(self):
        """Label: 'Debt', a method name (COD/Cash/GCash…), 'Mixed', or 'Partial · X'."""
        status = self.settlement_status
        if status == 'unpaid':
            return 'Debt'
        methods = {p.get_method_display() for p in self.payments.all()}
        label = next(iter(methods)) if len(methods) == 1 else 'Mixed'
        return f'Partial · {label}' if status == 'partial' else label

    @property
    def payment_method_code(self):
        """Which method settled this purchase — a single method code (cod/cash/
        gcash/bank/credit), 'mixed' when more than one, or None when nothing's
        paid yet. Drives the payment-method icon in the purchase list/detail."""
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
        caller. 'Paid' = settled on delivery (one payment, dated the purchase
        day); 'Fully Paid' = installments, mixed methods, or credit cleared on
        a later date."""
        if self.is_fully_paid:
            payments = list(self.payments.all())
            settled_on_delivery = len(payments) == 1 and payments[0].date == self.purchase_date
            label = 'Paid' if settled_on_delivery else 'Fully Paid'
            return {'label': label, 'icon': 'bi-check-circle-fill',
                    'level': 'success', 'amount': None}
        if self.amount_paid > 0:
            return {'label': 'Partial', 'icon': '',
                    'level': 'warning', 'amount': self.amount_paid}
        return {'label': 'Debt', 'icon': 'bi-clock-history', 'level': 'danger', 'amount': None}



class PurchaseItem(TimeStampModel):
    name = models.CharField(max_length=255, null=True, blank=True)
    purchase = models.ForeignKey(Purchase, on_delete=models.SET_NULL, related_name='materials', null=True, blank=True)
    material = models.ForeignKey(Material, on_delete=models.SET_NULL, related_name='items', null=True, blank=True)
    discount = models.DecimalField(max_digits=5, decimal_places=2, default=0.00)
    quantity = models.PositiveIntegerField(default=1)
    price = models.DecimalField(max_digits=10, decimal_places=6, default=0.00)
    supplier = models.CharField(max_length=255, null=True, blank=True)
    
    def __str__(self):
        if self.name:
            return f"{self.name} - ({self.material.price} x {self.quantity}) - {self.discount} = {self.total_item_discount}"
        return 'No info'
    
    def save(self, *args, **kwargs):
        if self.material:
            self.name = self.material.name
        
        if self.material and self.material.supplier:
            self.supplier = self.material.supplier.name
            
        super().save(*args, **kwargs)
        
    @property
    def material_price(self):
        return self.material.price
    
    @property
    def total_price_per_item(self):
        return self.price * self.quantity
    
    @property
    def total_item_discount(self):
        return self.total_price_per_item - self.discount

    @property
    def effective_unit_price(self):
        """What we ACTUALLY paid the supplier for ONE of these.

        A purchase carries two discounts and they are mutually EXCLUSIVE — in whole-order
        % mode the per-item flats are forced to 0 (Expense/views.py), so exactly one of
        the two terms below is ever non-zero and this needs no branching:

            line = price × qty − discount          (flat mode; discount is 0 in % mode)
            line = line × (100 − percent) / 100    (percent is 0 in flat mode)

        ★ Price every REFUND through here, never through `price`, or the return claims
          back more than the PO was ever worth — and a FULL return totals more than
          `total_cost` and gets rejected by the refund ceiling. Twin of
          Sales.SaleItem.effective_unit_price; same reasoning, mirrored side.

        Rounds DOWN to centavos so the lines always sum to at or under `total_cost`.
        """
        qty = self.quantity or 0
        if qty <= 0:
            return Decimal('0')

        line = (self.price or Decimal('0')) * qty - (self.discount or Decimal('0'))
        pct = (self.purchase.discount_percent or Decimal('0')) if self.purchase_id else Decimal('0')
        if pct > 0:
            line = line * (Decimal('100') - pct) / Decimal('100')

        unit = max(line, Decimal('0')) / qty
        return unit.quantize(Decimal('0.01'), rounding=ROUND_DOWN)

    @property
    def total_returned_quantity(self):
        return self.return_items.aggregate(
            t=models.Sum('quantity'))['t'] or 0

    @property
    def returnable_quantity(self):
        return self.quantity - self.total_returned_quantity

    
    # def material_discount(self):
    #     if self.discount:
    #         return self.total_price_per_item - self.discount
    #     return self.total_price_per_item
    
# ──────────────────────────────────────────────────────────────
# PURCHASE PAYMENTS — for partial / Net 15-30 / DP scenarios
# Multiple payments per Purchase. Outstanding = total_cost - sum(payments) - refunded_credits
# ──────────────────────────────────────────────────────────────

class PurchasePayment(TimeStampModel):
    PAYMENT_METHOD_CHOICES = [
        ('cod', 'Cash on Delivery'),
        ('cash', 'Cash'),
        ('gcash', 'GCash'),
        ('bank', 'Bank Transfer'),
        ('credit', 'Credit / Other'),
    ]
    
    purchase = models.ForeignKey(
        Purchase, on_delete=models.CASCADE, related_name='payments')
    
    amount = models.DecimalField(max_digits=10, decimal_places=6)
    date = models.DateField(db_index=True)
    method = models.CharField(max_length=20, choices=PAYMENT_METHOD_CHOICES, default='cod')
    note = models.CharField(max_length=255, blank=True)
    business = models.ForeignKey( 
        BusinessProfile, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='purchase_payments')
    
    created_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, 
        related_name='created_purchase_payments')
    
    class Meta:
        ordering = ['date', 'created_at']
        
    def __str__(self):
        return f"₱{self.amount} payment for {self.purchase.reference}"
    
    def save(self, *args, **kwargs):
        if not self.date:
            self.date = timezone.localdate()
            
        super().save(*args, **kwargs)
        
# ──────────────────────────────────────────────────────────────
# PURCHASE RETURNS — supplier returns (defective, wrong qty, etc.)
# ──────────────────────────────────────────────────────────────
class PurchaseReturnSequence(AbstractDocumentSequence):
    """PRR- series — one continuous run per business."""
    pass

class PurchaseReturn(TimeStampModel):
    # ★ refund_method is now DERIVED, not chosen (2026-07-12) — mirror of SalesReturn.
    # The refund is split by core.utils.returns.split_refund (debt first, cash second),
    # so a cash refund on an order we never paid for can't be represented at all.
    REFUND_METHOD_CHOICES = [
        ('cash',   'Cash refund'),
        ('credit', 'Credit on outstanding balance'),
        ('mixed',  'Credit + cash'),
    ]
    
    REASON_CHOICES = [
        # Real returns
        ('defective', 'Defective'),
        ('wrong_item', 'Wrong item'),
        ('expired', 'Expired'),
        ('damaged_delivery', 'Damaged in delivery'),
        
        # Corrections
        ('qty_correction', 'Quantity correction'),
        ('amount_correction', 'Amount correction'),
        ('staff_error', 'Staff error'),
        ('other', 'Other'),
    ]
    
    original_purchase = models.ForeignKey(
        Purchase, on_delete=models.PROTECT, related_name='returns')
    
    date = models.DateField(db_index=True)
    reason = models.CharField(max_length=30, choices=REASON_CHOICES, default='defective')
    reason_note = models.CharField(max_length=255, blank=True)
    refund_total = models.DecimalField(max_digits=10, decimal_places=6, default=0)

    # The actual split — refund_total = refund_cash + refund_credit, always.
    #   refund_credit = knocked off what we still owe the supplier (no money moves)
    #   refund_cash   = money the supplier physically handed back
    # Cash can only be non-zero once the balance is settled. See split_refund().
    refund_cash   = models.DecimalField(max_digits=10, decimal_places=6, default=0)
    refund_credit = models.DecimalField(max_digits=10, decimal_places=6, default=0)

    # Derived from the split above — for badges only. Never trust it for money.
    refund_method = models.CharField(max_length=20, choices=REFUND_METHOD_CHOICES, default='cash')
    reference = models.CharField(max_length=255, blank=True) # auto-generated PRR-0000000001
    
    business = models.ForeignKey(BusinessProfile, on_delete=models.SET_NULL,
        related_name='purchase_returns', null=True, blank=True)
    
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL,
        related_name='created_purchase_returns', null=True, blank=True)
    
    class Meta:
        ordering = ['-date', '-created_at']
        
    def __str__(self):
        return f"{self.reference or '(unsaved)'} — ₱{self.refund_total}"
    
    def save(self, *args, **kwargs):
        if not self.date:
            self.date = timezone.localdate()

        if not self.reference and self.business:
            self.reference, _, _ = PurchaseReturnSequence.issue(self.business, 'PRR')

        super().save(*args, **kwargs)
        
class PurchaseReturnItem(models.Model):
    purchase_return = models.ForeignKey(PurchaseReturn, on_delete=models.CASCADE,
        related_name='items')
    
    original_purchase_item = models.ForeignKey(PurchaseItem, on_delete=models.SET_NULL,
        related_name='return_items', null=True, blank=True)
    
    name = models.CharField(max_length=255) # snasphot
    quantity = models.PositiveIntegerField(default=1)
    unit_refund = models.DecimalField(max_digits=10, decimal_places=6, default=0)

    def __str__(self):
        return f"{self.name} × {self.quantity}"

    @property
    def line_total(self):
        return self.unit_refund * self.quantity
    
    
    
class WasteQuerySet(models.QuerySet):
    def total_waste_cost(self):
        return self.aggregate(total_waste_cost=Sum('total_cost'))['total_waste_cost'] or 0

class Waste(TimeStampModel):
    REASON_CHOICES = [
        ('spoilage',     'Spoilage'),
        ('expired',      'Expired'),
        ('damage',       'Damage'),
        ('defective',    'Defective'),   
        ('personal_use', 'Personal Use'),
        ('service',      'Service Use'),
        ('theft',        'Theft'),
        ('other',        'Other'),
    ]
    
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='wastes')
    date = models.DateField(db_index=True)
    total_cost = models.DecimalField(max_digits=10, decimal_places=6)
    reason = models.CharField(max_length=20, choices=REASON_CHOICES, default='other', db_index=True)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='created_wastes')
    business = models.ForeignKey(BusinessProfile, on_delete=models.SET_NULL, related_name='wastes', null=True, blank=True)
    
    objects = WasteQuerySet.as_manager()
    
    def __str__(self):
        if self.date:
            return f"Waste - {self.date}"
        
    def save(self, *args, **kwargs):
        if not self.date:
            self.date = timezone.localdate()
            
        super().save(*args, **kwargs)
    
    @property
    def waste_cost(self):
        return sum(item.price * item.quantity for item in self.waste_items.all())
        
class WasteItemQuerySet(models.QuerySet):
    def total_product_waste(self):
        return self.filter(product__isnull=False).aggregate(total_product_waste=Sum(F('price') * F('quantity')))['total_product_waste'] or 0

    def total_material_waste(self):
        return self.filter(material__isnull=False).aggregate(total_material_waste=Sum(F('price') * F('quantity')))['total_material_waste'] or 0
    
class WasteItem(models.Model):
    name = models.CharField(max_length=255, null=True, blank=True)
    waste = models.ForeignKey(Waste, on_delete=models.SET_NULL, related_name='waste_items', null=True, blank=True)
    material = models.ForeignKey(Material, on_delete=models.SET_NULL, related_name='waste_items', null=True, blank=True)
    product = models.ForeignKey(Product, on_delete=models.SET_NULL, related_name='waste_items', null=True, blank=True)
    price = models.DecimalField(max_digits=10, decimal_places=6)
    quantity = models.PositiveBigIntegerField(default=0)
    supplier = models.CharField(max_length=255, null=True, blank=True)
    
    objects = WasteItemQuerySet.as_manager()
    
    def __str__(self):
        item = self.material or self.product
        if not self.name:
            return 'No info provided'
        return f"{self.name} - {self.quantity}"
    
    def save(self, *args, **kwargs):
        # if self.product and not self.material:
        #     self.price = self.product.cost_price
        # elif self.material and not self.product:
        #     self.price = self.material.price
        
        if self.product:
            self.name = self.product.name
        
        if self.material:
            self.name = self.material.name
            
        if self.material and self.material.supplier:
            self.supplier = self.material.supplier.name
        
        super().save(*args, **kwargs)
        
    @property
    def total_product_cost(self):
        return self.price * self.quantity
    
    @property
    def total_material_cost(self):
        return self.price * self.quantity
    
    
    
class MiscExpense(TimeStampModel):
    user = models.ForeignKey(User, on_delete=models.CASCADE, null=True, blank=True)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='created_misc_expenses')
    name = models.CharField(max_length=255)
    business = models.ForeignKey(BusinessProfile, on_delete=models.SET_NULL, related_name='misc_expenses', null=True, blank=True)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    category = models.ForeignKey(Category, on_delete=models.SET_NULL, null=True, blank=True)
    
class ExpenseQuerySet(models.QuerySet):
    def total_amount_cost(self):
        return self.aggregate(total_amount_cost=Sum('total_amount'))['total_amount_cost'] or 0
    
    def average_amount_cost(self):
        return self.aggregate(average_amount_cost=Avg('total_amount'))['average_amount_cost'] or 0

class Expense(TimeStampModel):
    # How the bill was paid. An expense is a single-shot outflow (no utang /
    # installments), so the method lives right here — no separate payment ledger.
    # Codes match core/templatetags/payment_tags.py so {% payment_method_badge %}
    # renders it with no new CSS.
    PAYMENT_METHOD_CHOICES = [
        ('cash',  'Cash'),
        ('gcash', 'GCash'),
        ('bank',  'Bank Transfer'),
    ]

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='expenses', null=True, blank=True)
    total_amount = models.DecimalField(max_digits=10, decimal_places=2)
    date = models.DateField(db_index=True)
    payment_method = models.CharField(max_length=20, choices=PAYMENT_METHOD_CHOICES, default='cash')
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='created_expenses')
    business = models.ForeignKey(BusinessProfile, on_delete=models.SET_NULL, related_name='expenses', null=True, blank=True)
    
    objects = ExpenseQuerySet.as_manager()
    
    def __str__(self):
        return f"{self.id} - {self.created_by}" 
    
    def save(self, *args, **kwargs):
        if not self.date:
            self.date = timezone.localdate()
        super().save(*args, **kwargs)

    
    def total_expense_items(self):
        return sum(item.count() for item in self.expense_items.all())
    

class ExpenseItem(models.Model):
    expense = models.ForeignKey(Expense, on_delete=models.SET_NULL, related_name='expense_items', null=True, blank=True)
    misc_expense = models.ForeignKey(MiscExpense, on_delete=models.SET_NULL, related_name='expense_items', null=True, blank=True)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    name = models.CharField(max_length=255)  # snapshot
    category = models.CharField(max_length=150, null=True, blank=True)  # snapshot
    
    def __str__(self):
        return self.name
    
    def save(self, *args, **kwargs):
        if not self.category:
            self.category = self.misc_expense.category.name
        
        if not self.name:
            self.name = self.misc_expense.name
            
        if not self.amount:
            self.amount = self.misc_expense.amount
        
        super().save(*args, **kwargs)