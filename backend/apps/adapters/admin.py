from django.contrib import admin

from .models import AdapterCapability


@admin.register(AdapterCapability)
class AdapterCapabilityAdmin(admin.ModelAdmin):
    list_display = ["name", "version", "host", "is_available", "last_probed_at"]
    list_filter = ["is_available", "created_at"]
    search_fields = ["name", "host__slug"]
    readonly_fields = ["id", "created_at", "updated_at"]
