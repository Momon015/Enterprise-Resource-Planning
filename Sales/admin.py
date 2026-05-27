from django.contrib import admin
from Sales.models import Sale, SaleItem, SaleEmployee


class SaleItemInline(admin.TabularInline):
    model = SaleItem
    extra = 0
    autocomplete_fields = ('product',)
    readonly_fields = ('name', 'price_at_sale', 'cost_price', 'supplier_name')


class SaleEmployeeInline(admin.TabularInline):
    model = SaleEmployee
    extra = 0
    autocomplete_fields = ('employee',)
    readonly_fields = ('name', 'daily_rate')


@admin.register(Sale)
class SaleAdmin(admin.ModelAdmin):
    list_display = ('id', 'business', 'date', 'total_revenue', 'total_salary_cost',
                    'line_count', 'created_by', 'created_at')
    list_filter = ('date', 'business', 'created_at')
    search_fields = ('reference', 'business__business_name', 'created_by__username')
    readonly_fields = ('date', 'created_at', 'updated_at')
    autocomplete_fields = ('business', 'user', 'created_by')
    date_hierarchy = 'date'
    inlines = [SaleItemInline, SaleEmployeeInline]


@admin.register(SaleItem)
class SaleItemAdmin(admin.ModelAdmin):
    list_display = ('name', 'sale', 'product', 'quantity', 'price_at_sale', 'cost_price')
    search_fields = ('name', 'product__name', 'supplier_name')
    autocomplete_fields = ('sale', 'product')


# @admin.register(SaleEmployee)
# class SaleEmployeeAdmin(admin.ModelAdmin):
#     list_display = ('name', 'sale', 'employee', 'daily_rate', 'created_at')
#     search_fields = ('name', 'employee__name')
#     readonly_fields = ('created_at', 'updated_at')
#     autocomplete_fields = ('sale', 'employee')
