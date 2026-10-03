from django.db import models
from django.core.serializers.json import DjangoJSONEncoder
from src.id_utils import (
    generate_offline_artist_id,
    generate_offline_album_id,
    generate_offline_song_id,
    generate_offline_venue_id,
    is_offline_id
)

class Artist(models.Model):
    id = models.CharField(max_length=64, primary_key=True, help_text="MusicBrainz Artist MBID or offline:artist:<uuid>")
    name = models.CharField(max_length=255, unique=True)
    normalized_name = models.CharField(max_length=255, db_index=True)
    is_custom_offline = models.BooleanField(default=False, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name

    @property
    def mbid(self):
        return self.id if not self.is_custom_offline else None

    def save(self, *args, **kwargs):
        if not self.id:
            self.id = generate_offline_artist_id(self.name)
        self.is_custom_offline = is_offline_id(self.id)
        super().save(*args, **kwargs)

    @classmethod
    def get_or_create_artist(cls, name: str, mbid: str = None):
        from src.csv_parser import normalize_artist_name
        can_name = (normalize_artist_name(name) if normalize_artist_name else "") or (name or "").strip()
        if not can_name:
            return None, False
        norm = can_name.lower()
        art = None
        if mbid:
            art = cls.objects.filter(id=mbid).first()
        if not art:
            art = cls.objects.filter(normalized_name=norm).first()
        if not art:
            art = cls.objects.filter(name__iexact=can_name).first()
            if art and not art.normalized_name:
                art.normalized_name = norm
                try:
                    art.save(update_fields=['normalized_name'])
                except Exception:
                    pass

        if art:
            # Upgrade casing if incoming name is properly capitalized and stored name is lowercase/uppercase
            if (art.name.islower() and not can_name.islower()) or (art.name.isupper() and not can_name.isupper()):
                if not cls.objects.filter(name=can_name).exclude(id=art.id).exists():
                    art.name = can_name
                    try:
                        art.save(update_fields=['name'])
                    except Exception:
                        pass
            return art, False

        # If incoming name is all-lowercase, give it Title Case for display
        display_name = can_name.title() if can_name.islower() else can_name
        artist_id = mbid or generate_offline_artist_id(display_name)
        try:
            return cls.objects.create(
                id=artist_id,
                name=display_name,
                normalized_name=norm,
                is_custom_offline=is_offline_id(artist_id)
            ), True
        except Exception:
            existing = (cls.objects.filter(id=artist_id).first()
                        or cls.objects.filter(normalized_name=norm).first()
                        or cls.objects.filter(name__iexact=can_name).first())
            if existing:
                return existing, False
            raise


class Album(models.Model):
    id = models.CharField(max_length=64, primary_key=True, help_text="MusicBrainz Release Group MBID or offline:album:<uuid>")
    artist = models.ForeignKey(Artist, on_delete=models.CASCADE, related_name='albums')
    title = models.CharField(max_length=255)
    clean_title = models.CharField(max_length=255, db_index=True)
    release_year = models.IntegerField(null=True, blank=True, db_index=True)
    album_type = models.CharField(max_length=50, default='album')
    is_custom_offline = models.BooleanField(default=False, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['artist', 'release_year', 'title']
        constraints = [
            models.UniqueConstraint(fields=['artist', 'clean_title'], name='unique_artist_album')
        ]

    def __str__(self):
        return f"{self.artist.name} - {self.title} ({self.release_year or 'Unknown'})"

    @property
    def mbid(self):
        return self.id if not self.is_custom_offline else None

    def save(self, *args, **kwargs):
        if not self.id:
            self.id = generate_offline_album_id(self.artist_id, self.clean_title)
        self.is_custom_offline = is_offline_id(self.id)
        super().save(*args, **kwargs)


class Song(models.Model):
    id = models.CharField(max_length=64, primary_key=True, help_text="MusicBrainz Recording MBID or offline:song:<uuid>")
    artist = models.ForeignKey(Artist, on_delete=models.CASCADE, related_name='songs')
    album = models.ForeignKey(Album, on_delete=models.SET_NULL, null=True, blank=True, related_name='songs')
    title = models.CharField(max_length=255)
    clean_title = models.CharField(max_length=255, db_index=True)
    release_year = models.IntegerField(null=True, blank=True)
    is_cover = models.BooleanField(default=False)
    original_artist = models.CharField(max_length=255, blank=True, null=True)
    is_custom_offline = models.BooleanField(default=False, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['artist', 'title']
        constraints = [
            models.UniqueConstraint(fields=['artist', 'clean_title'], name='unique_artist_song')
        ]

    def __str__(self):
        return f"{self.artist.name} - {self.title}"

    @property
    def mbid(self):
        return self.id if not self.is_custom_offline else None

    def save(self, *args, **kwargs):
        if not self.id:
            self.id = generate_offline_song_id(self.artist_id, self.clean_title)
        self.is_custom_offline = is_offline_id(self.id)
        super().save(*args, **kwargs)


class Venue(models.Model):
    id = models.CharField(max_length=64, primary_key=True, help_text="Setlist.fm Venue ID or offline:venue:<uuid>")
    name = models.CharField(max_length=255)
    city = models.CharField(max_length=150, blank=True, default='')
    state = models.CharField(max_length=100, blank=True, default='')
    country = models.CharField(max_length=100, blank=True, default='United States')
    latitude = models.FloatField(null=True, blank=True)
    longitude = models.FloatField(null=True, blank=True)
    geocode_source = models.CharField(max_length=50, default='unknown')
    is_custom_offline = models.BooleanField(default=False, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['name']
        constraints = [
            models.UniqueConstraint(fields=['name', 'city', 'state', 'country'], name='unique_venue_location')
        ]

    def __str__(self):
        loc = f" ({self.city}, {self.state})" if self.city and self.state else ""
        return f"{self.name}{loc}"

    @property
    def setlistfm_id(self):
        return self.id if not self.is_custom_offline else ''

    def save(self, *args, **kwargs):
        if not self.id:
            self.id = generate_offline_venue_id(self.name, self.city, self.state, self.country)
        self.is_custom_offline = is_offline_id(self.id)
        super().save(*args, **kwargs)


class MusicianTenure(models.Model):
    musician_name = models.CharField(max_length=255, db_index=True)
    musician_id = models.CharField(max_length=64, blank=True, null=True, db_index=True, help_text="MusicBrainz Artist UUID for the musician")
    artist = models.ForeignKey(Artist, on_delete=models.CASCADE, related_name='members')
    role = models.CharField(max_length=255, default='Musician')
    instrument = models.CharField(max_length=100, default='Other')
    start_year = models.IntegerField(default=1900)
    end_year = models.IntegerField(null=True, blank=True, help_text="Null represents active tenure to present")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['artist', 'start_year', 'musician_name']
        constraints = [
            models.UniqueConstraint(
                fields=['artist', 'musician_name', 'start_year', 'end_year'],
                name='unique_artist_musician_tenure'
            )
        ]

    def __str__(self):
        span = f"{self.start_year}-{self.end_year or 'Present'}"
        return f"{self.musician_name} ({self.artist.name}: {self.role}, {span})"

    @property
    def musician_mbid(self):
        return self.musician_id


class ApiCache(models.Model):
    cache_key = models.CharField(max_length=255, primary_key=True)
    endpoint = models.CharField(max_length=100, db_index=True)
    payload = models.JSONField(encoder=DjangoJSONEncoder)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name_plural = "API Caches"

    def __str__(self):
        return f"[{self.endpoint}] {self.cache_key}"


class MusicBrainzDump(models.Model):
    class Meta:
        managed = False
        verbose_name = "MusicBrainz Dump Manager"
        verbose_name_plural = "MusicBrainz Dump Manager"
