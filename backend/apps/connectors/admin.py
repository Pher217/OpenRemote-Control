from django.contrib import admin

from .models import ConnectorInstance


@admin.register(ConnectorInstance)
class ConnectorInstanceAdmin(admin.ModelAdmin):
    list_display = ["connector_id", "tool", "workspace_root", "thread", "last_seen_at", "created_at"]
    list_filter = ["tool", "created_at"]
    search_fields = ["connector_id", "tool", "workspace_root"]
    readonly_fields = ["created_at", "last_seen_at"]
