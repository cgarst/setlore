from django.db import models
from django.contrib.auth.models import User

class Concert(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='concerts')
    date = models.DateField(null=True, blank=True, db_index=True)
    raw_date = models.CharField(max_length=50)
    year = models.IntegerField(null=True, blank=True, db_index=True)
    venue = models.ForeignKey('catalog.Venue', on_delete=models.SET_NULL, null=True, blank=True, related_name='concerts')
    raw_venue = models.CharField(max_length=255, blank=True, default='')
    primary_artist = models.CharField(max_length=255)
    raw_artists = models.CharField(max_length=500)
    seen_before = models.CharField(max_length=255, blank=True, default='')
    notes = models.TextField(blank=True, default='')
    source = models.CharField(
        max_length=20,
        default='csv',
        choices=[('manual', 'Manual Entry'), ('csv', 'CSV Import'), ('ticketmaster', 'Ticketmaster Import'), ('setlistfm', 'Setlist.fm')],
        db_index=True
    )
    is_custom_offline = models.BooleanField(
        default=False,
        help_text="True if this concert is marked as an offline show not on Setlist.fm"
    )
    is_fully_matched = models.BooleanField(default=False)
    is_partially_matched = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-date', '-id']

    def __str__(self):
        d_str = self.date.strftime("%m-%d-%Y") if self.date else self.raw_date
        return f"{d_str}: {self.primary_artist} @ {self.venue.name if self.venue else self.raw_venue}"

class ConcertArtist(models.Model):
    concert = models.ForeignKey(Concert, on_delete=models.CASCADE, related_name='artists')
    artist = models.ForeignKey('catalog.Artist', on_delete=models.CASCADE, related_name='concert_appearances')
    billing_order = models.IntegerField(default=0)
    setlistfm_id = models.CharField(max_length=100, blank=True, default='')
    setlist_url = models.URLField(max_length=500, blank=True, default='')
    has_setlist = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['concert', 'billing_order']

    def __str__(self):
        return f"{self.concert} - {self.artist.name}"

class ConcertSong(models.Model):
    concert_artist = models.ForeignKey(ConcertArtist, on_delete=models.CASCADE, related_name='songs')
    song = models.ForeignKey('catalog.Song', on_delete=models.CASCADE, related_name='performances')
    raw_song_name = models.CharField(max_length=255)
    set_name = models.CharField(max_length=100, blank=True, default='Main Set')
    is_encore = models.BooleanField(default=False)
    encore_number = models.IntegerField(null=True, blank=True)
    track_num = models.IntegerField(default=1)
    total_tracks = models.IntegerField(default=1)
    pct_position = models.IntegerField(default=0)
    slot = models.CharField(max_length=100, default='Mid-Set')
    slot_category = models.CharField(max_length=50, default='mid')
    is_cover = models.BooleanField(default=False)
    original_artist = models.CharField(max_length=255, blank=True, default='')
    info = models.CharField(max_length=500, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['concert_artist', 'track_num']

    def __str__(self):
        return f"{self.raw_song_name} ({self.concert_artist})"
