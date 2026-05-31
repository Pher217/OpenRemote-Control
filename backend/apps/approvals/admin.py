from django.contrib import admin

from .models import ApprovalRequest


@admin.register(ApprovalRequest)
class ApprovalRequestAdmin(admin.ModelAdmin):
    list_display = ["request_type", "risk", "status", "thread", "requested_at", "expires_at"]
    list_filter = ["risk", "status", "request_type", "created_at"]
    search_fields = ["summary", "thread__name"]
    readonly_fields = ["id", "created_at", "updated_at"]
