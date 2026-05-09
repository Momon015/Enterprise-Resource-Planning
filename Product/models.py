from django.db import models
from django.utils.text import slugify
from core.models import Category, SlugModel, TimeStampModel
from user.models import User, BusinessProfile
from Supplier.models import Material
# Create your models here.

class Product(SlugModel, TimeStampModel):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='products', null=True, blank=True)
    material = models.ForeignKey(Material, on_delete=models.SET_NULL, related_name='products', null=True, blank=True)
    name = models.CharField(max_length=100)
    description = models.TextField(null=True, blank=True, max_length=500)
    category = models.ForeignKey(Category, on_delete=models.SET_NULL, related_name='products', null=True, blank=True)
    cost_price = models.DecimalField(max_digits=10, decimal_places=6, default=0.00)
    prepared_quantity = models.PositiveIntegerField()
    default_quantity = models.PositiveIntegerField(default=0) # preset
    selling_price = models.DecimalField(max_digits=10, decimal_places=6)
    unit = models.CharField(max_length=255, null=True, blank=True)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='created_products')
    business = models.ForeignKey(BusinessProfile, on_delete=models.SET_NULL, related_name='products', null=True, blank=True)
    
    class Meta:
        ordering = ['name']
        unique_together = ('user', 'slug', 'business')
        
    def __str__(self):
        return self.name
    
    def save(self, *args, **kwargs):
        base_slug = slugify(self.name)  # or whatever name field
        slug = base_slug
        counter = 1
        
        # include business in collision check
        while Product.objects.filter(user=self.user, business=self.business, slug=slug).exclude(id=self.id).exists():
            slug = f"{base_slug}-{counter}"
            counter += 1
        
        self.slug = slug
        
        if not self.cost_price:
            self.cost_price = 0
            
        if self.material:
            self.unit = self.material.get_unit_display()
            
        super().save(*args, **kwargs)
        
    def restore_product_quantity(self):
        self.prepared_quantity = self.default_quantity
        self.save()
        

class ProductPreset(TimeStampModel, SlugModel):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='product_presets')
    name = models.CharField(max_length=255, unique=True)
    business = models.ForeignKey(BusinessProfile, on_delete=models.SET_NULL, related_name='product_presets', null=True, blank=True)
    is_active = models.BooleanField(default=False)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, related_name='created_product_presets', null=True, blank=True)
    
    class Meta:
        unique_together = ('user', 'name', 'slug')
        
    def save(self, *args, **kwargs):
        base_slug = slugify(self.name)  # or whatever name field
        slug = base_slug
        counter = 1
        
        # include business in collision check
        while ProductPreset.objects.filter(user=self.user, business=self.business, slug=slug).exclude(id=self.id).exists():
            slug = f"{base_slug}-{counter}"
            counter += 1
        
        self.slug = slug

        super().save(*args, **kwargs)
    
    def __str__(self):
        return f"{self.name}"

class ProductPresetItem(models.Model):
    preset = models.ForeignKey(ProductPreset, on_delete=models.CASCADE, related_name='product_preset_items', null=True, blank=True)
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='product_preset_items', null=True, blank=True)
    cost_price = models.DecimalField(max_digits=10, decimal_places=2)
    quantity = models.PositiveIntegerField(default=0)
    supplier_name = models.CharField(max_length=150, null=True, blank=True) # snapshot
    
    
    class Meta:
        unique_together = ('preset', 'product')
        ordering = ['id'] 
    
    def __str__(self):
        return f"{self.preset.id} - {self.product.name}"
    
    def save(self, *args, **kwargs):
        if not self.supplier_name:
            self.supplier_name = self.product.material.supplier.name if self.product.material.supplier else 'No supplier'
            
        super().save(*args, **kwargs)
    
# class Recipe(TimeStampModel):
#     user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='recipes')
#     created_by = models.ForeignKey(User, on_delete=models.SET_NULL, related_name='recipe', null=True, blank=True)
#     name = models.CharField(max_length=255)
#     material = models.ForeignKey(Material, on_delete=models.SET_NULL, related_name='recipes', null=True, blank=True)
#     cost = models.DecimalField(max_digits=10, decimal_places=6)
#     unit = models.CharField(max_length=255)
    
#     def __str__(self):
#         return self.name
    
#     def save(self, *args, **kwargs):
#         if not self.unit:
#             self.unit = self.material.get_unit_display()
        
        
#         super().save(*args, **kwargs)