from django.contrib import admin
from Expense.models import (
    Purchase, PurchaseItem, Waste, WasteItem,
    Expense, ExpenseItem, MiscExpense,
)


# ── Purchase ──
class PurchaseItemInline(admin.TabularInline):
    model = PurchaseItem
    extra = 0
    autocomplete_fields = ('material',)
    readonly_fields = ('name', 'supplier')


@admin.register(Purchase)
class PurchaseAdmin(admin.ModelAdmin):
    list_display = ('id', 'business', 'purchase_date', 'total_cost', 'line_count',
                    'status', 'is_paid', 'created_by', 'created_at')
    list_filter = ('is_paid', 'status', 'purchase_date', 'business')
    search_fields = ('reference', 'business__business_name')
    readonly_fields = ('purchase_date', 'created_at', 'updated_at')
    autocomplete_fields = ('business', 'user', 'created_by', 'status')
    date_hierarchy = 'purchase_date'
    inlines = [PurchaseItemInline]


@admin.register(PurchaseItem)
class PurchaseItemAdmin(admin.ModelAdmin):
    list_display = ('name', 'purchase', 'material', 'quantity', 'price', 'discount', 'supplier')
    search_fields = ('name', 'material__name', 'supplier')
    autocomplete_fields = ('purchase', 'material')


# ── Waste ──
class WasteItemInline(admin.TabularInline):
    model = WasteItem
    extra = 0
    autocomplete_fields = ('material', 'product')
    readonly_fields = ('name', 'supplier')


@admin.register(Waste)
class WasteAdmin(admin.ModelAdmin):
    list_display = ('id', 'business', 'date', 'reason', 'total_cost')
    list_filter = ('reason', 'business', 'date')
    search_fields = ('business__business_name',)
    readonly_fields = ('date', 'created_at', 'updated_at')
    autocomplete_fields = ('business', 'user', 'created_by')
    date_hierarchy = 'date'
    inlines = [WasteItemInline]


@admin.register(WasteItem)
class WasteItemAdmin(admin.ModelAdmin):
    list_display = ('name', 'waste', 'material', 'product', 'quantity', 'price', 'supplier')
    search_fields = ('name', 'material__name', 'product__name')
    autocomplete_fields = ('waste', 'material', 'product')


# ── Expense ──
class ExpenseItemInline(admin.TabularInline):
    model = ExpenseItem
    extra = 0
    autocomplete_fields = ('misc_expense',)
    readonly_fields = ('name', 'category')


@admin.register(Expense)
class ExpenseAdmin(admin.ModelAdmin):
    list_display = ('id', 'business', 'date', 'total_amount', 'created_by', 'created_at')
    list_filter = ('date', 'business')
    search_fields = ('business__business_name',)
    readonly_fields = ('created_at', 'updated_at')
    autocomplete_fields = ('business', 'user', 'created_by')
    date_hierarchy = 'date'
    inlines = [ExpenseItemInline]


@admin.register(ExpenseItem)
class ExpenseItemAdmin(admin.ModelAdmin):
    list_display = ('name', 'expense', 'misc_expense', 'amount', 'category')
    search_fields = ('name', 'category')
    autocomplete_fields = ('expense', 'misc_expense')


@admin.register(MiscExpense)
class MiscExpenseAdmin(admin.ModelAdmin):
    list_display = ('name', 'business', 'amount', 'category', 'created_by', 'created_at')
    list_filter = ('category', 'business', 'created_at')
    search_fields = ('name', 'business__business_name')
    readonly_fields = ('created_at', 'updated_at')
    autocomplete_fields = ('business', 'user', 'created_by', 'category')
    date_hierarchy = 'created_at'
