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
        ('purchase.recorded',  'Purchase recorded'),
        ('purchase.reference', 'Purchase reference'),
        ('stock.adjusted',     'Stock updated'),
        ('stock.low',          'Low stock alert'),
        ('stock.out',          'Out of stock'),
        ('waste.recorded',     'Waste recorded'),
        ('trial.ending',       'Trial ending soon'),
        ('plan.expired',       'Plan expired'),
        ('plan.canceled',      'Subscription canceled'),
        ('staff.added',        'Staff added'),
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
        return {
            'product':  'fa-box',
            'material': 'fa-cube',
            'supplier': 'fa-truck',
            'sale':     'fa-cash-register',
            'purchase': 'fa-shopping-cart',
            'stock':    'fa-warehouse',
            'waste':    'fa-trash',
            'trial':    'fa-clock',
            'plan':     'fa-credit-card',
            'staff':    'fa-user-plus',
        }.get(self.category, 'fa-bell')
        
    @property
    def tone(self):
        if self.verb in ('stock.low', 'stock.out', 'plan.expired'):
            return 'danger'
        if self.verb in ('trial.ending', 'plan.canceled',
                        'product.archived', 'material.archived', 'supplier.archived',
                        'waste.recorded'):
            return 'warning'
        return 'info'

    
    @classmethod
    def prune_old(cls, days=7):
        """Delete events older than N days. Call from a daily cron / management command."""
        cutoff = timezone.now() - timedelta(days=days)
        return cls.objects.filter(created_at__lt=cutoff).delete()