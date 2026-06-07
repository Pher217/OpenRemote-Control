import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ("threads", "0002_rename_content_message_redacted_content_and_more"),
    ]

    operations = [
        migrations.CreateModel(
            name="GatewayMessage",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("platform", models.CharField(max_length=32)),
                ("recipient", models.CharField(max_length=255)),
                ("text", models.TextField()),
                ("prompt_nonce", models.CharField(blank=True, default="", max_length=64)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("delivered_at", models.DateTimeField(db_index=True, null=True, blank=True)),
            ],
        ),
        migrations.CreateModel(
            name="GatewayChat",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("platform", models.CharField(max_length=32)),
                ("chat_id", models.CharField(max_length=255)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "thread",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="gateway_chats",
                        to="threads.thread",
                    ),
                ),
            ],
            options={
                "unique_together": {("platform", "chat_id")},
            },
        ),
        migrations.AddIndex(
            model_name="gatewaymessage",
            index=models.Index(fields=["platform", "delivered_at"], name="gateway_msg_platform_delivered_idx"),
        ),
    ]
