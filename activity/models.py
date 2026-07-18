from django.db import models
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.utils import timezone
from datetime import timedelta

from user.models import User, BusinessProfile
from django.core.serializers.json import DjangoJSONEncoder

# Create your models here.

class ActivityEvent(models.Model):
    VERB_CHOICES = [
        ('product.created',    'Product created'),
        ('product.updated',    'Product updated'),
        ('product.archived',   'Product archived'),
        ('product.restored',   'Product restored'),
        ('product.margin_low', 'Profit margin dropped'),
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
        ('sale.voided',        'Sale voided'),
        ('purchase.recorded',  'Purchase recorded'),
        ('purchase.reference', 'Purchase reference'),
        ('purchase.paid',      'Purchase payment recorded'),
        ('purchase.voided',    'Purchase voided'),
        ('stock.adjusted',     'Stock updated'),
        ('stock.low',          'Low stock alert'),
        ('stock.critical',     'Critically low stock'),
        ('stock.out',          'Out of stock'),
        ('waste.recorded',     'Waste recorded'),
        ('trial.ending',       'Trial ending soon'),
        ('plan.expired',       'Plan expired'),
        ('plan.canceled',      'Subscription canceled'),
        ('plan.expiring',      'Subscription ending soon'),
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
        # margin_low fires at TWO severities (below target vs near-zero profit), so its
        # tone follows the event's own metadata rather than the verb. Below target is a
        # warning; barely-above-cost is a danger. Old rows have no 'status' key — they
        # predate the split and were all critical-only, hence the 'critical' default.
        if self.verb == 'product.margin_low':
            return 'danger' if self.metadata.get('status', 'critical') == 'critical' else 'warning'

        if self.verb in ('stock.out', 'stock.critical', 'plan.expired',
                         'sale.refunded', 'purchase.refunded',
                         'waste.recorded'):
            return 'danger'

        if self.verb in ('stock.low','trial.ending', 'plan.expiring', 'plan.canceled',
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
            if self.verb.startswith('stock.'):
                model = self.target_type.model if self.target_type else None
                if model == 'product':
                    from Product.models import Product
                    try:
                        p = Product.objects.only('id', 'slug').get(id=self.target_id)
                        return reverse('product-detail', kwargs={
                            'business_slug': business_slug, 'product_id': p.id, 'product_slug': p.slug,
                        })
                    except Product.DoesNotExist:
                        return None
                if model == 'stock':
                    from Inventory.models import Stock
                    try:
                        s = Stock.objects.select_related('material').get(id=self.target_id)
                    except Stock.DoesNotExist:
                        return None
                    if s.material_id:
                        return reverse('material-detail', kwargs={
                            'business_slug': business_slug, 'id': s.material.id, 'slug': s.material.slug,
                        })
                    return None
                return None

            if self.verb == 'staff.added':
                return reverse('employee-list', kwargs={'business_slug': business_slug})
            
            if self.verb in ('sale.completed', 'sale.reference', 'sale.voided'):
                return reverse('sale-detail', kwargs={'business_slug': business_slug, 'sale_id': self.target_id})

            if self.verb == 'sale.paid':
                # target is SalesPayment — hop to its sale
                from Sales.models import SalesPayment
                sale_id = SalesPayment.objects.only('sale_id').get(id=self.target_id).sale_id
                return reverse('sale-detail', kwargs={'business_slug': business_slug, 'sale_id': sale_id})

            if self.verb == 'sale.refunded':
                return reverse('sales-return-detail', kwargs={'business_slug': business_slug, 'return_id': self.target_id})

            if self.verb in ('purchase.recorded', 'purchase.reference', 'purchase.voided'):
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
    
from django.core.serializers.json import DjangoJSONEncoder

class AuditLog(models.Model):
    """Append-only legal/BIR audit trail. Never edited, never pruned (~10yr).
    Distinct from ActivityEvent (UX feed, self-prunes, no before/after values).
    Generalizes the OpeningCashChange 'pen-not-pencil' pattern."""

    ACTION_CHOICES = [
        ('create',  'Created'),
        ('void',    'Voided'),
        ('return',  'Returned'),
        ('adjust',  'Adjusted'),
        ('payment', 'Payment recorded'),
        ('edit',    'Edited'),
    ]

    business     = models.ForeignKey(BusinessProfile, on_delete=models.SET_NULL,
                       null=True, blank=True, related_name='audit_logs')
    actor        = models.ForeignKey(User, on_delete=models.SET_NULL,
                       null=True, blank=True, related_name='audit_logs')
    action       = models.CharField(max_length=20, choices=ACTION_CHOICES, db_index=True)

    target_model = models.CharField(max_length=50, db_index=True)      # 'Sale', 'Purchase'…
    target_id    = models.PositiveIntegerField(null=True, blank=True)
    target_ref   = models.CharField(max_length=100, blank=True)        # 'SI-2026-0005' snapshot

    old_values   = models.JSONField(default=dict, blank=True, encoder=DjangoJSONEncoder)
    new_values   = models.JSONField(default=dict, blank=True, encoder=DjangoJSONEncoder)
    reason       = models.CharField(max_length=255, blank=True)

    created_at   = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['business', '-created_at']),
            models.Index(fields=['target_model', 'target_id']),
        ]

    def __str__(self):
        return f"{self.get_action_display()} {self.target_model} {self.target_ref}"

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValueError("AuditLog is append-only — existing rows cannot be modified.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError("AuditLog is append-only — rows cannot be deleted.")

class DailyClose(models.Model):
    """BIR-style frozen accrual snapshot of one business-day's books.
    Created lazily the first time a PAST day is read (day-rollover freeze,
    NEVER shift clock-out). Append-only: once a day closes its figures can
    never change — corrections post to the current day as Adjustments.
    Cash Flow is NOT frozen here (it stays live by payment date)."""

    business = models.ForeignKey(BusinessProfile, on_delete=models.CASCADE,
                                 related_name='daily_closes')
    date     = models.DateField(db_index=True)

    total_revenue       = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    # COST OF GOODS SOLD (2026-07-13) — what net_profit subtracts. Frozen alongside the
    # rest so a closed day can be re-read exactly as it was booked, even if a product's
    # cost is edited later. `total_material_cost` (what we PAID suppliers that day) is KEPT
    # — it's still real, the Cash Flow lens uses it — it just no longer drives profit.
    total_cogs          = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    total_material_cost = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    total_salary_cost   = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    total_waste_cost    = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    total_expense_cost  = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    net_profit          = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    closed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-date']
        constraints = [
            models.UniqueConstraint(fields=['business', 'date'],
                                    name='uniq_dailyclose_business_date'),
        ]
        indexes = [models.Index(fields=['business', '-date'])]

    def __str__(self):
        return f"Close {self.business} {self.date} — net {self.net_profit}"

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValueError("DailyClose is append-only — a closed day cannot be modified.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError("DailyClose is append-only — a closed day cannot be reopened.")

