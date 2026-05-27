from django.contrib import admin
from core.models import StatusModel, Category


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ('name', 'category_type', 'business', 'user', 'created_by')
    list_filter = ('category_type',)
    search_fields = ('name', 'business__business_name', 'user__username')
    readonly_fields = ('slug',)
    autocomplete_fields = ('business', 'user', 'created_by')


@admin.register(StatusModel)
class StatusModelAdmin(admin.ModelAdmin):
    list_display = ('name', 'slug', 'created_at')
    search_fields = ('name',)
    readonly_fields = ('slug', 'created_at', 'updated_at')
