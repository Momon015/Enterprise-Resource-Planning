from django.contrib import admin
from Inventory.models import Stock


@admin.register(Stock)
class StockAdmin(admin.ModelAdmin):
    list_display = ('name', 'business', 'material', 'quantity', 'price',
                    'unit', 'supplier', 'created_by', 'created_at')
    list_filter = ('unit', 'business', 'created_at')
    search_fields = ('name', 'business__business_name', 'material__name', 'supplier')
    readonly_fields = ('slug', 'created_at', 'updated_at')
    autocomplete_fields = ('business', 'user', 'material', 'created_by')
    date_hierarchy = 'created_at'
