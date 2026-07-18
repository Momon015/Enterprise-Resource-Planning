from django.db import models, transaction
from django.utils import timezone
from datetime import timedelta
from decimal import Decimal, ROUND_HALF_UP, ROUND_DOWN

from user.models import User
import secrets


# ── Plan Limits ──────────────────────────────────────────────────────────────

# NOTE — two keys here sound alike and are NOT the same thing:
#   'analytics'        = access to the Analytics PAGES (trends & charts). Pro only.
#                        This is the one hard feature gate; see the analytics-gate decision.
#   'analytics_access' = which SUMMARY granularities the Daily Summary filter offers
#                        (daily / weekly / monthly). Reporting, not insight.
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
        'analytics':            False,
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
        'analytics':            False,
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
        'analytics':            False,
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
        'analytics':            True,
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
    """Owner-level billing account. Per-business plans live on BusinessPlan.

    Billing is either monthly (pay-as-you-go — cancel anytime, nothing owed either
    way) or yearly (paid UPFRONT in full at signup for the discounted rate). There
    is no yearly-installment option: collecting the whole year upfront means an
    early yearly cancel is settled by REFUNDING the customer, never by chasing a
    balance from someone who has already left. See BusinessPlan.compute_refund_due.
    """
    user          = models.OneToOneField(User, on_delete=models.CASCADE, related_name='subscription')
    bundle        = models.CharField(max_length=20, choices=BUNDLE_CHOICES, default='triple')
    billing_cycle = models.CharField(max_length=20, choices=BILLING_CHOICES, default='monthly')
    is_founder    = models.BooleanField(default=False)
    is_lifetime   = models.BooleanField(default=False)
    trial_used    = models.BooleanField(default=False)
    started_at    = models.DateTimeField(auto_now_add=True)

    # ── The billing period lives HERE, on the owner — not on BusinessPlan ────────
    # Bundle pricing is owner-level: the highest-tier business pays the BASE rate and the
    # rest pay SURCHARGES, so a business's price depends on its siblings and cannot be
    # billed in isolation. A nightly job that charged each business on its own expires_at
    # would mis-charge every bundle. One owner, one period, one invoice, one sum.
    #
    # NULL/NULL = no paid term running (an all-Free owner).
    current_period_start = models.DateTimeField(null=True, blank=True)
    current_period_end   = models.DateTimeField(null=True, blank=True)

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

    # ── Billing period ───────────────────────────────────────────────────────

    PERIOD_DAYS = {'monthly': 30, 'yearly': 365}

    @property
    def has_active_period(self):
        return self.current_period_end is not None

    @property
    def period_is_due(self):
        """The term has run out — the biller should charge (or downgrade) this owner."""
        return self.has_active_period and timezone.now() > self.current_period_end

    def open_period(self, *, start=None, days=None):
        """Start the owner's billing term and push its end date onto every paid business.

        BusinessPlan.expires_at becomes a DENORMALISED COPY of current_period_end. It is
        kept only so the per-request access checks (is_plan_active / is_expired) stay
        query-free — they run on every request, once per business. This method is the one
        place allowed to write it; never set expires_at on a paid plan by hand or the two
        will drift.

        Trials are NOT billing: they keep their own 14-day expires_at and are left alone.
        """
        start = start or timezone.now()
        days = days or self.PERIOD_DAYS.get(self.billing_cycle, 30)
        self.current_period_start = start
        self.current_period_end = start + timedelta(days=days)
        self.save(update_fields=['current_period_start', 'current_period_end'])
        self.sync_paid_expiry()
        return self.current_period_end

    def renew(self, *, days=None):
        """Advance the term by one cycle. The biller calls this on a successful charge.

        A yearly renewal is a NEW upfront payment, so the refund clock on every paid
        business restarts too — otherwise a customer in year two would be refunded as if
        they had been paying since year one.
        """
        if not self.has_active_period:
            return self.open_period(days=days)
        start = self.current_period_end
        end = self.open_period(start=start, days=days)
        BusinessPlan.objects.filter(
            business__user=self.user, business__is_active=True, is_trial=False,
        ).exclude(plan='free').update(plan_started_at=start)
        return end

    def close_period(self):
        """No paid businesses left — stop the clock so the biller skips this owner."""
        self.current_period_start = None
        self.current_period_end = None
        self.save(update_fields=['current_period_start', 'current_period_end'])

    def sync_paid_expiry(self):
        # business__is_active=True — BusinessPlan.objects is NOT archive-filtered (only the
        # BusinessProfile reverse manager is), so without this we'd touch archived rows.
        BusinessPlan.objects.filter(
            business__user=self.user, business__is_active=True, is_trial=False,
        ).exclude(plan='free').update(expires_at=self.current_period_end)

    # ── Pricing (sums per-business plans) ────────────────────────────────────

    def plan_components(self):
        """[(BusinessPlan, monthly_price)] for every paid business — base-payer first.

        THE single answer to "what does this business cost this owner". Billing AND
        refunds must both read it.

        Bundle pricing means the highest tier pays the BASE rate and every other business
        pays a SURCHARGE, so a business's price depends on its siblings — it cannot be
        priced alone. The refund path used to re-derive the price straight from
        REGULAR_BASE and never consulted the surcharge, so cancelling a 2nd business
        refunded it at base rate (₱300) when it had only ever been billed a surcharge
        (₱150) — handing back more than was ever collected.

        Ties on tier are broken by pk so the base-payer is stable between calls; without
        it, which business is "first" could flip and prices would flicker.
        """
        return self._components_for(self.paid_plans())

    def paid_plans(self):
        """Every non-free BusinessPlan this owner has (archived businesses excluded)."""
        return [
            bp for biz in self.user.business_profiles.select_related('plan')
            if (bp := getattr(biz, 'plan', None)) and bp.plan in ('standard', 'premium', 'pro')
        ]

    def _components_for(self, plans):
        """Price an arbitrary SET of plans. Kept separate from plan_components() so the
        same rules can be run against a hypothetical bundle — see reprice_preview()."""
        ordered = sorted(plans, key=lambda bp: (-PLAN_RANK[bp.plan], bp.pk))
        return [
            (bp, self._component_monthly(bp.plan, is_first=(i == 0)))
            for i, bp in enumerate(ordered)
        ]

    def component_monthly_for(self, business_plan):
        """This one business's share of the owner's monthly bill (base OR surcharge)."""
        for bp, monthly in self.plan_components():
            if bp.pk == business_plan.pk:
                return monthly
        return Decimal('0')

    def reprice_preview(self, cancelling):
        """What the SURVIVORS would pay if `cancelling` went away.

        Cancelling the base-tier business promotes the next-highest survivor from a
        surcharge to the base rate — so the bill for a business the owner is KEEPING can
        go UP. That is honest to the pricing model (a surcharge only exists as an add-on
        to a base plan) but it is a nasty surprise, so the confirm modal and the
        cancellation email both warn in pesos before anything is committed.

        Returns [(BusinessPlan, old_monthly, new_monthly)] — only the ones that MOVE.
        """
        paid = self.paid_plans()
        before = {bp.pk: m for bp, m in self._components_for(paid)}
        survivors = [bp for bp in paid if bp.pk != cancelling.pk]
        after = {bp.pk: m for bp, m in self._components_for(survivors)}

        return [
            (bp, _peso(before[bp.pk]), _peso(after[bp.pk]))
            for bp in survivors if before[bp.pk] != after[bp.pk]
        ]

    def _component_monthly(self, plan, is_first):
        """Base price if this is the highest-tier business, else its surcharge."""
        base_table = FOUNDER_BASE if self.is_founder else REGULAR_BASE
        base = base_table[plan]
        if is_first:
            return base
        return _extra_business_surcharge(plan, base, self.is_founder)

    def _yearly_component(self, plan, monthly):
        """One component's discounted per-month rate, floored to whole peso."""
        discount = Decimal('0') if self.is_founder else REGULAR_YEARLY_DISCOUNT.get(plan, Decimal('0'))
        return _floor_peso(monthly * (Decimal('1') - discount))

    def get_monthly_price(self):
        if self.is_lifetime:
            return Decimal('0')
        total = sum((m for _, m in self.plan_components()), Decimal('0'))
        return _peso(total)

    def get_yearly_price(self):
        """Effective discounted per-month total × 12. Each component rounded down."""
        if self.is_lifetime:
            return Decimal('0')
        monthly_total = sum(
            (self._yearly_component(bp.plan, monthly)
             for bp, monthly in self.plan_components()),
            Decimal('0'),
        )
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

    # ── The two timestamps are NOT interchangeable ───────────────────────────
    # started_at      = when this ROW was born. A BusinessPlan is created (on Free)
    #                   the moment the BUSINESS is created — see user/signals.py — so
    #                   this is the business's birthday, nothing more.
    # plan_started_at = when the CURRENT billing term began. Set on upgrade, on trial
    #                   start, and (once billing is automated) on renewal.
    #
    # Refund maths must read plan_started_at. Reading started_at re-prices every month
    # since the business signed up rather than since the customer actually paid, which
    # silently under-refunds anyone who ran on Free before upgrading.
    started_at      = models.DateTimeField(auto_now_add=True)
    plan_started_at = models.DateTimeField(null=True, blank=True)

    expires_at    = models.DateTimeField(null=True, blank=True)
    is_trial = models.BooleanField(default=False, db_index=True)
    pending_cancellation = models.BooleanField(default=False)


    def __str__(self):
        return f"{self.business.business_name} — {self.get_plan_display()}"

    def _owner_sub(self):
        return getattr(self.business.user, 'subscription', None)

    # Both of these run on EVERY request via SubscriptionExpiryMiddleware, once per
    # business. _owner_sub() is a DB hit, so it is consulted last — only when the local
    # fields can't already settle the answer. The lifetime check can only ever flip a
    # False to a True, so short-circuiting the True cases ahead of it is equivalent.
    def is_plan_active(self):
        if self.plan == 'free':
            return True
        if self.is_active and (not self.expires_at or timezone.now() <= self.expires_at):
            return True
        sub = self._owner_sub()
        return bool(sub and sub.is_lifetime)

    def is_expired(self):
        if self.plan == 'free' or not self.expires_at:
            return False
        if timezone.now() <= self.expires_at:
            return False
        sub = self._owner_sub()
        return not (sub and sub.is_lifetime)

    def has_dashboard(self):
        """PRO-only feature."""
        return self.limits().get('dashboard')

    def has_analytics(self):
        """Analytics pages (trends & charts) — PRO only.

        The one hard feature gate. Reporting (Daily Summary totals) stays open to
        every tier; only INSIGHT is paid for. Not to be confused with
        has_daily_summary(), which reads the unrelated 'analytics_access' key.
        """
        return self.limits().get('analytics', False)


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
        # Only completed sales count toward the cap — pending/canceled drafts are free.
        return self._can_add('max_sales', self._today_count(Sale.objects.filter(status='completed')))

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
            # A new tier is a new term: the refund clock restarts here, not at signup.
            self.plan_started_at = timezone.now()
            self.save()

            sub = self._owner_sub()
            if sub:
                if billing_cycle and sub.billing_cycle != billing_cycle:
                    sub.billing_cycle = billing_cycle
                    sub.save(update_fields=['billing_cycle'])

                # Join the owner's billing term — opening one if this is their first paid
                # business. Either way expires_at comes from the period, so it is NEVER
                # left NULL on a paid plan. That NULL was the bug that made a paying
                # customer unable to cancel (request_cancellation rejected it as "no
                # active billing cycle").
                if sub.has_active_period:
                    sub.sync_paid_expiry()
                else:
                    sub.open_period(days=days)
                self.refresh_from_db(fields=['expires_at'])
            elif days:
                self.expires_at = timezone.now() + timedelta(days=days)
                self.save(update_fields=['expires_at'])

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
            self.plan_started_at = None   # no term is running on Free
            self.save(update_fields=['plan', 'is_trial', 'expires_at', 'plan_started_at'])

            # Losing a business re-prices the survivors (a surcharge-payer can be promoted
            # to the base rate), and if that was the last paid one the owner's term ends.
            sub = self._owner_sub()
            if sub:
                if sub.paid_plans():
                    sub.sync_paid_expiry()
                elif sub.has_active_period:
                    sub.close_period()

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
            self.plan_started_at = timezone.now()
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
            
    # Both rates price off the owner's BUNDLE COMPONENT for this business — the base rate
    # only if it's the highest-tier business, the surcharge otherwise. They used to read
    # base_table[self.plan] directly, which refunded every extra business at base rate
    # even though it was billed a surcharge. Always go through Subscription; it is the one
    # place that knows what this business actually costs.
    def _yearly_monthly_rate(self):
        """What the owner actually pays per month for THIS business on a yearly cycle."""
        sub = self._owner_sub()
        if sub is None or self.plan not in ('standard', 'premium', 'pro'):
            return Decimal('0')
        return sub._yearly_component(self.plan, sub.component_monthly_for(self))

    def _standard_monthly_rate(self):
        """Undiscounted per-month rate for THIS business (its bundle component)."""
        sub = self._owner_sub()
        if sub is None or self.plan not in ('standard', 'premium', 'pro'):
            return Decimal('0')
        return _peso(sub.component_monthly_for(self))

    def months_used_on_plan(self):
        """Months consumed in the CURRENT billing term — the refund clock.

        Anchors on plan_started_at, NOT started_at. Falls back to started_at only for
        legacy rows written before plan_started_at existed (see migration 0005), where
        the two happen to coincide.
        """
        anchor = self.plan_started_at or self.started_at
        if not anchor:
            return 0
        delta = timezone.now() - anchor
        months = (delta.days + 29) // 30   # round up partial months
        return max(1, months)

    def compute_refund_due(self):
        """
        Cancelling a yearly (upfront) plan early. The customer paid the full
        discounted year at signup, so the months already used are re-priced at the
        standard monthly rate (the yearly discount is forfeited) and we refund
        whatever of the upfront payment is left over. Never negative — cancelling
        near year-end simply yields no refund.
        """
        paid_upfront = self._yearly_monthly_rate() * 12
        used_at_standard = self._standard_monthly_rate() * self.months_used_on_plan()
        return _peso(max(Decimal('0'), paid_upfront - used_at_standard))

    def request_cancellation(self):
        if self.pending_cancellation:
            raise ValueError("This business already has a pending cancellation.")
        sub = self._owner_sub()
        if sub is None:
            raise ValueError("No subscription found.")
        if self.plan == 'free':
            raise ValueError("Free plans don't need to be cancelled.")

        # A paid plan's term end is the OWNER's period end (expires_at is just a cached
        # copy of it); a trial's is its own 14-day expires_at. Falling back to the period
        # means a paid plan can always be cancelled even if the cached copy is missing —
        # it used to hard-fail on a NULL expires_at, locking paying customers out of the
        # cancel flow entirely.
        term_end = self.expires_at or (None if self.is_trial else sub.current_period_end)
        if not term_end:
            raise ValueError("This business has no active billing cycle to cancel.")

        months = self.months_used_on_plan()
        due = timezone.now() + timedelta(days=30)

        # Monthly = pay-as-you-go, nothing owed either way. Yearly = paid upfront,
        # so an early cancel refunds the unused portion (used months re-priced at
        # the standard rate). We hold the cash, so there's no balance to chase.
        if sub.billing_cycle == 'yearly':
            refund = self.compute_refund_due()
            status = 'pending' if refund > 0 else 'none'
        else:
            refund, status = Decimal('0'), 'none'

        with transaction.atomic():
            self.pending_cancellation = True
            self.save(update_fields=['pending_cancellation'])
            invoice = CancellationInvoice.objects.create(
                business=self.business,
                refund_amount=refund,
                plan_at_cancel=self.plan,
                months_used=months,
                cycle_end_at=term_end,
                due_at=due,
                status=status,
            )
        return invoice

    def resume_cancellation(self):
        """Undo a scheduled cancellation before the cycle ends and keep the plan.

        Safe while no refund has actually gone out: we clear the flag and mark
        the pending refund record 'voided' — kept, not deleted, so the cancel →
        resume event stays as history (churn-saved signal + pen-not-pencil). If
        we've already paid the refund (status 'refunded'), the customer can't
        silently resume for free — they must re-subscribe. Access never lapsed
        (expires_at is untouched), so the plan simply carries on."""
        if not self.pending_cancellation:
            raise ValueError("This business has no pending cancellation to resume.")

        invoice = self.business.cancellation_invoices.order_by('-created_at').first()
        if invoice and invoice.status == 'refunded':
            raise ValueError(
                "A refund was already issued for this cancellation. "
                "Please re-subscribe to continue on a paid plan."
            )

        with transaction.atomic():
            self.pending_cancellation = False
            self.save(update_fields=['pending_cancellation'])
            if invoice and invoice.status in ('pending', 'none'):
                invoice.status = 'voided'
                invoice.save(update_fields=['status', 'updated_at'])
        return True


