from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.models import User
from .models import UserProfile

class UserProfileInline(admin.StackedInline):
    model = UserProfile
    can_delete = False
    verbose_name_plural = 'Profile & Setlist.fm Settings'

class UserAdmin(BaseUserAdmin):
    inlines = [UserProfileInline]
    list_display = ['username', 'email', 'get_setlistfm_user', 'get_sync_status', 'is_staff']

    def get_setlistfm_user(self, obj):
        return obj.profile.setlistfm_username if hasattr(obj, 'profile') else '-'
    get_setlistfm_user.short_description = 'Setlist.fm User'

    def get_sync_status(self, obj):
        return obj.profile.sync_status if hasattr(obj, 'profile') else '-'
    get_sync_status.short_description = 'Sync Status'

admin.site.unregister(User)
admin.site.register(User, UserAdmin)

@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ['user', 'setlistfm_username', 'prompt_setlistfm', 'sync_status', 'last_synced_at']
    search_fields = ['user__username', 'setlistfm_username']
    list_filter = ['prompt_setlistfm', 'sync_status']
