from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin

from .models import Role, User


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    ordering = ["email"]
    list_display = ["email", "phone", "hospital", "department", "role", "is_staff", "is_saas_admin", "is_active"]
    list_filter = ["hospital", "department", "role", "is_staff", "is_saas_admin", "is_active"]
    search_fields = ["email", "phone", "first_name", "last_name"]
    fieldsets = (
        (None, {"fields": ("email", "password")}),
        ("Personal info", {"fields": ("first_name", "last_name", "phone", "preferred_language")}),
        ("Hospital", {"fields": ("hospital", "department", "role")}),
        ("Security", {"fields": ("is_active", "is_staff", "is_saas_admin", "is_superuser", "is_2fa_enabled", "allowed_ip_ranges", "groups", "user_permissions")}),
        ("Important dates", {"fields": ("last_login", "date_joined")}),
    )
    add_fieldsets = (
        (None, {
            "classes": ("wide",),
            "fields": ("email", "password1", "password2", "hospital", "is_staff", "is_active"),
        }),
    )
    readonly_fields = ["date_joined"]


@admin.register(Role)
class RoleAdmin(admin.ModelAdmin):
    list_display = ["name", "hospital", "department"]
    list_filter = ["hospital", "department"]
    filter_horizontal = []
