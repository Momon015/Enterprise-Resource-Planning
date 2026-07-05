from django.db import models, transaction
from django.utils import timezone
from datetime import timedelta
from decimal import Decimal, ROUND_HALF_UP, ROUND_DOWN

from user.models import User
import secrets


# ── Plan Limits ──────────────────────────────────────────────────────────────

PLAN_LIMITS = {
    'free': {
        'max_staff':            0,
        'max_products':         None,
        'max_materials':        None,
        'max_suppliers':        2,
        'max_sales':            10,
        'max_purchases':        10,
        'max_waste':            10,
        'max_expenses':         5,
        'max_product_presets':  2,
        'max_material_presets': 2,
        'receipt_print':        False,
        'timecards':            False,
        'cash_reconciliation':  False,
        'dashboard':            False,
        'analytics_access':        'none',
    },
    'standard': {
        'max_staff':            1,
        'max_products':         None,
        'max_materials':        None,
        'max_suppliers':        5,
        'max_sales':            30,
        'max_purchases':        30,
        'max_waste':            30,
        'max_expenses':         30,
        'max_product_presets':  5,
        'max_material_presets': 5,
        'receipt_print':        False,
        'timecards':            True,
        'cash_reconciliation':  True,
        'dashboard':            False,
        'analytics_access':        'none',
    },
    'premium': {
        'max_staff':            5,
        'max_products':         None,
        'max_materials':        None,
        'max_suppliers':        10,
        'max_sales':            None,
        'max_purchases':        None,
        'max_waste':            None,
        'max_expenses':         None,
        'max_product_presets':  None,
        'max_material_presets': None,
        'receipt_print':        True,
        'timecards':            True,
        'cash_reconciliation':  True,
        'dashboard':            False,
        'analytics_access':        'monthly + daily',
    },
    'pro': {
        'max_staff':            10,
        'max_products':         None,
        'max_materials':        None,
        'max_suppliers':        None,
        'max_sales':            None,
        'max_purchases':        None,
        'max_waste':            None,
        'max_expenses':         None,
        'max_product_presets':  None,
        'max_material_presets': None,
        'receipt_print':        True,
        'timecards':            True,
        'cash_reconciliation':  True,
        'dashboard':            True,
        'analytics_access':        'daily + monthly + weekly',
    },
}


LOCKABLE_LIMIT_KEYS = {
    'Product':        'max_products',
    'Material':       'max_materials',
    'Supplier':       'max_suppliers',
    'Employee':       'max_staff',
    'ProductPreset':  'max_product_presets',
    'MaterialPreset': 'max_material_presets',
}


PLAN_CHOICES = [
    ('free',     'Free'),
    ('standard', 'Standard'),
    ('premium',  'Premium'),
    ('pro',      'Pro'),
]

BUNDLE_CHOICES = [
    ('single',  'Single Business'),
    ('dual',    '2 Businesses'),
    ('triple',  '3 Businesses'),
]

BILLING_CHOICES = [
    ('monthly', 'Monthly'),
    ('yearly',  'Yearly'),
]

BUNDLE_COUNT = {'single': 1, 'dual': 2, 'triple': 3}

FOUNDER_SLOTS_TOTAL = 10

FOUNDER_BASE = {
    'free':     Decimal('0'),
    'standard': Decimal('300'),
    'premium':  Decimal('800'),
    'pro':      Decimal('1000'),
}

REGULAR_BASE = {
    'free':     Decimal('0'),
    'standard': Decimal('300'),
    'premium':  Decimal('1299'),
    'pro':      Decimal('1499'),
}

REGULAR_YEARLY_DISCOUNT = {
    'free':     Decimal('0'),
    'standard': Decimal('0'),
    'premium':  Decimal('0.15'),
    'pro':      Decimal('0.17'),
}

REGULAR_EXTRA_FLAT = {
    'standard': Decimal('150'),
    'premium':  Decimal('600'),
    'pro':      Decimal('700'),
}

PLAN_RANK = {'free': 0, 'standard': 1, 'premium': 2, 'pro': 3}

def _floor_peso(value):
    """Round down to whole peso (customer-friendly)."""
    return value.quantize(Decimal('1'), rounding=ROUND_DOWN)


