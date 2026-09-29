from django.db import models
from django.contrib.auth.models import User
from django.db.models.signals import post_save
from django.dispatch import receiver

class UserProfile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='profile')
    setlistfm_username = models.CharField(max_length=150, blank=True, default='')
    setlistfm_api_key = models.CharField(max_length=255, blank=True, default='', help_text="Optional personal API key (overrides global env key)")
    prompt_setlistfm = models.BooleanField(default=True, help_text="Prompt for Setlist.fm username during sync if not configured")
    is_public = models.BooleanField(default=True, help_text="Allow public access to view this user profile")
    ignored_artists = models.JSONField(default=list, blank=True, help_text="List of artist names to exclude from analytics")
    google_id = models.CharField(max_length=255, blank=True, null=True, unique=True, default=None, help_text="Google OAuth unique subject identifier")
    google_email = models.EmailField(blank=True, default='', help_text="Google OAuth verified email address")
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

class SiteSetting(models.Model):
    registration_enabled = models.BooleanField(
        default=True,
        help_text="Enable or disable new user registration across the platform."
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Site Setting"
        verbose_name_plural = "Site Settings"

    def __str__(self):
        return f"Site Settings (Registration: {'Enabled' if self.registration_enabled else 'Disabled'})"

    @classmethod
    def get_settings(cls):
        obj, _ = cls.objects.get_or_create(id=1, defaults={'registration_enabled': True})
        return obj

    @classmethod
    def is_registration_enabled(cls):
        try:
            return cls.get_settings().registration_enabled
        except Exception:
            return True


class Friendship(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='friendships')
    friend = models.ForeignKey(User, on_delete=models.CASCADE, related_name='friended_by')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('user', 'friend')
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.user.username} -> {self.friend.username}"


@receiver(post_save, sender=User)
def create_or_save_user_profile(sender, instance, created, **kwargs):
    if created:
        UserProfile.objects.create(user=instance)
    else:
        if hasattr(instance, 'profile'):
            instance.profile.save()

