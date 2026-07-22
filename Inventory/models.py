from django.db import models
from django.db.models import Q
from django.utils.text import slugify

from core.models import TimeStampModel, SlugModel, Category
from core.constants import LOW_STOCK_THRESHOLD, CRITICAL_STOCK_THRESHOLD
from Supplier.models import Material
from user.models import User, BusinessProfile

# Create your models here.

# Material-stock bands, DISJOINT — the Stock twin of Product's CRITICAL_BAND_Q / LOW_BAND_Q.
# Stock has no per-item threshold, so these use the globals (see core/constants.py).
#   critical  1 .. CRITICAL          low  CRITICAL+1 .. LOW      (low EXCLUDES critical)
STOCK_CRITICAL_Q = Q(quantity__gte=1, quantity__lte=CRITICAL_STOCK_THRESHOLD)
STOCK_LOW_Q      = Q(quantity__gt=CRITICAL_STOCK_THRESHOLD,
                     quantity__lte=LOW_STOCK_THRESHOLD)

class Stock(TimeStampModel):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='stocks', null=True, blank=True)
    name = models.CharField(max_length=255, null=True, blank=True)
    slug = models.SlugField(max_length=255, null=True, blank=True)
    material = models.ForeignKey(Material, on_delete=models.SET_NULL, related_name='stocks', null=True, blank=True)
    price = models.DecimalField(decimal_places=6, max_digits=16)
    quantity = models.PositiveIntegerField(default=0)
    supplier = models.CharField(max_length=255, null=True, blank=True)
    unit = models.CharField(max_length=255, null=True, blank=True)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='created_stocks')
    business = models.ForeignKey(BusinessProfile, on_delete=models.SET_NULL, related_name='stocks', null=True, blank=True)
    
    class Meta:
        unique_together = ('user', 'business', 'material')
        
    
    def __str__(self):
        if self.name:
            return self.name
        return '-'
    
    @property
    def total_value(self):
        return self.price * self.quantity

    @property
    def stock_status(self):
        """'out' | 'critical' | 'low' | 'ok' — the Stock twin of Product.stock_status.

        Exists so TEMPLATES stop hand-rolling their own comparisons. The row badge
        used to test `quantity <= 25` inline: the literal instead of the constant,
        and only THREE bands, so a critical row rendered as "Low stock" while the
        KPI card beside it counted it as Critically Low (fixed 2026-07-20).

        Must agree with STOCK_CRITICAL_Q / STOCK_LOW_Q above — same thresholds,
        same disjoint boundaries — so what a card COUNTS and what a row SAYS can
        never drift apart.
        """
        if self.quantity is None:
            return None
        if self.quantity <= 0:
            return 'out'
        if self.quantity <= CRITICAL_STOCK_THRESHOLD:
            return 'critical'
        if self.quantity <= LOW_STOCK_THRESHOLD:
            return 'low'
        return 'ok'
    
    def save(self, *args, **kwargs):
        base_slug = slugify(self.name)  # or whatever name field
        slug = base_slug
        counter = 1
        
        # include business in collision check
        while Stock.objects.filter(user=self.user, business=self.business, slug=slug).exclude(id=self.id).exists():
            slug = f"{base_slug}-{counter}"
            counter += 1
        
        self.slug = slug
        
        if self.material:
            self.name = self.material.name
        
        if not self.supplier:
            self.supplier = self.material.supplier.name if self.material.supplier else 'No supplier'
            
        if self.material:
            self.unit = self.material.get_unit_display()

        super().save(*args, **kwargs)