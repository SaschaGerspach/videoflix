from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import migrations
from django.utils import timezone

DEMO_EMAIL = "demo@videoflix.local"
DEMO_PASSWORD = "Demo123!"


def create_demo_user(apps, schema_editor):
    User = get_user_model()
    user, created = User.objects.get_or_create(
        email=DEMO_EMAIL,
        defaults={
            "username": DEMO_EMAIL,
            "is_active": True,
            "is_staff": False,
            "is_superuser": False,
            "last_login": timezone.now(),  # WICHTIG: nicht NULL
        },
    )
    if created:
        user.set_password(DEMO_PASSWORD)
        user.save()


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0001_create_demo_user"),
    ]

    operations = [
        migrations.RunPython(create_demo_user, migrations.RunPython.noop),
    ]
