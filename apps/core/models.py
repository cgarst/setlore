from django.db import models
from django.contrib.auth.models import User
from django.db.models.signals import post_save
from django.dispatch import receiver

class UserProfile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='profile')
    setlistfm_username = models.CharField(max_length=150, blank=True, default='')
    setlistfm_api_key = models.CharField(max_length=255, blank=True, default='', help_text="Optional personal API key (overrides global env key)")
    ignored_artists = models.JSONField(default=list, blank=True, help_text="List of artist names to exclude from analytics")
    default_location = models.CharField(max_length=255, blank=True, default='Washington, DC', help_text="Default city/region for venue fallbacks")
    last_synced_at = models.DateTimeField(null=True, blank=True)
    sync_status = models.CharField(max_length=50, default='idle', choices=[
        ('idle', 'Idle'),
        ('syncing', 'Syncing'),
        ('completed', 'Completed'),
        ('error', 'Error'),
    ])
    sync_progress = models.CharField(max_length=255, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Profile for {self.user.username} (@{self.setlistfm_username or 'no-setlistfm'})"

@receiver(post_save, sender=User)
def create_or_save_user_profile(sender, instance, created, **kwargs):
    if created:
        UserProfile.objects.create(user=instance)
    else:
        if hasattr(instance, 'profile'):
            instance.profile.save()
