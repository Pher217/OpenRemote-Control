from django.contrib import admin

from .models import AuditEvent


@admin.register(AuditEvent)
class AuditEventAdmin(admin.ModelAdmin):
    list_display = ["event_type", "actor", "thread", "timestamp"]
    list_filter = ["event_type", "timestamp"]
    search_fields = ["actor", "thread__name"]
    readonly_fields = ["id", "timestamp"]
