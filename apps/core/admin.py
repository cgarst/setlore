from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.models import User
from .models import UserProfile, SiteSetting, Friendship

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

admin.site.site_header = 'Setlore Administration'
admin.site.site_title = 'Setlore Admin'
admin.site.index_title = 'Setlore Administration'

@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ['user', 'setlistfm_username', 'prompt_setlistfm', 'sync_status', 'last_synced_at']
    search_fields = ['user__username', 'setlistfm_username']
    list_filter = ['prompt_setlistfm', 'sync_status']

@admin.register(SiteSetting)
class SiteSettingAdmin(admin.ModelAdmin):
    list_display = ['__str__', 'registration_enabled', 'updated_at']
    list_editable = ['registration_enabled']

    def has_add_permission(self, request):
        return not SiteSetting.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False

@admin.register(Friendship)
class FriendshipAdmin(admin.ModelAdmin):
    list_display = ['user', 'friend', 'created_at']
    search_fields = ['user__username', 'friend__username']
    list_filter = ['created_at']

