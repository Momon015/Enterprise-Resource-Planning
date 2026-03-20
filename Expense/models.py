from django.db import models
from django.utils.text import slugify

from core.models import TimeStampModel, SlugModel, Category, StatusModel
from Supplier.models import Material

from django.utils import timezone

from django.db.models import Sum, Avg, Value

from django.db.models.functions import Coalesce
from decimal import Decimal

from user.models import User

from Product.models import Product
from django.db.models import F
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
    purchase_date = models.DateField(auto_now_add=True, null=True, db_index=True) # remove NULL when you reset the DB
    reference = models.CharField(max_length=255, null=True, blank=True)
    # save the custom queryset as_manager()
    objects = PurchaseQuerySet.as_manager()
    
    def __str__(self):
        return f"Purchase ID: #{self.id} - {self.formatted_date}, Total Cost: {self.total_cost}"
    
    def save(self, *args, **kwargs):
        if self.status and self.status.slug == 'paid':
            self.is_paid = True
            
        if not self.reference:
            year = timezone.now().year
            count = Purchase.objects.filter(purchase_date__year=year).count() + 1
            self.reference = f"PO-{year}-{count:04d}"
            
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

class PurchaseItem(TimeStampModel):
    purchase = models.ForeignKey(Purchase, on_delete=models.SET_NULL, related_name='materials', null=True)
    material = models.ForeignKey(Material, on_delete=models.CASCADE, related_name='items')
    discount = models.DecimalField(max_digits=5, decimal_places=2, default=0.00)
    quantity = models.PositiveIntegerField(default=1)
    price = models.DecimalField(max_digits=10, decimal_places=6, default=0.00)
    
    def __str__(self):
        return f"{self.material.name} - ({self.material.price} x {self.quantity}) - {self.discount} = {self.total_item_discount}"

    @property
    def material_price(self):
        return self.material.price
    
    @property
    def total_price_per_item(self):
        return self.price * self.quantity
    
    @property
    def total_item_discount(self):
        return self.total_price_per_item - self.discount
    
    # def material_discount(self):
    #     if self.discount:
    #         return self.total_price_per_item - self.discount
    #     return self.total_price_per_item

class EmployeeQuerySet(models.QuerySet):
    def total_daily_rate(self):
        return self.aggregate(total_daily_rate=Sum('daily_rate'))['total_daily_rate'] or 0
    
class Employee(TimeStampModel):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='employees')
    name = models.CharField(max_length=255, unique=True)
    daily_rate = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    
    objects = EmployeeQuerySet.as_manager()

class WasteQuerySet(models.QuerySet):
    def total_waste_cost(self):
        return self.aggregate(total_waste_cost=Sum('total_cost'))['total_waste_cost'] or 0

class Waste(TimeStampModel):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='wastes')
    date = models.DateField(auto_now_add=True, db_index=True)
    total_cost = models.DecimalField(max_digits=10, decimal_places=6)
    
    objects = WasteQuerySet.as_manager()
    
    def __str__(self):
        return f"Waste - {self.date}"
    
    @property
    def waste_cost(self):
        return sum(item.price * item.quantity for item in self.waste_items.all())
        
class WasteItemQuerySet(models.QuerySet):
    def total_product_waste(self):
        return self.filter(product__isnull=False).aggregate(total_product_waste=Sum(F('price') * F('quantity')))['total_product_waste'] or 0

    def total_material_waste(self):
        return self.filter(material__isnull=False).aggregate(total_material_waste=Sum(F('price') * F('quantity')))['total_material_waste'] or 0
    
class WasteItem(models.Model):
    waste = models.ForeignKey(Waste, on_delete=models.CASCADE, related_name='waste_items', null=True, blank=True)
    material = models.ForeignKey(Material, on_delete=models.CASCADE, related_name='waste_items', null=True, blank=True)
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='waste_items', null=True, blank=True)
    price = models.DecimalField(max_digits=10, decimal_places=6)
    quantity = models.PositiveBigIntegerField(default=0)
    
    objects = WasteItemQuerySet.as_manager()
    
    def __str__(self):
        item = self.material or self.product
        return f"{item.name} - {self.quantity}"
    
    # def save(self, *args, **kwargs):
    #     # if self.product and not self.material:
    #     #     self.price = self.product.cost_price
    #     # elif self.material and not self.product:
    #     #     self.price = self.material.price
        
    #     super().save(*args, **kwargs)
        
    @property
    def total_product_cost(self):
        return self.price * self.quantity
    
    @property
    def total_material_cost(self):
        return self.price * self.quantity