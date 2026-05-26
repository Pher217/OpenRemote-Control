from django.contrib import admin

from .models import Message, Thread


class MessageInline(admin.TabularInline):
    model = Message
    extra = 0
    readonly_fields = ["id", "created_at"]


@admin.register(Thread)
class ThreadAdmin(admin.ModelAdmin):
    list_display = ["name", "runtime", "runtime_mode", "status", "host", "created_at"]
    list_filter = ["runtime", "runtime_mode", "status", "created_at"]
    search_fields = ["name", "external_session_ref"]
    readonly_fields = ["id", "created_at", "updated_at"]
    inlines = [MessageInline]


@admin.register(Message)
class MessageAdmin(admin.ModelAdmin):
    list_display = ["thread", "role", "sequence", "created_at"]
    list_filter = ["role", "created_at"]
    search_fields = ["thread__name"]
    readonly_fields = ["id", "created_at"]
