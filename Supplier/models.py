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

class Material(TimeStampModel, SlugModel):
    UNIT_CHOICES = (
        ('kg', 'Kilogram'),
        ('pc', 'Piece'),
        ('liter', 'Liter'),
        ('gram', 'Gram')
    )
    name = models.CharField(max_length=100)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='materials')
    category = models.ForeignKey(Category, on_delete=models.CASCADE, related_name='materials', null=True, blank=True)
    supplier = models.ForeignKey(Supplier, on_delete=models.CASCADE, related_name='materials')
    price = models.DecimalField(decimal_places=2, max_digits=10)
    quantity = models.PositiveIntegerField(default=1)
    unit = models.CharField(max_length=100, choices=UNIT_CHOICES, default='pc')

    
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
    
