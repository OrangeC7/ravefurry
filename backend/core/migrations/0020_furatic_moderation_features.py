from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [("core", "0019_store_requester_identity"), migrations.swappable_dependency(settings.AUTH_USER_MODEL)]
    operations = [
        migrations.AddField(model_name="queuedsong", name="requester_token", field=models.CharField(blank=True, db_index=True, default="", max_length=64)),
        migrations.AddField(model_name="queuedsong", name="priority_tier", field=models.CharField(db_index=True, default="normal", max_length=16)),
        migrations.AddField(model_name="queuedsong", name="review_status", field=models.CharField(db_index=True, default="clear", max_length=16)),
        migrations.AddField(model_name="queuedsong", name="review_reason", field=models.CharField(blank=True, default="", max_length=500)),
        migrations.AddField(model_name="queuedsong", name="lyrics", field=models.TextField(blank=True, default="")),
        migrations.AddField(model_name="queuedsong", name="profanity_count", field=models.PositiveIntegerField(default=0)),
        migrations.AddField(model_name="queuedsong", name="slur_count", field=models.PositiveIntegerField(default=0)),
        migrations.AddField(model_name="queuedsong", name="artwork_url", field=models.CharField(blank=True, default="", max_length=2000)),
        migrations.AddField(model_name="queuedsong", name="genre", field=models.CharField(blank=True, default="", max_length=250)),
        migrations.AddField(model_name="currentsong", name="artwork_url", field=models.CharField(blank=True, default="", max_length=2000)),
        migrations.AddField(model_name="currentsong", name="requester_token", field=models.CharField(blank=True, db_index=True, default="", max_length=64)),
        migrations.AddField(model_name="currentsong", name="genre", field=models.CharField(blank=True, default="", max_length=250)),
        migrations.CreateModel(name="ModeratorProfile", fields=[("id", models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")), ("label", models.CharField(max_length=120)), ("song_only", models.BooleanField(default=False)), ("user", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, to=settings.AUTH_USER_MODEL))]),
        migrations.CreateModel(name="ClientIdentity", fields=[("id", models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")), ("token_hash", models.CharField(max_length=64, unique=True)), ("codename", models.CharField(db_index=True, max_length=40, unique=True)), ("first_ip", models.CharField(blank=True, default="", max_length=45)), ("last_ip", models.CharField(blank=True, default="", max_length=45)), ("created", models.DateTimeField(auto_now_add=True)), ("last_seen", models.DateTimeField(auto_now=True))]),
        migrations.CreateModel(name="AuditEntry", fields=[("id", models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")), ("created", models.DateTimeField(auto_now_add=True, db_index=True)), ("action", models.CharField(db_index=True, max_length=120)), ("actor", models.CharField(blank=True, default="", max_length=150)), ("actor_role", models.CharField(blank=True, default="", max_length=20)), ("ip", models.CharField(blank=True, db_index=True, default="", max_length=45)), ("codename", models.CharField(blank=True, db_index=True, default="", max_length=40)), ("browser_token", models.CharField(blank=True, default="", max_length=16)), ("target", models.CharField(blank=True, default="", max_length=500)), ("song_key", models.IntegerField(blank=True, null=True)), ("song_title", models.CharField(blank=True, default="", max_length=2000)), ("metadata", models.JSONField(default=dict))], options={"ordering": ["-created"]}),
        migrations.CreateModel(name="RecentPlay", fields=[("id", models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")), ("created", models.DateTimeField(auto_now_add=True, db_index=True)), ("song_url", models.CharField(db_index=True, max_length=2000))]),
    ]