def _extra_business_surcharge(plan, base_price, is_founder):
    if plan == 'free':
        return Decimal('0')
    if is_founder:
        if plan == 'standard':
            return Decimal('150')
        return base_price * Decimal('0.5')
    return REGULAR_EXTRA_FLAT.get(plan, Decimal('0'))


def _peso(value):
    return value.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)


def _lockable_models():
    """Lazy import to avoid circular deps at module load."""
    from Product.models import Product, ProductPreset
    from Supplier.models import Material, MaterialPreset, Supplier
    from Employee.models import Employee
    return (Product, Material, Supplier, Employee, ProductPreset, MaterialPreset)


# ── Founder System ───────────────────────────────────────────────────────────

class FounderInvite(models.Model):
    code        = models.CharField(max_length=20, unique=True, db_index=True)
    email       = models.EmailField(blank=True)
    note        = models.CharField(max_length=100, blank=True)
    claimed_by  = models.OneToOneField(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='founder_invite',
    )
    claimed_at  = models.DateTimeField(null=True, blank=True)
    created_at  = models.DateTimeField(auto_now_add=True)

    @property
    def is_claimed(self):
        return self.claimed_by_id is not None

    @classmethod
    def generate(cls, note='', email=''):
        return cls.objects.create(
            code=secrets.token_urlsafe(8).upper().replace('_', '').replace('-', '')[:12],
            note=note,
            email=email,
        )

    def __str__(self):
        status = 'claimed' if self.is_claimed else 'open'
        return f"{self.code} ({status}) — {self.note or 'no note'}"


class FounderSlot(models.Model):
    slots_total   = models.PositiveIntegerField(default=FOUNDER_SLOTS_TOTAL)
    slots_claimed = models.PositiveIntegerField(default=0)

    @classmethod
    def try_claim(cls):
        with transaction.atomic():
            slot = cls.objects.select_for_update().filter(pk=1).first()
            if slot is None:
                slot = cls.objects.create(pk=1, slots_total=FOUNDER_SLOTS_TOTAL)
                slot = cls.objects.select_for_update().get(pk=1)
            if slot.slots_claimed >= slot.slots_total:
                return False
            slot.slots_claimed += 1
            slot.save(update_fields=['slots_claimed'])
            return True

    @property
    def slots_remaining(self):
        return max(0, self.slots_total - self.slots_claimed)

    def __str__(self):
        return f"Founder slots: {self.slots_claimed}/{self.slots_total}"


# ── Subscription (Owner-level Billing Account) ───────────────────────────────

