from django.contrib import admin
from Expense.models import PurchaseItem, Purchase, Employee, WasteItem

# Register your models here.
admin.site.register(PurchaseItem)
admin.site.register(Employee)
admin.site.register(WasteItem)
# admin.site.register(Purchase)

class PurchaseItemInline(admin.TabularInline):
    model = PurchaseItem
    extra = 0
    readonly_fields = ['material', 'show_material_price', 'quantity', 'show_total_price_per_item', 'discount', 'show_total_item_discount']
    
    def show_material_price(self, obj):
        return obj.material_price
    
    def show_total_item_discount(self, obj):
        return obj.total_item_discount
    
    def show_total_price_per_item(self, obj):
        return obj.total_price_per_item
    
    show_total_item_discount.short_description = 'Total'
    show_material_price.short_description = 'Price'
    show_total_price_per_item.short_description = 'Item Total'
    
    
class PurchaseAdmin(admin.ModelAdmin):
    list_display = ['id', 'show_purchase_items', 'show_quantity_items', 'total_cost', 'status', 'show_total_discount', 'is_paid', 'created_at', 'purchase_date']
    inlines = [PurchaseItemInline]
    
    
    def show_total_discount(self, obj):
        return obj.total_discount
    

    def show_purchase_items(self, obj):
        return ", ".join(obj.purchase_items)
    
    def show_quantity_items(self, obj):
        return ", ".join(str(q) for q in obj.quantity_items)
    
    show_quantity_items.short_description = 'Quantity'
    show_purchase_items.short_description = 'Items'
    show_total_discount.short_description = 'Total Discount'


admin.site.register(Purchase, PurchaseAdmin)