from django.contrib import admin

from .models import Host


@admin.register(Host)
class HostAdmin(admin.ModelAdmin):
    list_display = ["name", "slug", "os", "status", "last_seen_at"]
    list_filter = ["os", "status", "created_at"]
    search_fields = ["name", "slug", "tailscale_dns"]
    readonly_fields = ["id", "created_at", "updated_at"]
