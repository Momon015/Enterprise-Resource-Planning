from django.contrib import admin

# Register your models here.
from .models import AuditLog, DailyClose, AccumulatedGrandSalesCounter, AccumulatedGrandSalesEntry

@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ('created_at', 'action', 'target_model', 'target_ref', 'actor', 'business')
    list_filter  = ('action', 'target_model', 'business')
    search_fields = ('target_ref', 'reason')
    readonly_fields = [f.name for f in AuditLog._meta.fields]
    def has_add_permission(self, request): return False
    def has_change_permission(self, request, obj=None): return False
    def has_delete_permission(self, request, obj=None): return False

@admin.register(AccumulatedGrandSalesCounter)
class AccumulatedGrandSalesCounterAdmin(admin.ModelAdmin):
    """Read-only on purpose. An editable odometer is a sales-suppression tool, and
    RMO 24-2023 lists tampering with sales data as grounds for revoking accreditation
    — so not even a superuser gets a text box here."""
    list_display = ('business', 'channel', 'total', 'entry_count', 'updated_at')
    list_filter  = ('channel', 'business')
    readonly_fields = [f.name for f in AccumulatedGrandSalesCounter._meta.fields]
    def has_add_permission(self, request): return False
    def has_change_permission(self, request, obj=None): return False
    def has_delete_permission(self, request, obj=None): return False

@admin.register(AccumulatedGrandSalesEntry)
class AccumulatedGrandSalesEntryAdmin(admin.ModelAdmin):
    list_display = ('created_at', 'business', 'channel', 'amount', 'running_total',
                    'source_model', 'source_ref', 'business_date')
    list_filter  = ('channel', 'business', 'source_model')
    search_fields = ('source_ref',)
    date_hierarchy = 'business_date'
    readonly_fields = [f.name for f in AccumulatedGrandSalesEntry._meta.fields]
    def has_add_permission(self, request): return False
    def has_change_permission(self, request, obj=None): return False
    def has_delete_permission(self, request, obj=None): return False

@admin.register(DailyClose)
class DailyCloseAdmin(admin.ModelAdmin):
    list_display = ('date', 'business', 'total_revenue', 'total_material_cost',
                    'total_salary_cost', 'total_waste_cost', 'total_expense_cost',
                    'net_profit', 'closed_at')
    list_filter  = ('business',)
    date_hierarchy = 'date'
    readonly_fields = [f.name for f in DailyClose._meta.fields]
    def has_add_permission(self, request): return False
    def has_change_permission(self, request, obj=None): return False
    def has_delete_permission(self, request, obj=None): return False
