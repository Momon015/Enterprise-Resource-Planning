from django.db import models
from django.utils.text import slugify

class TimeStampModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        abstract = True

class SlugModel(models.Model):
    slug = models.SlugField(null=True, blank=True, unique=True)
    
    class Meta:
        abstract = True
    

class Category(SlugModel):
    CATEGORY_TYPE_CHOICES = (
        ('item', 'Item'),
        ('product', 'Product'),
        ('expense', 'Expense'),
        ('material', 'Material')
    )
    
    name = models.CharField(max_length=100)
    category_type = models.CharField(max_length=100, choices=CATEGORY_TYPE_CHOICES, default='item') # which app
    
    def __str__(self):
        return f"Category: {self.category_type} - {self.name}"
    
    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)
        return super().save(*args, **kwargs)
    
class StatusModel(SlugModel, TimeStampModel):
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('completed', 'Completed'),
        ('paid', 'Paid'),
        ('canceled', 'Canceled'),
    ]
    
    name = models.CharField(max_length=50, choices=STATUS_CHOICES)
    
    
    def __str__(self):
        return self.name
    
    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)
        return super().save(*args, **kwargs)
    
