from django.db import models
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.utils import timezone
from datetime import timedelta

from user.models import User, BusinessProfile

# Create your models here.

class ActivityEvent(models.Model):
    VERB_CHOICES = [
        ('product.created',    'Product created'),
        ('product.updated',    'Product updated'),
        ('product.archived',   'Product archived'),
        ('product.restored',   'Product restored'),
        ('product.margin_low', 'Margin critically low'),
        ('material.created',   'Material created'),
        ('material.updated',   'Material updated'),
        ('material.archived',  'Material archived'),
        ('material.restored',  'Material restored'),
        ('supplier.created',   'Supplier added'),
        ('supplier.updated',   'Supplier updated'),
        ('supplier.archived',  'Supplier archived'),
        ('supplier.restore',   'Supplier restored'),
        ('sale.completed',     'Sale completed'),
        ('sale.reference',     'Sale reference'),
        ('sale.paid',          'Sale payment recorded'),
        ('purchase.recorded',  'Purchase recorded'),
        ('purchase.reference', 'Purchase reference'),
        ('purchase.paid',      'Purchase payment recorded'),
        ('stock.adjusted',     'Stock updated'),
        ('stock.low',          'Low stock alert'),
        ('stock.out',          'Out of stock'),
        ('waste.recorded',     'Waste recorded'),
        ('trial.ending',       'Trial ending soon'),
        ('plan.expired',       'Plan expired'),
        ('plan.canceled',      'Subscription canceled'),
        ('staff.added',        'Staff added'),
        ('purchase.refunded',  'Purchase refunded'),
        ('sale.refunded',      'Sale refunded'),
    ]
    
    business = models.ForeignKey(BusinessProfile, on_delete=models.CASCADE, related_name='activities')
    actor = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='activities')
    verb = models.CharField(max_length=40, choices=VERB_CHOICES, db_index=True)

    target_type = models.ForeignKey(ContentType, on_delete=models.SET_NULL, null=True, blank=True)
    target_id = models.PositiveIntegerField(null=True, blank=True)
    target = GenericForeignKey('target_type', 'target_id')

    description = models.CharField(max_length=255)
    metadata = models.JSONField(default=dict, blank=True)

    is_important = models.BooleanField(default=False, db_index=True)
    is_read = models.BooleanField(default=False, db_index=True)

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['business', '-created_at']),
            models.Index(fields=['business', 'is_important', 'is_read']),
        ]
    
    def __str__(self):
        return f"[{self.business.business_name}] {self.get_verb_display()} — {self.description}"
    
    @property
    def category(self):
        return self.verb.split('.')[0]

    @property
    def icon_class(self):
        if self.verb == 'product.margin_low':
            return 'bi-graph-down-arrow'
        return {
            'product':  'bi-box-seam',
            'material': 'bi-boxes',
            'supplier': 'bi-truck',
            'sale':     'bi-cash-coin',
            'purchase': 'bi-cart3',
            'stock':    'bi-stack',
            'waste':    'bi-trash3',
            'trial':    'bi-clock',
            'plan':     'bi-credit-card',
            'staff':    'bi-person-plus',
        }.get(self.category, 'bi-bell')

    @property
    def tone(self):
        if self.verb in ('stock.low', 'stock.out', 'plan.expired',
                         'sale.refunded', 'purchase.refunded',
                         'waste.recorded', 'product.margin_low'):
            return 'danger'
        if self.verb in ('trial.ending', 'plan.canceled',
                         'product.archived', 'material.archived', 'supplier.archived'):
            return 'warning'
        if self.verb == 'purchase.recorded':
            return 'purple'
        if self.verb == 'sale.completed':
            return 'success'
        if self.verb in ('sale.paid', 'purchase.paid'):
            return 'info'
        return 'info'


    
    @classmethod
    def prune_old(cls, days=7):
        """Delete events older than N days. Call from a daily cron / management command."""
        cutoff = timezone.now() - timedelta(days=days)
        return cls.objects.filter(created_at__lt=cutoff).delete()
    
    def target_url(self, business_slug):
        """Return the most useful detail URL for this event, or None."""
        from django.urls import reverse

        if not self.target_id:
            return None

        try:
            if self.verb in ('sale.completed', 'sale.reference'):
                return reverse('sale-detail', kwargs={'business_slug': business_slug, 'sale_id': self.target_id})

            if self.verb == 'sale.paid':
                # target is SalesPayment — hop to its sale
                from Sales.models import SalesPayment
                sale_id = SalesPayment.objects.only('sale_id').get(id=self.target_id).sale_id
                return reverse('sale-detail', kwargs={'business_slug': business_slug, 'sale_id': sale_id})

            if self.verb == 'sale.refunded':
                return reverse('sales-return-detail', kwargs={'business_slug': business_slug, 'return_id': self.target_id})

            if self.verb in ('purchase.recorded', 'purchase.reference'):
                return reverse('purchase-detail', kwargs={'business_slug': business_slug, 'purchase_id': self.target_id})

            if self.verb == 'purchase.paid':
                from Expense.models import PurchasePayment
                purchase_id = PurchasePayment.objects.only('purchase_id').get(id=self.target_id).purchase_id
                return reverse('purchase-detail', kwargs={'business_slug': business_slug, 'purchase_id': purchase_id})

            if self.verb == 'purchase.refunded':
                return reverse('purchase-return-detail', kwargs={'business_slug': business_slug, 'return_id': self.target_id})

            if self.verb == 'waste.recorded':
                return reverse('material-waste-detail', kwargs={'business_slug': business_slug, 'waste_id': self.target_id})

            if self.verb.startswith('material.'):
                from Supplier.models import Material
                try:
                    m = Material.objects.only('id', 'slug').get(id=self.target_id)
                    return reverse('material-detail', kwargs={
                        'business_slug': business_slug, 'id': m.id, 'slug': m.slug
                    })
                except Material.DoesNotExist:
                    return None

            if self.verb.startswith('product.'):
                from Product.models import Product
                try:
                    p = Product.objects.only('id', 'slug').get(id=self.target_id)
                    return reverse('product-detail', kwargs={
                        'business_slug': business_slug, 'product_id': p.id, 'product_slug': p.slug
                    })
                except Product.DoesNotExist:
                    return None

            # supplier-detail URL is currently disabled in Supplier/urls.py — skip until re-enabled
        except Exception:
            return None

        return None