class CancellationInvoice(models.Model):
    """Record of a plan cancellation. For yearly (upfront) plans it may carry a
    refund WE owe the customer; monthly cancels carry no refund."""
    STATUS_CHOICES = [
        ('none',     'No refund'),
        ('pending',  'Refund pending'),
        ('refunded', 'Refunded'),
        ('voided',   'Voided (cancellation undone)'),
    ]
    business      = models.ForeignKey('user.BusinessProfile', on_delete=models.CASCADE, related_name='cancellation_invoices')
    refund_amount = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0'))
    plan_at_cancel = models.CharField(max_length=20, choices=PLAN_CHOICES)
    months_used   = models.PositiveIntegerField()
    cycle_end_at  = models.DateTimeField()
    due_at        = models.DateTimeField()
    status        = models.CharField(max_length=20, choices=STATUS_CHOICES, default='none')
    created_at    = models.DateTimeField(auto_now_add=True)
    updated_at    = models.DateTimeField(auto_now=True)
    reminder_day_15_sent = models.BooleanField(default=False)
    reminder_day_30_sent = models.BooleanField(default=False)

    def __str__(self):
        return f"{self.business.business_name} — refund ₱{self.refund_amount} ({self.get_status_display()})"

    def is_overdue(self):
        """Refund still unpaid past its target date — a nudge for us, not the customer."""
        return self.status == 'pending' and timezone.now() > self.due_at



        