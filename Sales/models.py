from django.db import models

from Product.models import Product

from user.models import User

from django.db.models import Sum, Avg

from Expense.models import Employee

from decimal import Decimal

from django.utils import timezone

from core.models import TimeStampModel
from django.core.exceptions import ValidationError

# Create your models here.

class SaleQuerySet(models.QuerySet):
    def total_revenue(self):
        return self.aggregate(total_revenue=Sum('total_revenue'))['total_revenue']

    def average_total_revenue(self):
        return self.aggregate(average_total_revenue=Avg('total_revenue'))['average_total_revenue']
    

class Sale(TimeStampModel):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='sales', null=True, blank=True)
    date = models.DateField(auto_now_add=True, db_index=True)
    total_revenue = models.DecimalField(max_digits=10, decimal_places=6, null=True, blank=True)
    total_salary_cost = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    line_count = models.PositiveIntegerField(default=0)
    reference = models.CharField(max_length=255, null=True, blank=True)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, related_name='created_sales', null=True, blank=True)
    
    objects = SaleQuerySet.as_manager()
    
    def __str__(self):
        return f"Date: {self.date} - {self.total_revenue}"
    
    def quantity_item(self):
        return sum(item.quantity for item in self.sale_items.all())
    
    def save(self, *args, **kwargs):
        if not self.reference:
            year = timezone.now().year
            count = Sale.objects.filter(date__year=year).count() + 1
            self.reference = f"SI-{year}-{count:04d}"
        
        super().save(*args, **kwargs)
        
class SaleItem(models.Model):
    name = models.CharField(max_length=255, null=True, blank=True)
    sale = models.ForeignKey(Sale, on_delete=models.CASCADE, related_name='sale_items', null=True, blank=True)
    product = models.ForeignKey(Product, on_delete=models.SET_NULL, related_name='sale_items', null=True, blank=True)
    price_at_sale = models.DecimalField(max_digits=10, decimal_places=6, null=True, blank=True)
    cost_price = models.DecimalField(max_digits=10, decimal_places=6, default=1.00)
    quantity = models.PositiveIntegerField(default=1)
    unsold_quantity = models.PositiveIntegerField(default=0)

    def __str__(self):
        if self.name:
            return f"{self.name} x {self.quantity}"
        return '-'
    def save(self, *args, **kwargs):
        if not self.price_at_sale:
            self.price_at_sale = self.product.selling_price
            
        if self.product:
            self.name = self.product.name 
        
        
        return super().save(*args, **kwargs)
        
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
    
    
class SaleEmployee(TimeStampModel):
    """
    Tracks which employees worked during a sale session.
    Currently used for labor cost tracking in summary/dashboard.
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