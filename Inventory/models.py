from django.db import models
from django.utils.text import slugify

from core.models import TimeStampModel, SlugModel, Category
from Supplier.models import Material
from user.models import User

# Create your models here.

class Stock(TimeStampModel):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='stocks', null=True, blank=True)
    name = models.CharField(max_length=255, null=True, blank=True)
    material = models.ForeignKey(Material, on_delete=models.SET_NULL, related_name='stocks', null=True, blank=True)
    price = models.DecimalField(decimal_places=6, max_digits=10)
    quantity = models.PositiveIntegerField(default=0)
    supplier = models.CharField(max_length=255, null=True, blank=True)
    unit = models.CharField(max_length=255, null=True, blank=True)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='created_stocks')
    
    def __str__(self):
        if self.name:
            return self.name
        return '-'
    
    @property
    def total_value(self):
        return self.price * self.quantity
    
    def save(self, *args, **kwargs):
        if self.material:
            self.name = self.material.name
        
        if self.material and self.material.supplier:
            self.supplier = self.material.supplier.name
            
        if self.material:
            self.unit = self.material.get_unit_display()

        super().save(*args, **kwargs)