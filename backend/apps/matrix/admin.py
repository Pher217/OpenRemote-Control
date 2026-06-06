from django.contrib import admin

from apps.matrix.models import MatrixRoom


@admin.register(MatrixRoom)
class MatrixRoomAdmin(admin.ModelAdmin):
    list_display = ("room_id", "thread", "created_at")
    raw_id_fields = ("thread",)
