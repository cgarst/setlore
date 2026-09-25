from django.contrib import admin
from .models import Concert, ConcertArtist, ConcertSong

class ConcertSongInline(admin.TabularInline):
    model = ConcertSong
    extra = 0
    fields = ['song', 'raw_song_name', 'set_name', 'is_encore', 'track_num', 'slot', 'is_cover', 'original_artist']
    autocomplete_fields = ['song']

class ConcertArtistInline(admin.TabularInline):
    model = ConcertArtist
    extra = 0
    fields = ['artist', 'billing_order', 'has_setlist', 'setlist_url']
    autocomplete_fields = ['artist']
    show_change_link = True

@admin.register(Concert)
class ConcertAdmin(admin.ModelAdmin):
    list_display = ['date', 'primary_artist', 'venue', 'user', 'is_fully_matched', 'is_partially_matched']
    search_fields = ['primary_artist', 'raw_artists', 'venue__name', 'raw_venue', 'user__username']
    list_filter = ['user', 'year', 'is_fully_matched']
    autocomplete_fields = ['venue', 'user']
    inlines = [ConcertArtistInline]

@admin.register(ConcertArtist)
class ConcertArtistAdmin(admin.ModelAdmin):
    list_display = ['concert', 'artist', 'billing_order', 'has_setlist']
    search_fields = ['artist__name', 'concert__primary_artist', 'concert__user__username']
    list_filter = ['has_setlist', 'concert__user']
    autocomplete_fields = ['concert', 'artist']
    inlines = [ConcertSongInline]

@admin.register(ConcertSong)
class ConcertSongAdmin(admin.ModelAdmin):
    list_display = ['raw_song_name', 'concert_artist', 'set_name', 'track_num', 'slot', 'is_cover']
    search_fields = ['raw_song_name', 'song__title', 'concert_artist__artist__name']
    list_filter = ['is_cover', 'is_encore']
    autocomplete_fields = ['concert_artist', 'song']
