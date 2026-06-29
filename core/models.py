from django.db import models, transaction
from django.utils.text import slugify
from django.conf import settings
from django.core.validators import MinValueValidator, MaxValueValidator

from user.models import BusinessProfile

class TimeStampModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        abstract = True

class SlugModel(models.Model):
    slug = models.SlugField(null=True, blank=True, unique=False, db_index=True)
    
    class Meta:
        abstract = True
    

class Category(SlugModel):
    CATEGORY_TYPE_CHOICES = (
        ('product', 'Product'),
        ('expense', 'Expense'),
        ('material', 'Material')
    )
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, null=True, blank=True)
    name = models.CharField(max_length=100)
    category_type = models.CharField(max_length=100, choices=CATEGORY_TYPE_CHOICES, default='material') # which app
    target_margin = models.PositiveSmallIntegerField(
        null=True, blank=True,
        validators=[MinValueValidator(10), MaxValueValidator(90)],
        help_text="Default profit margin % for products in this category (Product categories only). Blank = global default.",
    )
    
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='created_categories')
    business = models.ForeignKey(BusinessProfile, on_delete=models.SET_NULL, related_name='categories', null=True, blank=True)
    
    class Meta:
        unique_together = ('user', 'slug', 'business')
        
    def __str__(self):
        return f"Category: {self.category_type} - {self.name}"
    
    def save(self, *args, **kwargs):
        # Reserve "No Category"
        if slugify(self.name) == 'no-category' and self.slug != 'no-category':
            from django.core.exceptions import ValidationError
            raise ValidationError('"No Category" is reserved for the system default.')
        
        base_slug = slugify(self.name)  # or whatever name field
        slug = base_slug
        counter = 1
        
        # include business in collision check
        while Category.objects.filter(user=self.user, business=self.business, slug=slug).exclude(id=self.id).exists():
            slug = f"{base_slug}-{counter}"
            counter += 1
        
        self.slug = slug
        
        super().save(*args, **kwargs)
        
    def delete(self, *args, **kwargs):
        if slugify(self.name) == 'no-category':
            from django.core.exceptions import ValidationError
            raise ValidationError("The 'No Category' fallback cannot be deleted.")
        super().delete(*args, **kwargs)

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
    
class KpiSnapshot(models.Model):
    business = models.ForeignKey(BusinessProfile, on_delete=models.CASCADE, related_name='kpi_snapshots')
    date = models.DateField(db_index=True)
    page = models.CharField(max_length=20, choices=[
        ('products',  'Products'),
        ('suppliers', 'Suppliers'),
        ('inventory', 'Inventory'),
        ('sales',     'Sales'),
        ('purchases', 'Purchases'),
        ('dashboard', 'Dashboard'),
    ])
    metrics = models.JSONField(default=dict)  # flexible per page
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        unique_together = ('business', 'date', 'page')
        indexes = [models.Index(fields=['business', 'page', '-date'])]
        
class AbstractDocumentSequence(models.Model):
    """Per-business running serial for BIR-style accountable references.

    RMO 24-2023: each business (its own MIN) keeps a continuous serial run
    (BIR min 6 digits — we use 10), appended with a reset counter. The number
    never resets on its own; `reset_counter` is "if applicable" → cloud/
    non-volatile = stays 1. One row per business.
    """
    business = models.OneToOneField(
        'user.BusinessProfile', on_delete=models.CASCADE, related_name='%(class)s',
    )
    next_number   = models.PositiveBigIntegerField(default=1)
    reset_counter = models.PositiveIntegerField(default=1)

    class Meta:
        abstract = True

    @classmethod
    def issue(cls, business, prefix, width=10):
        """Atomically claim the next serial for `business`.
        Returns (reference, number, reset_counter). Must run inside a
        transaction (sale/purchase finalize already wrap one)."""
        with transaction.atomic():
            seq, _ = cls.objects.select_for_update().get_or_create(business=business)
            number = seq.next_number
            seq.next_number = number + 1
            seq.save(update_fields=['next_number'])
        return f"{prefix}-{number:0{width}d}", number, seq.reset_counter
