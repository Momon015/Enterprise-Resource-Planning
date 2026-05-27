from django.db import models, transaction
from django.utils import timezone
from datetime import timedelta
from decimal import Decimal, ROUND_HALF_UP

from user.models import User
import secrets


# ── Plan Limits ──────────────────────────────────────────────────────────────

PLAN_LIMITS = {
    'free': {
        'max_staff':            0,
        'max_products':         10,
        'max_materials':        10,
        'max_suppliers':        2,
        'max_sales':            10,
        'max_purchases':        10,
        'max_waste':            10,
        'max_expenses':         5,
        'max_product_presets':  2,
        'max_material_presets': 2,
        'dashboard':            False,
        'daily_summary':        'none',
    },
    'standard': {
        'max_staff':            1,
        'max_products':         30,
        'max_materials':        30,
        'max_suppliers':        5,
        'max_sales':            30,
        'max_purchases':        30,
        'max_waste':            30,
        'max_expenses':         30,
        'max_product_presets':  5,
        'max_material_presets': 5,
        'dashboard':            False,
        'daily_summary':        'none',
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
        'dashboard':            False,
        'daily_summary':        'monthly + daily',
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
        'dashboard':            True,
        'daily_summary':        'daily + monthly + weekly',
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
    from Expense.models import Employee
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
    """Owner-level billing account. Per-business plans live on BusinessPlan."""
    user          = models.OneToOneField(User, on_delete=models.CASCADE, related_name='subscription')
    bundle        = models.CharField(max_length=20, choices=BUNDLE_CHOICES, default='triple')
    billing_cycle = models.CharField(max_length=20, choices=BILLING_CHOICES, default='monthly')
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

    def _plan_counts(self):
        counts = {'standard': 0, 'premium': 0, 'pro': 0}
        for biz in self.user.business_profiles.all():
            bp = getattr(biz, 'plan', None)
            if bp and bp.plan in counts:
                counts[bp.plan] += 1
        return counts

    def get_monthly_price(self):
        if self.is_lifetime:
            return Decimal('0')

        base_table = FOUNDER_BASE if self.is_founder else REGULAR_BASE
        total = Decimal('0')
        for plan, n in self._plan_counts().items():
            if n == 0:
                continue
            base = base_table[plan]
            total += base
            if n > 1:
                surcharge = _extra_business_surcharge(plan, base, self.is_founder)
                total += surcharge * (n - 1)
        return _peso(total)

    def get_yearly_price(self):
        if self.is_lifetime:
            return Decimal('0')

        base_table = FOUNDER_BASE if self.is_founder else REGULAR_BASE
        total = Decimal('0')
        for plan, n in self._plan_counts().items():
            if n == 0:
                continue
            base = base_table[plan]
            monthly_for_plan = base
            if n > 1:
                monthly_for_plan += _extra_business_surcharge(plan, base, self.is_founder) * (n - 1)
            discount = Decimal('0') if self.is_founder else REGULAR_YEARLY_DISCOUNT.get(plan, Decimal('0'))
            total += monthly_for_plan * 12 * (Decimal('1') - discount)
        return _peso(total)

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
    business      = models.OneToOneField(
        'user.BusinessProfile',
        on_delete=models.CASCADE,
        related_name='plan',
    )
    plan          = models.CharField(max_length=20, choices=PLAN_CHOICES, default='free')
    is_active     = models.BooleanField(default=True)
    started_at    = models.DateTimeField(auto_now_add=True)
    expires_at    = models.DateTimeField(null=True, blank=True)

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
        
    @staticmethod
    def next_calendar_reset():
        """Returns datetime for the first day of next month (00:00)."""
        now = timezone.now()
        if now.month == 12:
            return now.replace(year=now.year + 1, month=1, day=1,
                               hour=0, minute=0, second=0, microsecond=0)
            
        return now.replace(month=now.month + 1, day=1,
                           hour=0, minute=0, second=0, microsecond=0)

    def _can_add(self, key, current_count):
        limit = self.limits().get(key)
        if limit is None:
            return True
        return current_count < limit
    
    def can_add_sale(self):
        from Sales.models import Sale
        return self._can_add('max_sales', self._this_month_count(Sale.objects))

    def can_add_purchase(self):
        from Expense.models import Purchase
        return self._can_add('max_purchases', self._this_month_count(Purchase.objects))

    def can_add_waste(self):
        from Expense.models import Waste
        return self._can_add('max_waste', self._this_month_count(Waste.objects))

    def can_add_expense(self):
        from Expense.models import Expense
        return self._can_add('max_expenses', self._this_month_count(Expense.objects))


    @property
    def has_dashboard(self):
        return self.limits()['dashboard']

    @property
    def summary_access(self):
        return self.limits()['daily_summary']

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
        from Expense.models import Employee
        count = Employee.objects.filter(business=self.business).count()
        return self._can_add('max_staff', count)

    def can_add_product(self):
        from Product.models import Product
        count = Product.objects.filter(business=self.business).count()
        return self._can_add('max_products', count)

    def can_add_material(self):
        from Supplier.models import Material
        count = Material.objects.filter(business=self.business).count()
        return self._can_add('max_materials', count)

    def can_add_supplier(self):
        from Supplier.models import Supplier
        count = Supplier.objects.filter(business=self.business).count()
        return self._can_add('max_suppliers', count)

    def can_add_sale(self):
        from Sales.models import Sale
        count = Sale.objects.filter(business=self.business).count()
        return self._can_add('max_sales', count)

    def can_add_purchase(self):
        from Expense.models import Purchase
        count = Purchase.objects.filter(business=self.business).count()
        return self._can_add('max_purchases', count)

    def can_add_waste(self):
        from Expense.models import Waste
        count = Waste.objects.filter(business=self.business).count()
        return self._can_add('max_waste', count)

    def can_add_expense(self):
        from Expense.models import Expense
        count = Expense.objects.filter(business=self.business).count()
        return self._can_add('max_expenses', count)

    def can_add_product_preset(self):
        from Product.models import ProductPreset
        count = ProductPreset.objects.filter(business=self.business).count()
        return self._can_add('max_product_presets', count)

    def can_add_material_preset(self):
        from Supplier.models import MaterialPreset
        count = MaterialPreset.objects.filter(business=self.business).count()
        return self._can_add('max_material_presets', count)

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
            self.expires_at = None
            self.save(update_fields=['plan', 'expires_at'])

            free = PLAN_LIMITS['free']
            for model in _lockable_models():
                cap_key = LOCKABLE_LIMIT_KEYS[model.__name__]
                self._cap_locked(model, free[cap_key])

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
