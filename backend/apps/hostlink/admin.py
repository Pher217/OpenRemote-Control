from django.contrib import admin

from apps.hostlink.models import HostToken


@admin.register(HostToken)
class HostTokenAdmin(admin.ModelAdmin):
    list_display = ["host", "created_at", "rotated_at", "revoked_at", "is_active"]
    list_filter = ["revoked_at"]
    readonly_fields = ["token_hash", "created_at", "rotated_at", "revoked_at"]
    search_fields = ["host__name", "host__slug"]

    @admin.display(boolean=True)
    def is_active(self, obj):
        return obj.is_active
