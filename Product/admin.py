from django.contrib import admin
from Product.models import Product, ProductPreset, ProductPresetItem


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ('name', 'business', 'category', 'cost_price', 'selling_price',
                    'prepared_quantity', 'is_locked', 'created_at')
    list_filter = ('is_locked', 'category', 'created_at')
    search_fields = ('name', 'business__business_name', 'user__username')
    readonly_fields = ('slug', 'created_at', 'updated_at', 'locked_at')
    autocomplete_fields = ('business', 'user', 'material', 'category', 'created_by')
    date_hierarchy = 'created_at'
    actions = ['lock_selected', 'unlock_selected']

    @admin.action(description='Lock selected products')
    def lock_selected(self, request, queryset):
        from django.utils import timezone
        queryset.update(is_locked=True, locked_at=timezone.now())

    @admin.action(description='Unlock selected products')
    def unlock_selected(self, request, queryset):
        queryset.update(is_locked=False, locked_at=None)


class ProductPresetItemInline(admin.TabularInline):
    model = ProductPresetItem
    extra = 0
    autocomplete_fields = ('product',)


@admin.register(ProductPreset)
class ProductPresetAdmin(admin.ModelAdmin):
    list_display = ('name', 'business', 'is_active', 'is_locked', 'created_at')
    list_filter = ('is_active', 'is_locked', 'created_at')
    search_fields = ('name', 'business__business_name')
    readonly_fields = ('slug', 'created_at', 'updated_at', 'locked_at')
    autocomplete_fields = ('business', 'user', 'created_by')
    inlines = [ProductPresetItemInline]


@admin.register(ProductPresetItem)
class ProductPresetItemAdmin(admin.ModelAdmin):
    list_display = ('product', 'preset', 'quantity', 'cost_price', 'supplier_name')
    search_fields = ('product__name', 'preset__name', 'supplier_name')
    autocomplete_fields = ('preset', 'product')