class Subscription(models.Model):
    PAYMENT_METHOD_CHOICES = [
    ('installment', 'Monthly installments'),
    ('upfront',     'Yearly upfront'),
]
    
    """Owner-level billing account. Per-business plans live on BusinessPlan."""
    user          = models.OneToOneField(User, on_delete=models.CASCADE, related_name='subscription')
    bundle        = models.CharField(max_length=20, choices=BUNDLE_CHOICES, default='triple')
    billing_cycle = models.CharField(max_length=20, choices=BILLING_CHOICES, default='monthly')
    payment_method = models.CharField(max_length=20, choices=PAYMENT_METHOD_CHOICES, default='installment')
    is_founder    = models.BooleanField(default=False)
    is_lifetime   = models.BooleanField(default=False)
    trial_used    = models.BooleanField(default=False)
    started_at    = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        tag = ' [Founder]' if self.is_founder else ''
        lt  = ' [Lifetime]' if self.is_lifetime else ''
        return f"{self.user.username} — {self.get_bundle_display()}{tag}{lt}"

    @classmethod
    def create_free(cls, user):
        return cls.objects.create(
            user=user,
            is_founder=False,
            is_lifetime=False,
        )

    @classmethod
    def claim_founder_invite(cls, user, code):
        with transaction.atomic():
            invite = FounderInvite.objects.select_for_update().filter(
                code=code, claimed_by__isnull=True,
            ).first()
            if invite is None:
                return False
            if invite.email and invite.email.lower() != (user.email or '').lower():
                return False
            if not FounderSlot.try_claim():
                return False

            sub = getattr(user, 'subscription', None) or cls.objects.create(user=user)
            sub.is_founder = True
            sub.save(update_fields=['is_founder'])

            invite.claimed_by = user
            invite.claimed_at = timezone.now()
            invite.save(update_fields=['claimed_by', 'claimed_at'])
            return True

    @classmethod
    def grant_lifetime(cls, user):
        """All owner's businesses → Pro, no expiry."""
        sub, _ = cls.objects.get_or_create(user=user)
        sub.is_lifetime = True
        sub.bundle = 'triple'
        sub.save()
        for biz in user.business_profiles.all():
            bp = getattr(biz, 'plan', None)
            if bp:
                bp.plan = 'pro'
                bp.is_active = True
                bp.expires_at = None
                bp.save()
        return sub

    # ── Pricing (sums per-business plans) ────────────────────────────────────

    def _paid_plans_sorted(self):
        """Plan keys for non-free businesses, highest tier first."""
        plans = []
        for biz in self.user.business_profiles.all():
            bp = getattr(biz, 'plan', None)
            if bp and bp.plan in ('standard', 'premium', 'pro'):
                plans.append(bp.plan)
        plans.sort(key=lambda p: PLAN_RANK[p], reverse=True)
        return plans

    def _component_monthly(self, plan, is_first):
        """Base price if this is the highest-tier business, else its surcharge."""
        base_table = FOUNDER_BASE if self.is_founder else REGULAR_BASE
        base = base_table[plan]
        if is_first:
            return base
        return _extra_business_surcharge(plan, base, self.is_founder)

    def get_monthly_price(self):
        if self.is_lifetime:
            return Decimal('0')
        total = Decimal('0')
        for i, plan in enumerate(self._paid_plans_sorted()):
            total += self._component_monthly(plan, is_first=(i == 0))
        return _peso(total)

    def get_yearly_price(self):
        """Effective discounted per-month total × 12. Each component rounded down."""
        if self.is_lifetime:
            return Decimal('0')
        monthly_total = Decimal('0')
        for i, plan in enumerate(self._paid_plans_sorted()):
            component = self._component_monthly(plan, is_first=(i == 0))
            discount = Decimal('0') if self.is_founder else REGULAR_YEARLY_DISCOUNT.get(plan, Decimal('0'))
            monthly_total += _floor_peso(component * (Decimal('1') - discount))
        return _peso(monthly_total * 12)


    def get_total_price(self):
        return self.get_yearly_price() if self.billing_cycle == 'yearly' else self.get_monthly_price()

    def get_display_price(self):
        if self.billing_cycle == 'yearly':
            yearly = self.get_yearly_price()
            return _peso(yearly / 12) if yearly > 0 else Decimal('0')
        return self.get_monthly_price()

    @property
    def has_locked_items(self):
        for model in _lockable_models():
            if model.objects.filter(user=self.user, is_locked=True).exists():
                return True
        return False


# ── Per-Business Plan ────────────────────────────────────────────────────────

