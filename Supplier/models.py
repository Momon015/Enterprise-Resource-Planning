from django.db import models
from django.utils.text import slugify

from core.models import TimeStampModel, SlugModel, Category

from user.models import User
# # Create your models here.

class Supplier(TimeStampModel, SlugModel):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='supplies')
    name = models.CharField(max_length=255)

    def __str__(self):
        return self.name
    
    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)
        super().save(*args, **kwargs)

class Material(TimeStampModel, SlugModel):
    # RETAIL — sellable units (sold as-is to customer)
    RETAIL_UNIT_CHOICES = (
        ('pc', 'Piece'),        # single item
        ('pack', 'Pack'),       # small bundle
        ('box', 'Box'),         # larger container
        ('bottle', 'Bottle'),
        ('can', 'Can'),
        ('bag', 'Bag'),
        ('tray', 'Tray'),
        ('dozen', 'Dozen'),     # 12 pcs
        ('bundle', 'Bundle'),   # variable pcs
        ('carton', 'Carton'),   # bulk box
        ('sachet', 'Sachet'),   # small pouch
        ('liter', 'Liter'),
    )
    
    # RESTAURANT — raw ingredients by weight/volume
    RESTAURANT_UNIT_CHOICES = (
        ('kg', 'Kilogram'),
        ('g', 'Gram'),
        ('liter', 'Liter'),
        ('ml', 'Milliliter'),
        ('pc', 'Piece'),        # eggs, onions, etc.
        ('tbsp', 'Tablespoon'),
        ('tsp', 'Teaspoon'),
        ('cup', 'Cup'),
    )
    name = models.CharField(max_length=100)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='materials')
    category = models.ForeignKey(Category, on_delete=models.CASCADE, related_name='materials', null=True, blank=True)
    supplier = models.ForeignKey(Supplier, on_delete=models.CASCADE, related_name='materials')
    price = models.DecimalField(decimal_places=2, max_digits=10)
    quantity = models.PositiveIntegerField(default=1)
    unit = models.CharField(max_length=100, choices=RETAIL_UNIT_CHOICES, default='pc')
    piece_per_unit = models.PositiveBigIntegerField(default=1)
    
    def __str__(self):
        return self.name
    
    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)
        return super().save(*args, **kwargs)
    
class MaterialPreset(TimeStampModel):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='presets')
    name = models.CharField(max_length=255)
    is_active = models.BooleanField(default=False)
    
    def __str__(self):
        return self.name

class MaterialPresetItem(models.Model):
    class Meta:
        unique_together = ('preset', 'material')
        ordering = ['id']
        
    preset = models.ForeignKey(MaterialPreset, on_delete=models.CASCADE, related_name='preset_items')
    material = models.ForeignKey(Material, on_delete=models.CASCADE, related_name='preset_items')
    quantity = models.PositiveIntegerField(default=1)
    discount = models.DecimalField(max_digits=5, decimal_places=2, default=0.00)
    
    def __str__(self):
        return f"{self.material} x {self.quantity} - Discount: {self.discount}"
    
    @property
    def total_line_cost(self):
        return self.material.price * self.quantity
    
