from django.contrib import admin

from .models import Project


@admin.register(Project)
class ProjectAdmin(admin.ModelAdmin):
    list_display = ["name", "slug", "sensitivity", "policy", "created_at"]
    list_filter = ["sensitivity", "created_at"]
    search_fields = ["name", "slug"]
    readonly_fields = ["id", "created_at", "updated_at"]
    filter_horizontal = ["allowed_accounts", "allowed_hosts"]
