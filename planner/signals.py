import logging
import sys

from django.core.management import call_command
from django.contrib.auth import get_user_model
from django.db.models.signals import post_migrate, post_save
from django.dispatch import receiver

from planner.models import Airport, Profile

User = get_user_model()
logger = logging.getLogger(__name__)


@receiver(post_save, sender=User)
def ensure_profile(sender, instance, created, **kwargs):  # noqa: ANN001
    if created:
        Profile.objects.create(user=instance)
    else:
        Profile.objects.get_or_create(user=instance)


@receiver(post_migrate)
def ensure_airports_seeded(sender, **kwargs):  # noqa: ANN001
    if getattr(sender, "label", "") != "planner":
        return
    argv_parts = [part.lower() for part in sys.argv]
    if "test" in argv_parts or any("pytest" in part for part in argv_parts):
        return
    if Airport.objects.exists():
        return
    try:
        call_command("seed_airports")
    except Exception:  # noqa: BLE001
        logger.exception("Automatic airport seed failed after migrate.")
