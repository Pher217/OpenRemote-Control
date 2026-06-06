from django.db import models


class MatrixRoom(models.Model):
    room_id = models.CharField(max_length=255, unique=True, db_index=True)
    thread = models.OneToOneField(
        "threads.Thread",
        on_delete=models.CASCADE,
        related_name="matrix_room",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return f"matrix:{self.room_id}"
