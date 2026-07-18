from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin

from user.models import User, EmailOTP, BusinessProfile


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    list_display = ('username', 'email', 'name', 'role', 'is_active', 'failed_attempts', 'date_joined')
    list_filter = ('role', 'is_active', 'is_staff', 'is_superuser')
    search_fields = ('username', 'email', 'name', 'first_name', 'last_name')
    ordering = ('-date_joined',)
    readonly_fields = ('date_joined', 'last_login', 'password_changed_at')

    fieldsets = DjangoUserAdmin.fieldsets + (
        ('paKITA', {
            'fields': ('name', 'slug', 'role', 'owner', 'birthday', 'phone_number',
                       'failed_attempts', 'locked_until', 'password_changed_at'),
        }),
    )


@admin.register(EmailOTP)
class EmailOTPAdmin(admin.ModelAdmin):
    list_display = ('user', 'otp', 'is_verified', 'created_at', 'updated_at')
    list_filter = ('is_verified', 'created_at')
    search_fields = ('user__username', 'user__email', 'otp')
    readonly_fields = ('created_at', 'updated_at')
    date_hierarchy = 'created_at'


@admin.register(BusinessProfile)
class BusinessProfileAdmin(admin.ModelAdmin):
    list_display = ('business_name', 'user', 'business_type', 'is_vat_registered', 'is_bir_active', 'created_at')
    list_filter = ('is_bir_active', 'is_vat_registered', 'business_type')
    search_fields = ('business_name', 'user__username', 'user__email')
    readonly_fields = ('slug', 'created_at', 'updated_at')
    autocomplete_fields = ('user',)

