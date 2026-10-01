from django.contrib import admin
from django.urls import path, reverse_lazy
from django.contrib.auth import views as auth_views
from apps.core import views as core_views
from apps.concerts import views as concerts_views
from apps.catalog import views as catalog_views

urlpatterns = [
    path('admin/musicbrainz/', catalog_views.musicbrainz_dump_admin_view, name='admin_musicbrainz_dump'),
    path('admin/', admin.site.urls),
    path('health/', core_views.health_check, name='health_check'),
    path('privacy/', core_views.privacy_view, name='privacy'),

    # Authentication
    path('accounts/login/', auth_views.LoginView.as_view(template_name='registration/login.html'), name='login'),
    path('accounts/logout/', auth_views.LogoutView.as_view(next_page='login'), name='logout'),
    path('accounts/register/', core_views.register_view, name='register'),
    path('accounts/password_change/', auth_views.PasswordChangeView.as_view(
        template_name='registration/password_change_form.html',
        success_url=reverse_lazy('password_change_done'),
    ), name='password_change'),
    path('accounts/password_change/done/', auth_views.PasswordChangeDoneView.as_view(
        template_name='registration/password_change_done.html',
    ), name='password_change_done'),

    # Google OAuth
    path('accounts/google/login/', core_views.google_login_view, name='google_login'),
    path('accounts/google/callback/', core_views.google_callback_view, name='google_callback'),
    path('accounts/google/disconnect/', core_views.google_disconnect_view, name='google_disconnect'),

    # Homepage
    path('', core_views.home_view, name='home'),

    # Concert Dashboard & API
    path('overview/', concerts_views.dashboard_view, {'tab_name': 'overview'}, name='dashboard'),
    path('overview/', concerts_views.dashboard_view, {'tab_name': 'overview'}, name='dashboard_overview'),
    path('concerts/', concerts_views.dashboard_view, {'tab_name': 'concerts'}, name='dashboard_concerts'),
    path('songs/', concerts_views.dashboard_view, {'tab_name': 'songs'}, name='dashboard_songs'),
    path('drilldown/', concerts_views.dashboard_view, {'tab_name': 'songs'}, name='dashboard_drilldown'),
    path('artists/', concerts_views.dashboard_view, {'tab_name': 'songs'}, name='dashboard_artists'),
    path('musicians/', concerts_views.dashboard_view, {'tab_name': 'musicians'}, name='dashboard_musicians'),
    path('map/', concerts_views.dashboard_view, {'tab_name': 'map'}, name='dashboard_map'),
    path('venues/', concerts_views.dashboard_view, {'tab_name': 'map'}, name='dashboard_venues'),
    path('albums/', concerts_views.dashboard_view, {'tab_name': 'albums'}, name='dashboard_albums'),
    path('advanced/', concerts_views.dashboard_view, {'tab_name': 'albums'}, name='dashboard_advanced'),
    path('freshness/', concerts_views.dashboard_view, {'tab_name': 'freshness'}, name='dashboard_freshness'),
    path('setlists/', concerts_views.dashboard_view, {'tab_name': 'freshness'}, name='dashboard_setlists'),
    path('friends/', concerts_views.dashboard_view, {'tab_name': 'friends'}, name='dashboard_friends'),
    path('gap/', concerts_views.dashboard_view, {'tab_name': 'gap'}, name='dashboard_gap'),
    path('audit/', concerts_views.dashboard_view, {'tab_name': 'gap'}, name='dashboard_audit'),

    # Public Profile
    path('u/<str:username>/export/', concerts_views.export_concerts_csv, name='public_export_concerts_csv'),
    path('@<str:username>/export/', concerts_views.export_concerts_csv, name='public_export_concerts_csv_at'),
    path('u/<str:username>/', concerts_views.public_profile_view, name='public_profile'),
    path('u/<str:username>/<str:tab_name>/', concerts_views.public_profile_view, name='public_profile_tab'),
    path('@<str:username>/', concerts_views.public_profile_view, name='public_profile_at'),
    path('@<str:username>/<str:tab_name>/', concerts_views.public_profile_view, name='public_profile_at_tab'),

    # Admin Impersonation & Controls
    path('accounts/impersonate/', core_views.impersonate_user_view, name='impersonate_user'),
    path('accounts/stop-impersonating/', core_views.stop_impersonating_view, name='stop_impersonating'),
    path('api/admin/toggle-registration/', core_views.toggle_registration_view, name='api_toggle_registration'),

    # Friends API
    path('api/friends/toggle/', core_views.toggle_friend_view, name='api_toggle_friend'),
    path('api/friends/list/', core_views.list_friends_view, name='api_list_friends'),
    path('api/theme/', core_views.set_theme_view, name='api_set_theme'),
    path('api/profile/update/', core_views.update_profile_view, name='api_profile_update'),
    path('api/sync/', concerts_views.trigger_sync, name='api_sync'),
    path('api/sync/status/', concerts_views.sync_status, name='api_sync_status'),
    path('api/sync/cancel/', concerts_views.cancel_sync, name='api_sync_cancel'),
    path('api/upload-csv/', concerts_views.upload_csv, name='api_upload_csv'),
    path('api/concerts/export/', concerts_views.export_concerts_csv, name='api_export_concerts_csv'),
    path('api/concerts/add/', concerts_views.add_concert, name='api_add_concert'),
    path('api/concerts/sync-single/', concerts_views.sync_single_concert_view, name='api_sync_single_concert'),
    path('api/concerts/edit/', concerts_views.edit_concert, name='api_edit_concert'),
    path('api/concerts/delete/', concerts_views.delete_concert, name='api_delete_concert'),
    path('api/concerts/convert-local/', concerts_views.convert_concert_to_local, name='api_convert_concert_to_local'),
    path('api/concerts/save-setlist/', concerts_views.save_setlist, name='api_save_setlist'),
    path('api/concerts/toggle-attendance/', concerts_views.toggle_concert_attendance, name='api_toggle_concert_attendance'),
    path('api/concerts/toggle-favorite/', concerts_views.toggle_concert_favorite, name='api_toggle_concert_favorite'),
    path('api/concerts/toggle-artist-favorite/', concerts_views.toggle_concert_artist_favorite, name='api_toggle_concert_artist_favorite'),
    path('api/autocomplete/', concerts_views.autocomplete_view, name='api_autocomplete'),
    path('api/ticketmaster/preview/', concerts_views.parse_ticketmaster_preview, name='api_ticketmaster_preview'),
    path('api/ticketmaster/confirm/', concerts_views.confirm_ticketmaster_import, name='api_ticketmaster_confirm'),

    # Upcoming Shows & Settings API
    path('api/upcoming/shows/', concerts_views.api_upcoming_shows, name='api_upcoming_shows'),
    path('api/upcoming/track/', concerts_views.track_upcoming_show, name='api_track_upcoming_show'),
    path('api/upcoming/toggle-hidden/', concerts_views.toggle_upcoming_hidden_artist, name='api_toggle_upcoming_hidden_artist'),
    path('api/upcoming/settings/', concerts_views.save_upcoming_settings, name='api_save_upcoming_settings'),

    # MusicBrainz Dump & Catalog Refresh API
    path('api/admin/musicbrainz-dump/status/', catalog_views.api_musicbrainz_dump_status, name='api_mb_dump_status'),
    path('api/admin/musicbrainz-dump/download/', catalog_views.api_musicbrainz_dump_download, name='api_mb_dump_download'),
    path('api/admin/musicbrainz-dump/cancel/', catalog_views.api_musicbrainz_dump_cancel, name='api_mb_dump_cancel'),
    path('api/admin/musicbrainz-dump/delete/', catalog_views.api_musicbrainz_dump_delete, name='api_mb_dump_delete'),
    path('api/admin/musicbrainz-dump/delete-raw/', catalog_views.api_musicbrainz_dump_delete_raw, name='api_mb_dump_delete_raw'),
    path('api/admin/musicbrainz-dump/set-mode/', catalog_views.api_musicbrainz_dump_set_mode, name='api_mb_dump_set_mode'),
    path('api/admin/musicbrainz-dump/set-online-fallback/', catalog_views.api_musicbrainz_dump_set_online_fallback, name='api_mb_dump_set_online_fallback'),
    path('api/admin/musicbrainz-dump/set-schedule/', catalog_views.api_musicbrainz_dump_set_schedule, name='api_mb_dump_set_schedule'),
    path('api/admin/musicbrainz-dump/test-lookup/', catalog_views.api_musicbrainz_dump_test_lookup, name='api_mb_dump_test_lookup'),
    path('api/admin/catalog/refresh-lineup/', catalog_views.api_refresh_musician_lineup, name='api_refresh_musician_lineup'),
    path('api/admin/catalog/purge-cache/', catalog_views.api_purge_dashboard_cache, name='api_purge_dashboard_cache'),
]


