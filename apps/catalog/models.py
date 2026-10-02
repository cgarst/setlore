from django.db import models
from django.core.serializers.json import DjangoJSONEncoder

class Artist(models.Model):
    name = models.CharField(max_length=255, unique=True)
    normalized_name = models.CharField(max_length=255, db_index=True)
    mbid = models.CharField(max_length=36, blank=True, null=True, help_text="MusicBrainz Artist UUID")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name

    @classmethod
    def get_or_create_artist(cls, name: str):
        from src.csv_parser import normalize_artist_name
        can_name = (normalize_artist_name(name) if normalize_artist_name else "") or (name or "").strip()
        if not can_name:
            return None, False
        norm = can_name.lower()
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
        try:
            return cls.objects.create(name=display_name, normalized_name=norm), True
        except Exception:
            existing = cls.objects.filter(normalized_name=norm).first() or cls.objects.filter(name__iexact=can_name).first()
            if existing:
                return existing, False
            raise


class Album(models.Model):
    artist = models.ForeignKey(Artist, on_delete=models.CASCADE, related_name='albums')
    title = models.CharField(max_length=255)
    clean_title = models.CharField(max_length=255, db_index=True)
    release_year = models.IntegerField(null=True, blank=True, db_index=True)
    album_type = models.CharField(max_length=50, default='album')
    mbid = models.CharField(max_length=36, blank=True, null=True, help_text="MusicBrainz Release Group UUID")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['artist', 'release_year', 'title']
        constraints = [
            models.UniqueConstraint(fields=['artist', 'clean_title'], name='unique_artist_album')
        ]

    def __str__(self):
        return f"{self.artist.name} - {self.title} ({self.release_year or 'Unknown'})"

class Song(models.Model):
    artist = models.ForeignKey(Artist, on_delete=models.CASCADE, related_name='songs')
    album = models.ForeignKey(Album, on_delete=models.SET_NULL, null=True, blank=True, related_name='songs')
    title = models.CharField(max_length=255)
    clean_title = models.CharField(max_length=255, db_index=True)
    release_year = models.IntegerField(null=True, blank=True)
    is_cover = models.BooleanField(default=False)
    original_artist = models.CharField(max_length=255, blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['artist', 'title']
        constraints = [
            models.UniqueConstraint(fields=['artist', 'clean_title'], name='unique_artist_song')
        ]

    def __str__(self):
        return f"{self.artist.name} - {self.title}"

class Venue(models.Model):
    name = models.CharField(max_length=255, unique=True)
    city = models.CharField(max_length=150, blank=True, default='')
    state = models.CharField(max_length=100, blank=True, default='')
    country = models.CharField(max_length=100, blank=True, default='United States')
    latitude = models.FloatField(null=True, blank=True)
    longitude = models.FloatField(null=True, blank=True)
    geocode_source = models.CharField(max_length=50, default='unknown')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        loc = f" ({self.city}, {self.state})" if self.city and self.state else ""
        return f"{self.name}{loc}"

class MusicianTenure(models.Model):
    musician_name = models.CharField(max_length=255, db_index=True)
    musician_mbid = models.CharField(max_length=36, blank=True, null=True, db_index=True, help_text="MusicBrainz Artist UUID for the musician")
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

