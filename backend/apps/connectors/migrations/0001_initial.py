import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ("threads", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="ConnectorInstance",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("connector_id", models.CharField(db_index=True, max_length=255, unique=True)),
                ("tool", models.CharField(max_length=64)),
                ("workspace_root", models.CharField(blank=True, max_length=1024)),
                (
                    "thread",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="connector_instances",
                        to="threads.thread",
                    ),
                ),
                ("last_seen_at", models.DateTimeField(auto_now=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={
                "ordering": ["-last_seen_at"],
            },
        ),
    ]
