from django.contrib import admin

from apps.gateway.models import GatewayChat, GatewayMessage


@admin.register(GatewayChat)
class GatewayChatAdmin(admin.ModelAdmin):
    list_display = ("platform", "chat_id", "thread", "created_at")
    list_filter = ("platform",)
    search_fields = ("chat_id",)
    raw_id_fields = ("thread",)


@admin.register(GatewayMessage)
class GatewayMessageAdmin(admin.ModelAdmin):
    list_display = ("platform", "recipient", "prompt_nonce", "created_at", "delivered_at")
    list_filter = ("platform",)
    search_fields = ("recipient", "prompt_nonce")
    readonly_fields = ("created_at",)