class BusinessPlan(models.Model):
    """Per-business plan assignment."""
    business = models.OneToOneField(
        'user.BusinessProfile',
        on_delete=models.CASCADE,
        related_name='plan',
    )
    plan          = models.CharField(max_length=20, choices=PLAN_CHOICES, default='free')
    is_active     = models.BooleanField(default=True)
    started_at    = models.DateTimeField(auto_now_add=True)
    expires_at    = models.DateTimeField(null=True, blank=True)
    is_trial = models.BooleanField(default=False, db_index=True)
    pending_cancellation = models.BooleanField(default=False)


    def __str__(self):
        return f"{self.business.business_name} — {self.get_plan_display()}"

    def _owner_sub(self):
        return getattr(self.business.user, 'subscription', None)

    def is_plan_active(self):
        sub = self._owner_sub()
        if sub and sub.is_lifetime:
            return True
        if self.plan == 'free':
            return True
        if not self.expires_at:
            return self.is_active
        return self.is_active and timezone.now() <= self.expires_at

    def is_expired(self):
        sub = self._owner_sub()
        if sub and sub.is_lifetime:
            return False
        if self.plan == 'free' or not self.expires_at:
            return False
        return timezone.now() > self.expires_at

    def has_dashboard(self):
        """PRO-only feature."""
        return self.limits().get('dashboard')
    
    def has_timecards(self):
        """Clock in/out + hours tracking — Standard+."""
        return self.limits().get('timecards', False)

    def has_cash_reconciliation(self):
        """Cash drawer + GCash + bank variance — Standard+."""
        return self.limits().get('cash_reconciliation', False)

    def has_receipt_print(self):
        """Thermal receipt printing - Premium and Pro."""
        return self.limits().get('receipt_print', False)

    def has_weekly_summary(self):
        """Weekly summary filter - Pro only"""
        return 'weekly' in (self.limits().get('analytics_access') or '')
    
    def has_daily_summary(self):
        """Daily summary — Premium and Pro."""
        return 'daily' in (self.limits().get('analytics_access') or '')

    def has_monthly_summary(self):
        """Monthly summary — Premium and Pro."""
        return 'monthly' in (self.limits().get('analytics_access') or '')
    
    def limits(self):
        return PLAN_LIMITS.get(self.plan, PLAN_LIMITS['free'])
    
    def _this_month_count(self, qs):
        """Count rows in qs that were created in the current calendar month."""
        now = timezone.now()
        return qs.filter(
            business=self.business,
            created_at__year=now.year,
            created_at__month=now.month,
        ).count()
        
    def _today_count(self, qs):
        """Count rows in qs created during the current LOCAL day (Asia/Manila)."""
        start = timezone.localtime().replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=1)
        return qs.filter(
            business=self.business,
            created_at__gte=start,
            created_at__lt=end,
        ).count()

        
    @staticmethod
    def next_calendar_reset():
        """Returns datetime for the first day of next month (00:00)."""
        now = timezone.localtime()
        if now.month == 12:
            return now.replace(year=now.year + 1, month=1, day=1,
                               hour=0, minute=0, second=0, microsecond=0)
            
        return now.replace(month=now.month + 1, day=1,
                           hour=0, minute=0, second=0, microsecond=0)
        
    @staticmethod
    def next_daily_reset():
        """Datetime for the start of the next local day (00:00)."""
        start = timezone.localtime().replace(hour=0, minute=0, second=0, microsecond=0)
        return start + timedelta(days=1)


    def _can_add(self, key, current_count):
        limit = self.limits().get(key)
        if limit is None:
            return True
        return current_count < limit
    
    def can_add_sale(self):
        from Sales.models import Sale
        return self._can_add('max_sales', self._today_count(Sale.objects))

    def can_add_purchase(self):
        from Expense.models import Purchase
        return self._can_add('max_purchases', self._today_count(Purchase.objects))

    def can_add_waste(self):
        from Expense.models import Waste
        return self._can_add('max_waste', self._this_month_count(Waste.objects))

    def can_add_expense(self):
        from Expense.models import Expense
        return self._can_add('max_expenses', self._this_month_count(Expense.objects))

    @property
    def is_free(self):     return self.plan == 'free'
    @property
    def is_standard(self): return self.plan == 'standard'
    @property
    def is_premium(self):  return self.plan == 'premium'
    @property
    def is_pro(self):      return self.plan == 'pro'
    
    @property
    def has_locked_items(self):
        """True if THIS business has any locked items (not the whole account)."""
        biz = self.business
        for model in _lockable_models():
            if model.objects.filter(business=biz, is_locked=True).exists():
                return True
        return False

    # ── Capacity checks ──────────────────────────────────────────────────────

    def can_add_staff(self):
        from Employee.models import Employee
        count = Employee.objects.filter(business=self.business).count()
        return self._can_add('max_staff', count)

    def can_add_product(self):
        from Product.models import Product
        count = Product.goods.filter(business=self.business).count()
        return self._can_add('max_products', count)

    def can_add_material(self):
        from Supplier.models import Material
        count = Material.objects.filter(business=self.business).count()
        return self._can_add('max_materials', count)

    def can_add_supplier(self):
        from Supplier.models import Supplier
        count = Supplier.objects.filter(business=self.business).count()
        return self._can_add('max_suppliers', count)

    def can_add_product_preset(self):
        from Product.models import ProductPreset
        count = ProductPreset.objects.filter(business=self.business).count()
        return self._can_add('max_product_presets', count)

    def can_add_material_preset(self):
        from Supplier.models import MaterialPreset
        count = MaterialPreset.objects.filter(business=self.business).count()
        return self._can_add('max_material_presets', count)
    
    def can_self_switch_to(self, target_plan):
        """
        Self-serve plan change allowed?
        - Trial businesses: ONLY Premium ↔ Pro (switch tiers while the trial runs).
        - Paid (non-trial): NO self-serve switching at all — paid plans are
          provisioned manually (Django / support). Downgrades go through the
          cancellation flow; upgrades/changes go through support.
        """
        if target_plan == self.plan:
            return False
        if self.is_trial:
            return target_plan in ('premium', 'pro')   # trial = Premium ↔ Pro only
        return False   # paid = locked


        

    # ── Lock helpers ─────────────────────────────────────────────────────────

    def _cap_locked(self, model, cap):
        if cap is None:
            return
        biz = self.business
        qs = model.objects.filter(user=biz.user, business=biz).order_by('-updated_at')
        keep_ids = list(qs.values_list('id', flat=True)[:cap])
        if keep_ids:
            model.objects.filter(id__in=keep_ids).update(is_locked=False, locked_at=None)
        model.objects.filter(user=biz.user, business=biz).exclude(id__in=keep_ids).update(
            is_locked=True, locked_at=timezone.now(),
        )

    def _sync_all_locks(self):
        with transaction.atomic():
            limits = self.limits()
            biz = self.business
            for model in _lockable_models():
                cap_key = LOCKABLE_LIMIT_KEYS[model.__name__]
                cap = limits.get(cap_key)
                if cap is None:
                    model.objects.filter(
                        user=biz.user, business=biz, is_locked=True,
                    ).update(is_locked=False, locked_at=None)
                else:
                    self._cap_locked(model, cap)

    # ── Plan transitions ─────────────────────────────────────────────────────

    def upgrade_to(self, plan, billing_cycle=None, days=None):
        if plan not in ('standard', 'premium', 'pro'):
            raise ValueError(f"Cannot upgrade to '{plan}'.")
        with transaction.atomic():
            self.plan = plan
            self.is_active = True
            self.is_trial = False   # paid, not trial
            self.expires_at = (
                timezone.now() + timedelta(days=days) if days else None
            )
            self.save()

            if billing_cycle:
                sub = self._owner_sub()
                if sub and sub.billing_cycle != billing_cycle:
                    sub.billing_cycle = billing_cycle
                    sub.save(update_fields=['billing_cycle'])

            biz = self.business
            for model in _lockable_models():
                model.objects.filter(
                    user=biz.user, business=biz, is_locked=True,
                ).update(is_locked=False, locked_at=None)
                
    def downgrade_to_free(self):
        with transaction.atomic():
            self.plan = 'free'
            self.is_trial = False
            self.expires_at = None
            self.save(update_fields=['plan', 'is_trial', 'expires_at'])

            free = PLAN_LIMITS['free']
            for model in _lockable_models():
                cap_key = LOCKABLE_LIMIT_KEYS[model.__name__]
                self._cap_locked(model, free[cap_key])
                
    @property
    def trial_days_remaining(self):
        if not self.is_trial or not self.expires_at:
            return None
        delta = self.expires_at - timezone.now()
        return max(0, delta.days)


    def start_trial(self, plan, days=14):
        if plan not in ('premium', 'pro'):
            raise ValueError(f"Plan '{plan}' is not eligible for trial.")
        sub = self._owner_sub()
        if sub is None:
            raise ValueError("No subscription found for this owner.")
        if sub.trial_used:
            raise ValueError("Trial already used for this owner.")

        with transaction.atomic():
            self.plan = plan
            self.is_active = True
            self.is_trial = True
            self.expires_at = timezone.now() + timedelta(days=days)
            self.save()
            sub.trial_used = True
            sub.save(update_fields=['trial_used'])


    def set_active_items(self, model, keep_active_ids):
        key = LOCKABLE_LIMIT_KEYS.get(model.__name__)
        if key is None:
            raise ValueError(f"{model.__name__} is not lockable.")

        cap = self.limits().get(key)
        keep_ids = list(keep_active_ids)
        if cap is not None and len(keep_ids) > cap:
            raise ValueError(
                f"Your {self.get_plan_display()} plan allows only {cap} "
                f"active {model._meta.verbose_name_plural}."
            )

        biz = self.business
        qs = model.objects.filter(user=biz.user, business=biz)
        valid_ids = set(qs.filter(id__in=keep_ids).values_list('id', flat=True))
        if len(valid_ids) != len(keep_ids):
            raise ValueError("Some items don't belong to this business.")

        with transaction.atomic():
            qs.filter(id__in=valid_ids).update(is_locked=False, locked_at=None)
            qs.exclude(id__in=valid_ids).update(
                is_locked=True, locked_at=timezone.now(),
            )
            
    def _yearly_monthly_rate(self):
        sub = self._owner_sub()
        if sub is None or self.plan not in ('standard', 'premium', 'pro'):
            return Decimal('0')
        base_table = FOUNDER_BASE if sub.is_founder else REGULAR_BASE
        monthly = base_table[self.plan]
        discount = Decimal('0') if sub.is_founder else REGULAR_YEARLY_DISCOUNT.get(self.plan, Decimal('0'))
        return _floor_peso(monthly * (Decimal('1') - discount))


    def _standard_monthly_rate(self):
        """Standard (non-discounted) per-month rate for this plan tier."""
        sub = self._owner_sub()
        if sub is None or self.plan not in ('standard', 'premium', 'pro'):
            return Decimal('0')
        base_table = FOUNDER_BASE if sub.is_founder else REGULAR_BASE
        return _peso(base_table[self.plan])

    def months_used_on_plan(self):
        if not self.started_at:
            return 0
        delta = timezone.now() - self.started_at
        months = (delta.days + 29) // 30   # round up partial months
        return max(1, months)

    def compute_balance_due(self):
        """
        Cancelling a yearly (discounted) plan early: the months already used are
        recalculated at the standard monthly rate. Owner simply pays back the
        discount for those months — no penalty.
        """
        months = self.months_used_on_plan()
        diff = self._standard_monthly_rate() - self._yearly_monthly_rate()
        return _peso(max(Decimal('0'), diff * months))

    def request_cancellation(self):
        if self.pending_cancellation:
            raise ValueError("This business already has a pending cancellation.")
        sub = self._owner_sub()
        if sub is None:
            raise ValueError("No subscription found.")
        if self.plan == 'free':
            raise ValueError("Free plans don't need to be cancelled.")
        if not self.expires_at:
            raise ValueError("This business has no active billing cycle to cancel.")

        months = self.months_used_on_plan()
        due = timezone.now() + timedelta(days=30)

        # Determine the balance based on billing cycle + payment method.
        if sub.billing_cycle == 'monthly':
            amount, status = Decimal('0'), 'waived'
        elif sub.payment_method == 'upfront':
            amount, status = Decimal('0'), 'waived'
        else:
            amount, status = self.compute_balance_due(), 'pending'

        with transaction.atomic():
            self.pending_cancellation = True
            self.save(update_fields=['pending_cancellation'])
            invoice = CancellationInvoice.objects.create(
                business=self.business,
                amount_due=amount,
                plan_at_cancel=self.plan,
                months_used=months,
                cycle_end_at=self.expires_at,
                due_at=due,
                status=status,
            )
        return invoice

        
class CancellationInvoice(models.Model):
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('paid',    'Paid'),
        ('overdue', 'Overdue'),
        ('waived',  'Waived'),
    ]
    business      = models.ForeignKey('user.BusinessProfile', on_delete=models.CASCADE, related_name='cancellation_invoices')
    amount_due    = models.DecimalField(max_digits=10, decimal_places=2)
    plan_at_cancel = models.CharField(max_length=20, choices=PLAN_CHOICES)
    months_used   = models.PositiveIntegerField()
    cycle_end_at  = models.DateTimeField()
    due_at        = models.DateTimeField()
    status        = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    created_at    = models.DateTimeField(auto_now_add=True)
    updated_at    = models.DateTimeField(auto_now=True)
    reminder_day_15_sent = models.BooleanField(default=False)
    reminder_day_30_sent = models.BooleanField(default=False)

    def __str__(self):
        return f"{self.business.business_name} — ₱{self.amount_due} ({self.get_status_display()})"

    def is_overdue(self):
        return self.status == 'pending' and timezone.now() > self.due_at



        