from django.contrib import admin

from .models import Account


@admin.register(Account)
class AccountAdmin(admin.ModelAdmin):
    list_display = ["provider", "label", "auth_type", "credential_scheme_version", "created_at"]
    list_filter = ["provider", "auth_type", "created_at"]
    search_fields = ["label", "provider"]
    readonly_fields = ["id", "created_at", "updated_at"]
