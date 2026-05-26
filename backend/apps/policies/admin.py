from django.contrib import admin

from .models import PolicyProfile


@admin.register(PolicyProfile)
class PolicyProfileAdmin(admin.ModelAdmin):
    list_display = ["name", "sensitivity_max", "cloud_models_allowed", "block_destructive", "created_at"]
    list_filter = ["sensitivity_max", "cloud_models_allowed", "block_destructive", "created_at"]
    search_fields = ["name"]
    readonly_fields = ["id", "created_at", "updated_at"]
