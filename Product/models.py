from django.db import models
from django.utils.text import slugify
from core.models import Category, SlugModel, TimeStampModel
from user.models import User, BusinessProfile
from Supplier.models import Material

from core.utils.images import product_image_path

from decimal import Decimal
from django.core.validators import MinValueValidator, MaxValueValidator
from core.constants import MARGIN_DEFAULT_TARGET, MARGIN_DANGER_FLOOR

# Create your models here.

class ActiveManager(models.Manager):
    def get_queryset(self):
        return super().get_queryset().filter(is_active=True)
    
class GoodsManager(models.Manager):
    """Active merchandise only — excludes service fees. Use for catalog + analytics."""
    def get_queryset(self):
        return super().get_queryset().filter(is_active=True, is_service=False)

class ServiceManager(models.Manager):
    """Active service fees only (xerox, GCash, bills payment)."""
    def get_queryset(self):
        return super().get_queryset().filter(is_active=True, is_service=True)


class Product(SlugModel, TimeStampModel):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='products', null=True, blank=True)
    material = models.ForeignKey(Material, on_delete=models.SET_NULL, related_name='products', null=True, blank=True)
    image = models.ImageField(upload_to=product_image_path, null=True, blank=True)
    image_original_name = models.CharField(max_length=255, blank=True)
    name = models.CharField(max_length=100)
    sku = models.CharField(max_length=64, blank=True, db_index=True)
    barcode = models.CharField(max_length=64, null=True, blank=True, db_index=True)
    description = models.TextField(null=True, blank=True, max_length=500)
    category = models.ForeignKey(Category, on_delete=models.SET_NULL, related_name='products', null=True, blank=True)
    cost_price = models.DecimalField(max_digits=10, decimal_places=6, default=0.00)
    prepared_quantity = models.PositiveIntegerField()
    default_quantity = models.PositiveIntegerField(default=0) # preset
    selling_price = models.DecimalField(max_digits=10, decimal_places=6) 
    low_stock_threshold  = models.PositiveIntegerField(default=25, help_text="Alert when stock drops to or below this level")
    high_stock_threshold = models.PositiveIntegerField(default=50, help_text="Considered fully stocked at or above this level")
    target_margin = models.PositiveSmallIntegerField(
        null=True, blank=True,
        validators=[MinValueValidator(10), MaxValueValidator(90)],
        help_text="Desired profit margin % for this product. Blank = use category, then global default.",
    )

    unit = models.CharField(max_length=255, null=True, blank=True)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='created_products')
    business = models.ForeignKey(BusinessProfile, on_delete=models.SET_NULL, related_name='products', null=True, blank=True)
    is_locked = models.BooleanField(default=False, db_index=True)
    locked_at = models.DateTimeField(null=True, blank=True)
    is_service = models.BooleanField(default=False, db_index=True)
    is_active = models.BooleanField(default=True)
    is_session_based = models.BooleanField(default=False)   # rental w/ time-block tiers

    
    objects = ActiveManager()
    goods = GoodsManager()
    services = ServiceManager()
    all_objects = models.Manager()
            
    class Meta:
        ordering = ['name']
        unique_together = ('user', 'slug', 'business')
        constraints = [
            models.UniqueConstraint(
                fields=['business', 'sku'],
                condition=models.Q(sku__gt=''),
                name='unique_sku_per_business',
            ),
            models.UniqueConstraint(
                fields=['business', 'barcode'],
                condition=models.Q(barcode__isnull=False) & ~models.Q(barcode=''),
                name='unique_barcode_per_business',
            ),
        ]

        
    def __str__(self):
        return self.name
    
    def save(self, *args, **kwargs):
        if not self.sku and self.business:
            last = Product.all_objects.filter(business=self.business).exclude(sku='').order_by('-id').first()
            next_num = 1
            if last and last.sku.startswith('PRD-'):
                try:
                    next_num = int(last.sku.rsplit('-', 1)[-1]) + 1
                except (ValueError, IndexError):
                    next_num = Product.all_objects.filter(business=self.business).count() + 1
            self.sku = f"PRD-{next_num:04d}"
        
        base_slug = slugify(self.name)  # or whatever name field
        slug = base_slug
        counter = 1
        
        # include business in collision check
        while Product.all_objects.filter(user=self.user, business=self.business, slug=slug).exclude(id=self.id).exists():
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
        
    @property
    def effective_target_margin(self):
        """Resolved target %: product override → category → global default.
        Assumes select_related('category') to stay query-free."""
        if self.target_margin is not None:
            return self.target_margin
        if self.category and self.category.target_margin is not None:
            return self.category.target_margin
        return MARGIN_DEFAULT_TARGET

    @property
    def profit_per_piece(self):
        return self.selling_price - self.cost_price

    @property
    def current_margin(self):
        """Actual margin % = (price - cost) / price * 100. None if no price."""
        if self.is_service:
            return None
        if not self.selling_price or self.selling_price <= 0:
            return None
        return round((self.selling_price - self.cost_price) / self.selling_price * 100, 1)

    @property
    def margin_status(self):
        """'good' | 'warning' | 'critical' | None — vs effective target & danger floor."""
        m = self.current_margin
        if m is None:
            return None
        if m < MARGIN_DANGER_FLOOR:
            return 'critical'
        if m < self.effective_target_margin:
            return 'warning'
        return 'good'

    @property
    def suggested_price(self):
        """Price that hits the effective target: price = cost / (1 - target/100)."""
        target = Decimal(self.effective_target_margin) / Decimal(100)
        if target >= 1 or not self.cost_price:
            return None
        return (self.cost_price / (Decimal(1) - target)).quantize(Decimal('0.01'))

class ServiceSession(models.Model):
    """A priced time-block for a session-based rental service (billiards, court).
    Flat fees (xerox, GCash) have none and use Product.selling_price."""
    service = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='sessions')
    label = models.CharField(max_length=50)        # "30 mins", "1 hr", "1.5 hrs"
    minutes = models.PositiveIntegerField(null=True, blank=True) 
    price = models.DecimalField(max_digits=10, decimal_places=2)
    sort_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ['sort_order', 'minutes', 'id']

    def __str__(self):
        return f"{self.service.name} · {self.label}"

        
class ProductPreset(TimeStampModel, SlugModel):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='product_presets')
    name = models.CharField(max_length=255)
    business = models.ForeignKey(BusinessProfile, on_delete=models.SET_NULL, related_name='product_presets', null=True, blank=True)
    is_active = models.BooleanField(default=False)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, related_name='created_product_presets', null=True, blank=True)
    is_locked = models.BooleanField(default=False, db_index=True)
    locked_at = models.DateTimeField(null=True, blank=True)
    
    class Meta:
        unique_together = ('business', 'name')
        
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
    cost_price = models.DecimalField(max_digits=10, decimal_places=6)
    quantity = models.PositiveIntegerField(default=0)
    supplier_name = models.CharField(max_length=150, null=True, blank=True) # snapshot
    
    
    class Meta:
        unique_together = ('preset', 'product')
        ordering = ['id'] 
    
    def __str__(self):
        return f"{self.preset.id} - {self.product.name}"
    
    def save(self, *args, **kwargs):
        self.supplier_name = (
            self.product.material.supplier.name
            if self.product.material and self.product.material.supplier
            else 'No supplier'
        )

            
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