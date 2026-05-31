from django.contrib import admin

from .models import Tier2Provider


@admin.register(Tier2Provider)
class Tier2ProviderAdmin(admin.ModelAdmin):
    list_display = ["name", "base_url", "is_available", "last_probed_at"]
    list_filter = ["is_available", "created_at"]
    search_fields = ["name", "base_url"]
    readonly_fields = ["id", "created_at", "updated_at"]
