from django.contrib import admin
from Supplier.models import Material, MaterialPreset, MaterialPresetItem, Supplier

# Register your models here.

admin.site.register(Material)
admin.site.register(Supplier)
admin.site.register(MaterialPreset)
admin.site.register(MaterialPresetItem)