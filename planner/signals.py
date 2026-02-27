from django.contrib.auth import get_user_model
from django.db.models.signals import post_save
from django.dispatch import receiver

from planner.models import Profile

User = get_user_model()


@receiver(post_save, sender=User)
def ensure_profile(sender, instance, created, **kwargs):  # noqa: ANN001
    if created:
        Profile.objects.create(user=instance)
    else:
        Profile.objects.get_or_create(user=instance)

