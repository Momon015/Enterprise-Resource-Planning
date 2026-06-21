from django.contrib import admin
from .models import Employee, Shift, ShiftEmployee


# ── Employee ──
@admin.register(Employee)
class EmployeeAdmin(admin.ModelAdmin):
    list_display = ('name', 'business', 'daily_rate', 'staff_user', 'is_locked', 'created_at')
    list_filter = ('is_locked', 'business', 'created_at')
    search_fields = ('name', 'business__business_name', 'staff_user__username')
    readonly_fields = ('slug', 'created_at', 'updated_at', 'locked_at')
    autocomplete_fields = ('business', 'user', 'staff_user')
    date_hierarchy = 'created_at'


# ── Shift ──
class ShiftEmployeeInline(admin.TabularInline):
    model = ShiftEmployee
    extra = 0
    autocomplete_fields = ('employee',)
    readonly_fields = ('name', 'daily_rate')


@admin.register(Shift)
class ShiftAdmin(admin.ModelAdmin):
    list_display = ('id', 'business', 'date', 'amount', 'created_by', 'created_at')
    list_filter = ('date', 'business')
    search_fields = ('business__business_name',)
    readonly_fields = ('created_at', 'updated_at')
    autocomplete_fields = ('business', 'user', 'created_by')
    date_hierarchy = 'date'
    inlines = [ShiftEmployeeInline]


@admin.register(ShiftEmployee)
class ShiftEmployeeAdmin(admin.ModelAdmin):
    list_display = ('name', 'shift', 'employee', 'daily_rate')
    search_fields = ('name', 'employee__name')
    autocomplete_fields = ('shift', 'employee')
