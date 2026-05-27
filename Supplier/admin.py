from django.contrib import admin
from Supplier.models import Supplier, Material, MaterialPreset, MaterialPresetItem


@admin.register(Supplier)
class SupplierAdmin(admin.ModelAdmin):
    list_display = ('name', 'business', 'user', 'is_locked', 'created_at')
    list_filter = ('is_locked', 'created_at')
    search_fields = ('name', 'business__business_name')
    readonly_fields = ('slug', 'created_at', 'updated_at', 'locked_at')
    autocomplete_fields = ('business', 'user', 'created_by')
    date_hierarchy = 'created_at'


@admin.register(Material)
class MaterialAdmin(admin.ModelAdmin):
    list_display = ('name', 'business', 'supplier', 'category', 'price',
                    'quantity', 'unit', 'is_locked', 'created_at')
    list_filter = ('unit', 'is_locked', 'category', 'created_at')
    search_fields = ('name', 'business__business_name', 'supplier__name')
    readonly_fields = ('slug', 'created_at', 'updated_at', 'locked_at')
    autocomplete_fields = ('business', 'user', 'category', 'supplier', 'created_by')
    date_hierarchy = 'created_at'


class MaterialPresetItemInline(admin.TabularInline):
    model = MaterialPresetItem
    extra = 0
    autocomplete_fields = ('material',)


@admin.register(MaterialPreset)
class MaterialPresetAdmin(admin.ModelAdmin):
    list_display = ('name', 'business', 'is_active', 'is_locked', 'created_at')
    list_filter = ('is_active', 'is_locked', 'created_at')
    search_fields = ('name', 'business__business_name')
    readonly_fields = ('slug', 'created_at', 'updated_at', 'locked_at')
    autocomplete_fields = ('business', 'user', 'created_by')
    inlines = [MaterialPresetItemInline]


@admin.register(MaterialPresetItem)
class MaterialPresetItemAdmin(admin.ModelAdmin):
    list_display = ('material', 'preset', 'quantity', 'discount', 'supplier_name')
    search_fields = ('material__name', 'preset__name', 'supplier_name')
    autocomplete_fields = ('preset', 'material')
