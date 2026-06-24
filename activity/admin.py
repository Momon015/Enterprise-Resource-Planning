from django.contrib import admin

# Register your models here.
from .models import AuditLog, DailyClose

@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ('created_at', 'action', 'target_model', 'target_ref', 'actor', 'business')
    list_filter  = ('action', 'target_model', 'business')
    search_fields = ('target_ref', 'reason')
    readonly_fields = [f.name for f in AuditLog._meta.fields]
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
