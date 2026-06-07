import secrets

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("connectors", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="ConnectorKey",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("connector_id", models.CharField(db_index=True, max_length=255, unique=True)),
                ("key_id", models.CharField(max_length=64)),
                ("public_key", models.CharField(max_length=128)),
                ("tool", models.CharField(max_length=64)),
                ("label", models.CharField(blank=True, max_length=255)),
                ("scopes", models.JSONField(default=list)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("last_used_at", models.DateTimeField(blank=True, null=True)),
                ("revoked_at", models.DateTimeField(blank=True, null=True)),
            ],
            options={
                "ordering": ["-created_at"],
            },
        ),
        migrations.CreateModel(
            name="Pairing",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("code", models.CharField(
                    db_index=True,
                    default=secrets.token_urlsafe,
                    max_length=64,
                    unique=True,
                )),
                ("tool", models.CharField(blank=True, max_length=64)),
                ("label", models.CharField(blank=True, max_length=255)),
                ("scopes", models.JSONField(default=list)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("expires_at", models.DateTimeField()),
                ("claimed_at", models.DateTimeField(blank=True, null=True)),
                ("connector_id", models.CharField(blank=True, max_length=255)),
            ],
            options={
                "ordering": ["-created_at"],
            },
        ),
    ]
