from django.db import models
from django.utils.text import slugify

from core.models import TimeStampModel, SlugModel, Category

from user.models import User, BusinessProfile

from core.utils.images import supplier_image_path

from user.models import phone_validators
from django.core.exceptions import ValidationError

# # Create your models here.

STATUS_CHOICES = [
    ('active', 'Active'),
    ('on_hold', 'On hold') ,
    ('inactive', 'Inactive'), # acts as archive

]

class ActiveManager(models.Manager):
    """Default queryset hides 'inactive' suppliers — they're effectively archived."""
    def get_queryset(self):
        return super().get_queryset().exclude(status='inactive')

class Supplier(TimeStampModel, SlugModel):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='suppliers', null=True, blank=True)
    name = models.CharField(max_length=255)
    image = models.ImageField(upload_to=supplier_image_path, null=True, blank=True)
    image_original_name = models.CharField(max_length=255, blank=True)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, related_name='created_supplies', null=True, blank=True)
    business = models.ForeignKey(BusinessProfile, on_delete=models.SET_NULL, related_name='suppliers', null=True, blank=True)
    is_locked = models.BooleanField(default=False, db_index=True)
    locked_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, db_index=True, default='active')
    contact_number = models.CharField(max_length=11, validators=[phone_validators], null=True, blank=True)
    email = models.EmailField(null=True, blank=True)
    
    objects = ActiveManager()
    all_objects = models.Manager()
    
    class Meta:
        unique_together = ('user', 'slug', 'business')
        constraints = [
            models.UniqueConstraint(
                fields=['business', 'email'],
                condition=models.Q(email__isnull=False) & ~models.Q(email=''),
                name='unique_email_per_business',
            ),
        ]
        
    def __str__(self):
        return self.name
    
    def save(self, *args, **kwargs): 
        # Reserve for 'No supplier'
        if slugify(self.name) == 'no-supplier' and self.slug != 'no-supplier':
            from django.core.exceptions import ValidationError
            raise ValidationError('"No Supplier" is reserved for the system default.')
        
        base_slug = slugify(self.name)  # or whatever name field
        slug = base_slug
        counter = 1
        
        # include business in collision check
        while Supplier.objects.filter(user=self.user, business=self.business, slug=slug).exclude(id=self.id).exists():
            slug = f"{base_slug}-{counter}"
            counter += 1
        
        self.slug = slug
        
        super().save(*args, **kwargs)
    
    def delete(self, *args, **kwargs):
        if self.name.lower() == 'no supplier':
            from django.core.exceptions import ValidationError
            raise ValidationError("The 'No Supplier' fallback cannot be deleted.")
        super().delete(*args, **kwargs)
        
    def clean(self):
        super().clean()

        # Only validate when transitioning TO inactive
        if self.status != 'inactive':
            return

        # Skip on first create — no materials yet
        if not self.pk:
            return

        # Single query: any active material with stock > 0?
        from Inventory.models import Stock
        blocked_stocks = Stock.objects.filter(
            material__supplier=self,
            material__status='active',
            quantity__gt=0,
        ).select_related('material')

        if blocked_stocks.exists():
            material_names = ", ".join(
                blocked_stocks.values_list('material__name', flat=True).distinct()[:5]
            )
            count = blocked_stocks.count()
            raise ValidationError({
                'status': (
                    f"Cannot mark this supplier inactive — "
                    f"{count} material(s) still have stock on hand "
                    f"({material_names}{'...' if count > 5 else ''}). "
                    f"Use up or waste the stock before marking inactive."
                )
            })

class ActiveManager(models.Manager):
    def get_queryset(self):
        return super().get_queryset().exclude(status='inactive')


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
    category = models.ForeignKey(Category, on_delete=models.SET_NULL, related_name='materials', null=True, blank=True)
    supplier = models.ForeignKey(Supplier, on_delete=models.SET_NULL, related_name='materials', null=True, blank=True)
    price = models.DecimalField(decimal_places=2, max_digits=10)
    quantity = models.PositiveIntegerField(default=1)
    unit = models.CharField(max_length=100, choices=RETAIL_UNIT_CHOICES, default='pc')
    piece_per_unit = models.PositiveBigIntegerField(default=1)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, related_name='created_materials', null=True, blank=True)
    business = models.ForeignKey(BusinessProfile, on_delete=models.SET_NULL, related_name='materials', null=True, blank=True)
    is_locked = models.BooleanField(default=False, db_index=True)
    locked_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='active')
    
    objects = ActiveManager()
    all_objects = models.Manager()
    
    class Meta:
        unique_together = ('user', 'slug', 'business')
        
    def __str__(self):
        return self.name
    
    def save(self, *args, **kwargs):
        unit_display = self.get_unit_display().title()
        base_slug = f"{slugify(self.name)}-{unit_display}"
        slug = base_slug
        counter = 1
        
        # include business in collision check
        while Material.all_objects.filter(user=self.user, business=self.business, slug=slug).exclude(id=self.id).exists():
            slug = f"{base_slug}-{counter}"
            counter += 1
        
        self.slug = slug
        
        
        super().save(*args, **kwargs)
        
    @property
    def is_multi_unit(self):
        return self.unit in {'pack', 'box', 'tray', 'dozen', 'bundle', 'carton', 'sachet'}

    
class MaterialPreset(TimeStampModel, SlugModel):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='presets')
    name = models.CharField(max_length=255)
    business = models.ForeignKey(BusinessProfile, on_delete=models.SET_NULL, related_name='presets', null=True, blank=True)
    is_active = models.BooleanField(default=False)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, related_name='created_material_presets', null=True, blank=True)
    is_locked = models.BooleanField(default=False, db_index=True)
    locked_at = models.DateTimeField(null=True, blank=True)
    
    class Meta:
        unique_together = ('business', 'name')
        
    def save(self, *args, **kwargs):
        base_slug = slugify(self.name)  # or whatever name field
        slug = base_slug
        counter = 1
        
        # include business in collision check
        while MaterialPreset.objects.filter(user=self.user, business=self.business, slug=slug).exclude(id=self.id).exists():
            slug = f"{base_slug}-{counter}"
            counter += 1
        
        self.slug = slug

        super().save(*args, **kwargs)
    
    def __str__(self):
        return self.name

class MaterialPresetItem(models.Model):
    preset = models.ForeignKey(MaterialPreset, on_delete=models.CASCADE, related_name='preset_items')
    material = models.ForeignKey(Material, on_delete=models.CASCADE, related_name='preset_items')
    quantity = models.PositiveIntegerField(default=1)
    discount = models.DecimalField(max_digits=5, decimal_places=2, default=0.00)
    supplier_name = models.CharField(max_length=150, null=True, blank=True) # snapshot
    
    class Meta:
        unique_together = ('preset', 'material')
        ordering = ['id']
    
    def __str__(self):
        return f"{self.material} x {self.quantity} - Discount: {self.discount}"
    
    def save(self, *args, **kwargs):
        if not self.supplier_name:
            self.supplier_name = self.material.supplier.name if self.material.supplier else 'No supplier'
            
        super().save(*args, **kwargs)
    
    @property
    def total_line_cost(self):
        return self.material.price * self.quantity
    
