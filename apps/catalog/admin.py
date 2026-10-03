from django.contrib import admin
from .models import Artist, Album, Song, Venue, MusicianTenure, ApiCache, MusicBrainzDump

@admin.register(MusicBrainzDump)
class MusicBrainzDumpAdmin(admin.ModelAdmin):
    def changelist_view(self, request, extra_context=None):
        from .views import musicbrainz_dump_admin_view
        return musicbrainz_dump_admin_view(request)

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

class AlbumInline(admin.TabularInline):
    model = Album
    extra = 0
    fields = ['title', 'release_year', 'album_type']
    show_change_link = True

class MusicianTenureInline(admin.TabularInline):
    model = MusicianTenure
    extra = 0
    fields = ['musician_name', 'role', 'instrument', 'start_year', 'end_year']
    show_change_link = True

@admin.register(Artist)
class ArtistAdmin(admin.ModelAdmin):
    list_display = ['name', 'normalized_name', 'id', 'is_custom_offline', 'created_at']
    search_fields = ['name', 'normalized_name', 'id']
    list_filter = ['is_custom_offline']
    inlines = [MusicianTenureInline, AlbumInline]
    actions = ['refresh_musician_lineups', 'refresh_albums_discography']

    @admin.action(description="⚡ Refresh musician lineups from MusicBrainz (Live)")
    def refresh_musician_lineups(self, request, queryset):
        from src.musician_enricher import MusicianEnricher
        enricher = MusicianEnricher()
        total_created = 0
        total_updated = 0
        artists_processed = 0

        for art in queryset:
            tenures = enricher.enrich_artist(art.name, artist_obj=art, refresh=True)
            created, updated = getattr(enricher, 'last_sync_stats', (0, 0))
            total_created += created
            total_updated += updated
            artists_processed += 1

        ApiCache.objects.filter(endpoint='dashboard_bundle').delete()
        self.message_user(
            request,
            f"Successfully refreshed {artists_processed} artist(s). Added {total_created} new tenures, updated {total_updated} existing records. Dashboard cache cleared."
        )

    @admin.action(description="⚡ Refresh albums & discography from MusicBrainz")
    def refresh_albums_discography(self, request, queryset):
        from src.album_enricher import AlbumEnricher
        enricher = AlbumEnricher()
        for art in queryset:
            songs = Song.objects.filter(artist=art)
            if songs.exists():
                pairs = [{"artist": art.name, "song": s.title} for s in songs]
                enricher.load_cached_catalog(pairs)
        ApiCache.objects.filter(endpoint='dashboard_bundle').delete()
        self.message_user(
            request,
            f"Discography enrichment triggered for {queryset.count()} artist(s). Dashboard cache cleared."
        )

@admin.register(Album)
class AlbumAdmin(admin.ModelAdmin):
    list_display = ['title', 'artist', 'release_year', 'album_type', 'id', 'is_custom_offline']
    search_fields = ['title', 'artist__name', 'id']
    list_filter = ['album_type', 'release_year', 'is_custom_offline']
    autocomplete_fields = ['artist']

@admin.register(Song)
class SongAdmin(admin.ModelAdmin):
    list_display = ['title', 'artist', 'album', 'release_year', 'id', 'is_cover', 'is_custom_offline']
    search_fields = ['title', 'artist__name', 'album__title', 'id']
    list_filter = ['is_cover', 'release_year', 'is_custom_offline']
    autocomplete_fields = ['artist', 'album']

@admin.register(Venue)
class VenueAdmin(admin.ModelAdmin):
    list_display = ['name', 'city', 'state', 'country', 'id', 'is_custom_offline']
    search_fields = ['name', 'city', 'state', 'country', 'id']
    list_filter = ['country', 'state', 'geocode_source', 'is_custom_offline']

@admin.register(MusicianTenure)
class MusicianTenureAdmin(admin.ModelAdmin):
    list_display = ['musician_name', 'artist', 'role', 'instrument', 'start_year', 'end_year']
    search_fields = ['musician_name', 'artist__name', 'role']
    list_filter = ['instrument', 'artist']
    autocomplete_fields = ['artist']
    actions = ['refresh_parent_artist_tenures']

    @admin.action(description="⚡ Refresh selected artists' lineups from MusicBrainz")
    def refresh_parent_artist_tenures(self, request, queryset):
        from src.musician_enricher import MusicianEnricher
        enricher = MusicianEnricher()
        artists = {t.artist for t in queryset if t.artist}
        total_created = 0
        total_updated = 0

        for art in artists:
            enricher.enrich_artist(art.name, artist_obj=art, refresh=True)
            created, updated = getattr(enricher, 'last_sync_stats', (0, 0))
            total_created += created
            total_updated += updated

        ApiCache.objects.filter(endpoint='dashboard_bundle').delete()
        self.message_user(
            request,
            f"Successfully refreshed {len(artists)} artist(s). Added {total_created} new tenures, updated {total_updated} existing records. Dashboard cache cleared."
        )

@admin.register(ApiCache)
class ApiCacheAdmin(admin.ModelAdmin):
    list_display = ['cache_key', 'endpoint', 'updated_at']
    search_fields = ['cache_key', 'endpoint']
    list_filter = ['endpoint']
    actions = ['purge_selected_caches']

    @admin.action(description="⚡ Purge selected API cache records")
    def purge_selected_caches(self, request, queryset):
        count = queryset.count()
        queryset.delete()
        self.message_user(request, f"Purged {count} cache records.")
