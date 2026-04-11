from django.contrib import admin

from user.models import User, EmailOTP, BusinessProfile

# Register your models here.

admin.site.register(User)
admin.site.register(EmailOTP)
admin.site.register(BusinessProfile)