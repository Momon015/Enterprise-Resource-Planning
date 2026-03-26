from django.contrib import admin

from Sales.models import Sale, SaleItem, SaleEmployee

# Register your models here.
admin.site.register(Sale)
admin.site.register(SaleItem)
admin.site.register(SaleEmployee)