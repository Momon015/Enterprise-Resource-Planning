from django.db import models
from django.utils.text import slugify
from django.conf import settings

class TimeStampModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        abstract = True

class SlugModel(models.Model):
    slug = models.SlugField(null=True, blank=True, unique=False)
    
    class Meta:
        abstract = True
    

class Category(SlugModel):
    CATEGORY_TYPE_CHOICES = (
        ('item', 'Item'),
        ('product', 'Product'),
        ('expense', 'Expense'),
        ('material', 'Material')
    )
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, null=True, blank=True)
    name = models.CharField(max_length=100)
    category_type = models.CharField(max_length=100, choices=CATEGORY_TYPE_CHOICES, default='item') # which app
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='created_categories')
    
    class Meta:
        unique_together = ('user', 'slug')
        
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
    
