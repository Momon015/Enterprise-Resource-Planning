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

# Create your models here.

"""
This is a custom queryset for computing the average
and the sum of the total cost make sure u save it to 
the parent model use either objects(recommended) or 
any other name so u can simply called Purchase.objects.
<function_name>().
"""

class PurchaseQuerySet(models.QuerySet):
    def purchase_total_cost(self):
        return self.aggregate(monthly_cost=Coalesce(Sum('total_cost'), Value(Decimal('0'))))['monthly_cost']
        
    def average_total_cost(self):
        return self.aggregate(monthly_average_cost=Coalesce(Avg('total_cost'), Value(Decimal('0'))))['monthly_average_cost']
    
    
class Purchase(TimeStampModel):
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
    
    
    # save the custom queryset as_manager()
    objects = PurchaseQuerySet.as_manager()
    
    def __str__(self):
        return f"Purchase ID: #{self.id} - {self.formatted_date}, Total Cost: {self.total_cost}"
    
    def save(self, *args, **kwargs):
        if not self.purchase_date:
            self.purchase_date = timezone.localdate()
        
        # if self.status and self.status.slug == 'paid':
        #     self.is_paid = True
            
        if not self.reference:
            owner = get_owner(self.user)
            year = timezone.now().year
            
            last_purchase = (
                Purchase.objects.filter(user=owner, purchase_date__year=year).order_by('-reference').first()
            )
            
            if last_purchase and last_purchase.reference:
                last_number = int(last_purchase.reference.split('-')[-1])
                next_number = last_number + 1
            else:
                next_number = 1
            
            self.reference = f"PO-{year}-{next_number:04d}"
            
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
        """Sum of credit-method refunds (these reduce outstanding)."""
        return self.returns.filter(refund_method='credit').aggregate(
            t=models.Sum('refund_total'))['t'] or Decimal('0')

    @property
    def amount_refunded_cash(self):
        """Sum of cash-method refunds (these don't reduce outstanding — money already returned)."""
        return self.returns.filter(refund_method='cash').aggregate(
            t=models.Sum('refund_total'))['t'] or Decimal('0')

    @property
    def outstanding(self):
        """What's still owed to the supplier."""
        return (self.total_cost or Decimal('0')) - self.amount_paid - self.amount_refunded_credit

    @property
    def is_fully_paid(self):
        return self.outstanding <= Decimal('0')


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

class PurchaseReturn(TimeStampModel):
    REFUND_METHOD_CHOICES = [
        ('cash',   'Cash refund'),
        ('credit', 'Credit on outstanding balance'),
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
        ('void', 'Void / shouldn\'t have happened'),
        ('staff_error', 'Staff error'),
        ('other', 'Other'),
    ]
    
    original_purchase = models.ForeignKey(
        Purchase, on_delete=models.PROTECT, related_name='returns')
    
    date = models.DateField(db_index=True)
    reason = models.CharField(max_length=30, choices=REASON_CHOICES, default='defective')
    reason_note = models.CharField(max_length=255, blank=True)
    refund_total = models.DecimalField(max_digits=10, decimal_places=6, default=0)
    refund_method = models.CharField(max_length=20, choices=REFUND_METHOD_CHOICES, default='cash')
    reference = models.CharField(max_length=255, blank=True) # auto-generated PRR-YYYY-NNNN
    
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

        if not self.reference:
            year = timezone.now().year
            last = PurchaseReturn.objects.filter(
                business=self.business, date__year=year
            ).order_by('-reference').first()
            if last and last.reference:
                last_n = int(last.reference.split('-')[-1])
                next_n = last_n + 1
            else:
                next_n = 1
            self.reference = f"PRR-{year}-{next_n:04d}"

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
    
class EmployeeQuerySet(models.QuerySet):
    def total_daily_rate(self):
        return self.aggregate(total_daily_rate=Sum('daily_rate'))['total_daily_rate'] or 0
    
    def average_daily_rate(self):
        return self.aggregate(average_daily_rate=Avg('daily_rate'))['average_daily_rate'] or 0
    
class Employee(TimeStampModel, SlugModel):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='employees')
    staff_user = models.ForeignKey(User, on_delete=models.SET_NULL, related_name='employee_profile', null=True, blank=True)
    business = models.ForeignKey(BusinessProfile, on_delete=models.SET_NULL, related_name='employees', null=True, blank=True)
    name = models.CharField(max_length=255)
    daily_rate = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    is_locked = models.BooleanField(default=False, db_index=True)
    locked_at = models.DateTimeField(null=True, blank=True)
    
    objects = EmployeeQuerySet.as_manager()
    
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


class ShiftEmployee(models.Model):
    shift = models.ForeignKey(Shift, on_delete=models.CASCADE, related_name='shift_employees')
    employee = models.ForeignKey(Employee, on_delete=models.SET_NULL, related_name='shift_employees', null=True, blank=True)
    name = models.CharField(max_length=255) # snapshot
    daily_rate = models.DecimalField(max_digits=10, decimal_places=2)
    
    
    def __str__(self):
        return f"{self.employee}"

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
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='expenses', null=True, blank=True)
    total_amount = models.DecimalField(max_digits=10, decimal_places=2)
    date = models.DateField(db_index=True)
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