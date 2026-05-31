from django.contrib import admin

from .models import SlashCommand


@admin.register(SlashCommand)
class SlashCommandAdmin(admin.ModelAdmin):
    list_display = ["name", "handler_path", "requires_approval", "allowed_in_threads"]
    list_filter = ["requires_approval", "allowed_in_threads", "created_at"]
    search_fields = ["name", "handler_path"]
    readonly_fields = ["id", "created_at", "updated_at"]
