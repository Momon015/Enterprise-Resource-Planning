from django.contrib import admin
from django.utils import timezone
from Supplier.models import Supplier, Material, MaterialPreset, MaterialPresetItem


@admin.register(Supplier)
class SupplierAdmin(admin.ModelAdmin):
    list_display = ('name', 'business', 'status', 'contact_number', 'email',
                    'is_locked', 'created_at')
    list_filter = ('status', 'is_locked', 'created_at')
    search_fields = ('name', 'business__business_name', 'email', 'contact_number')
    readonly_fields = ('slug', 'created_at', 'updated_at', 'locked_at',
                       'image_original_name')
    autocomplete_fields = ('business', 'user', 'created_by')
    date_hierarchy = 'created_at'
    actions = ['mark_active', 'mark_on_hold', 'mark_inactive',
               'lock_selected', 'unlock_selected']

    # Show ALL suppliers (including 'inactive') in admin — not just active
    def get_queryset(self, request):
        return Supplier.all_objects.get_queryset()

    @admin.action(description='Mark selected as Active')
    def mark_active(self, request, queryset):
        queryset.update(status='active')

    @admin.action(description='Mark selected as On hold')
    def mark_on_hold(self, request, queryset):
        queryset.update(status='on_hold')

    @admin.action(description='Mark selected as Inactive (archive)')
    def mark_inactive(self, request, queryset):
        queryset.update(status='inactive')

    @admin.action(description='Lock selected suppliers')
    def lock_selected(self, request, queryset):
        queryset.update(is_locked=True, locked_at=timezone.now())

    @admin.action(description='Unlock selected suppliers')
    def unlock_selected(self, request, queryset):
        queryset.update(is_locked=False, locked_at=None)


@admin.register(Material)
class MaterialAdmin(admin.ModelAdmin):
    list_display = ('name', 'business', 'supplier', 'category', 'price',
                    'quantity', 'unit', 'status', 'is_locked', 'created_at')
    list_filter = ('status', 'unit', 'is_locked', 'category', 'created_at')
    search_fields = ('name', 'business__business_name', 'supplier__name')
    readonly_fields = ('slug', 'created_at', 'updated_at', 'locked_at')
    autocomplete_fields = ('business', 'user', 'category', 'supplier', 'created_by')
    date_hierarchy = 'created_at'
    actions = ['mark_active', 'mark_inactive', 'lock_selected', 'unlock_selected']

    def get_queryset(self, request):
        return Material.all_objects.get_queryset()

    @admin.action(description='Mark selected as Active (restore)')
    def mark_active(self, request, queryset):
        queryset.update(status='active')

    @admin.action(description='Mark selected as Inactive (archive)')
    def mark_inactive(self, request, queryset):
        queryset.update(status='inactive')

    @admin.action(description='Lock selected materials')
    def lock_selected(self, request, queryset):
        queryset.update(is_locked=True, locked_at=timezone.now())

    @admin.action(description='Unlock selected materials')
    def unlock_selected(self, request, queryset):
        queryset.update(is_locked=False, locked_at=None)


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
