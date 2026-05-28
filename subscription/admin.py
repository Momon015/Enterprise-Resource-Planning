from django.contrib import admin, messages
from django.utils.html import format_html
from django.urls import reverse

from subscription.models import (
    Subscription, BusinessPlan, FounderInvite, FounderSlot, CancellationInvoice
)


# ── Subscription ─────────────────────────────────────────────────────────────

@admin.register(Subscription)
class SubscriptionAdmin(admin.ModelAdmin):
    list_display = (
        'user', 'bundle', 'billing_cycle',
        'is_founder', 'is_lifetime',
        'monthly_total_display', 'started_at',
    )
    list_filter = ('bundle', 'billing_cycle', 'is_founder', 'is_lifetime')
    search_fields = ('user__username', 'user__email')
    readonly_fields = ('started_at', 'monthly_total_display', 'yearly_total_display')
    fieldsets = (
        ('Account', {
            'fields': ('user', 'bundle', 'billing_cycle'),
        }),
        ('Status flags', {
            'fields': ('is_founder', 'is_lifetime', 'trial_used'),
        }),
        ('Pricing (computed)', {
            'fields': ('monthly_total_display', 'yearly_total_display'),
        }),
        ('Meta', {
            'fields': ('started_at',),
        }),
    )
    actions = ['grant_lifetime']

    @admin.display(description='Monthly ₱', ordering='id')
    def monthly_total_display(self, obj):
        return f"₱{obj.get_monthly_price():,.2f}"

    @admin.display(description='Yearly ₱')
    def yearly_total_display(self, obj):
        return f"₱{obj.get_yearly_price():,.2f}"

    @admin.action(description='Grant lifetime (comp account) to selected')
    def grant_lifetime(self, request, queryset):
        count = 0
        for sub in queryset:
            Subscription.grant_lifetime(sub.user)
            count += 1
        self.message_user(request, f'{count} account(s) granted lifetime.', messages.SUCCESS)


# ── BusinessPlan ─────────────────────────────────────────────────────────────

@admin.register(BusinessPlan)
class BusinessPlanAdmin(admin.ModelAdmin):
    list_display = (
        'business', 'owner_username', 'plan',
        'is_active', 'started_at', 'expires_at',
    )
    list_filter = ('plan', 'is_active')
    search_fields = (
        'business__business_name',
        'business__user__username',
    )
    readonly_fields = ('started_at',)
    actions = ['force_downgrade_to_free']

    @admin.display(description='Owner', ordering='business__user__username')
    def owner_username(self, obj):
        return obj.business.user.username

    @admin.action(description='Force downgrade selected to Free')
    def force_downgrade_to_free(self, request, queryset):
        count = 0
        for bp in queryset:
            bp.downgrade_to_free()
            count += 1
        self.message_user(request, f'{count} business(es) downgraded to Free.', messages.SUCCESS)


# ── FounderInvite ────────────────────────────────────────────────────────────

@admin.register(FounderInvite)
class FounderInviteAdmin(admin.ModelAdmin):
    list_display = (
        'code', 'note', 'email',
        'claimed_status', 'claimed_by', 'claimed_at', 'created_at',
    )
    list_filter = ('claimed_at',)
    search_fields = ('code', 'note', 'email', 'claimed_by__username')
    readonly_fields = ('code', 'claimed_by', 'claimed_at', 'created_at')
    fields = ('code', 'note', 'email', 'claimed_by', 'claimed_at', 'created_at')
    actions = ['generate_one_invite']

    @admin.display(description='Status', boolean=True, ordering='claimed_by')
    def claimed_status(self, obj):
        return obj.is_claimed

    @admin.action(description='Generate one new invite code')
    def generate_one_invite(self, request, queryset):
        invite = FounderInvite.generate(note='Generated via admin')
        self.message_user(
            request,
            f'New invite code: {invite.code} (note: "{invite.note}")',
            messages.SUCCESS,
        )

    def has_add_permission(self, request):
        # Force codes to be created via FounderInvite.generate() (random token)
        return False


# ── FounderSlot ──────────────────────────────────────────────────────────────

@admin.register(FounderSlot)
class FounderSlotAdmin(admin.ModelAdmin):
    list_display = ('slots_total', 'slots_claimed', 'slots_remaining_display')
    readonly_fields = ('slots_claimed', 'slots_remaining_display')

    @admin.display(description='Remaining')
    def slots_remaining_display(self, obj):
        return obj.slots_remaining

    def has_add_permission(self, request):
        # Singleton — only one row should exist
        return not FounderSlot.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False

# ── CancellationInvoice ──────────────────────────────────────────────────────

@admin.register(CancellationInvoice)
class CancellationInvoiceAdmin(admin.ModelAdmin):
    list_display = ('id', 'business', 'plan_at_cancel', 'amount_due', 'status', 'months_used', 'cycle_end_at', 'due_at', 'created_at')
    list_filter = ('status', 'plan_at_cancel', 'created_at')
    search_fields = ('business__business_name', 'business__user__username')
    readonly_fields = ('created_at', 'updated_at')
    date_hierarchy = 'created_at'
    actions = ['mark_paid', 'mark_waived']

    @admin.action(description='Mark selected as PAID')
    def mark_paid(self, request, queryset):
        queryset.update(status='paid')

    @admin.action(description='Mark selected as WAIVED')
    def mark_waived(self, request, queryset):
        queryset.update(status='waived')
