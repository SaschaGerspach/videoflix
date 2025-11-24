from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("videos", "0005_video_is_published_video_owner_video_status"),
    ]

    operations = [
        migrations.AddField(
            model_name="video",
            name="width",
            field=models.IntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="video",
            name="height",
            field=models.IntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="video",
            name="duration_seconds",
            field=models.IntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="video",
            name="video_bitrate_kbps",
            field=models.IntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="video",
            name="audio_bitrate_kbps",
            field=models.IntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="video",
            name="codec_name",
            field=models.CharField(blank=True, max_length=64, null=True),
        ),
    ]
