from django.contrib import admin
from .models import Artist, Album, Song, Venue, MusicianTenure, ApiCache

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
    list_display = ['name', 'normalized_name', 'mbid', 'created_at']
    search_fields = ['name', 'normalized_name']
    inlines = [MusicianTenureInline, AlbumInline]

@admin.register(Album)
class AlbumAdmin(admin.ModelAdmin):
    list_display = ['title', 'artist', 'release_year', 'album_type', 'mbid']
    search_fields = ['title', 'artist__name']
    list_filter = ['album_type', 'release_year']
    autocomplete_fields = ['artist']

@admin.register(Song)
class SongAdmin(admin.ModelAdmin):
    list_display = ['title', 'artist', 'album', 'release_year', 'is_cover', 'original_artist']
    search_fields = ['title', 'artist__name', 'album__title']
    list_filter = ['is_cover', 'release_year']
    autocomplete_fields = ['artist', 'album']

@admin.register(Venue)
class VenueAdmin(admin.ModelAdmin):
    list_display = ['name', 'city', 'state', 'country', 'latitude', 'longitude', 'geocode_source']
    search_fields = ['name', 'city', 'state', 'country']
    list_filter = ['country', 'state', 'geocode_source']

@admin.register(MusicianTenure)
class MusicianTenureAdmin(admin.ModelAdmin):
    list_display = ['musician_name', 'artist', 'role', 'instrument', 'start_year', 'end_year']
    search_fields = ['musician_name', 'artist__name', 'role']
    list_filter = ['instrument', 'artist']
    autocomplete_fields = ['artist']

@admin.register(ApiCache)
class ApiCacheAdmin(admin.ModelAdmin):
    list_display = ['cache_key', 'endpoint', 'updated_at']
    search_fields = ['cache_key', 'endpoint']
    list_filter = ['endpoint']
