from django.db import models
from django.utils.text import slugify
from core.models import Category, SlugModel, TimeStampModel
from user.models import User
from Supplier.models import Material
# Create your models here.

class Product(SlugModel, TimeStampModel):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='products', null=True, blank=True)
    material = models.ForeignKey(Material, on_delete=models.SET_NULL, related_name='products', null=True, blank=True)
    name = models.CharField(max_length=100)
    description = models.TextField(null=True, blank=True)
    category = models.ForeignKey(Category, on_delete=models.SET_NULL, related_name='products', null=True, blank=True)
    cost_price = models.DecimalField(max_digits=10, decimal_places=6, default=0.00)
    prepared_quantity = models.PositiveIntegerField()
    default_quantity = models.PositiveIntegerField(default=0) # preset
    selling_price = models.DecimalField(max_digits=10, decimal_places=6)
    unit = models.CharField(max_length=255, null=True, blank=True)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='created_products')
    
    class Meta:
        ordering = ['name']
        
    def __str__(self):
        return self.name
    
    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)
        if not self.cost_price:
            self.cost_price = 0
            
        if self.material:
            self.unit = self.material.get_unit_display()
            
        super().save(*args, **kwargs)
        
    def restore_product_quantity(self):
        self.prepared_quantity = self.default_quantity
        self.save()
        

class ProductPreset(TimeStampModel):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='product_presets')
    name = models.CharField(max_length=255, unique=True)
    is_active = models.BooleanField(default=False)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, related_name='created_product_presets', null=True, blank=True)
    
    def __str__(self):
        return f"{self.name}"


class ProductPresetItem(models.Model):
    preset = models.ForeignKey(ProductPreset, on_delete=models.CASCADE, related_name='product_preset_items', null=True, blank=True)
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='product_preset_items', null=True, blank=True)
    cost_price = models.DecimalField(max_digits=10, decimal_places=2)
    quantity = models.PositiveIntegerField(default=0)
    
    class Meta:
        unique_together = ('preset', 'product')
        ordering = ['id'] 
    
    def __str__(self):
        return f"{self.preset.id} - {self.product.name}"