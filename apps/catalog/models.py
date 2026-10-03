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

    @mbid.setter
    def mbid(self, value):
        if value:
            self.id = value
            self.is_custom_offline = is_offline_id(value)

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

    @mbid.setter
    def mbid(self, value):
        if value:
            self.id = value
            self.is_custom_offline = is_offline_id(value)

    def save(self, *args, **kwargs):
        if not self.id:
            self.id = generate_offline_album_id(self.artist_id, self.clean_title)
        self.is_custom_offline = is_offline_id(self.id)
        super().save(*args, **kwargs)

    @classmethod
    def get_or_create_album(cls, artist, title: str, mbid: str = None, release_year: int = None, album_type: str = 'album'):
        from apps.catalog.models import Song
        if not title:
            return None, False
        clean_title = title.lower().strip()

        # 1. Check if an album with id=mbid already exists
        mbid_album = None
        if mbid:
            mbid_album = cls.objects.filter(id=mbid).first()

        # 2. Check if an album with (artist, clean_title) already exists
        title_album = cls.objects.filter(artist=artist, clean_title=clean_title).first()

        # 3. Handle both existing
        if mbid_album and title_album:
            if mbid_album.id == title_album.id:
                if release_year and not mbid_album.release_year:
                    mbid_album.release_year = release_year
                    mbid_album.save(update_fields=['release_year'])
                return mbid_album, False
            else:
                # Merge title_album into mbid_album
                Song.objects.filter(album=title_album).update(album=mbid_album)
                title_album.delete()
                if release_year and not mbid_album.release_year:
                    mbid_album.release_year = release_year
                    mbid_album.save(update_fields=['release_year'])
                return mbid_album, False

        # 4. Handle only mbid_album existing
        if mbid_album:
            if release_year and not mbid_album.release_year:
                mbid_album.release_year = release_year
                mbid_album.save(update_fields=['release_year'])
            return mbid_album, False

        # 5. Handle only title_album existing
        if title_album:
            if mbid and title_album.id != mbid:
                # Re-key title_album to mbid
                try:
                    saved_songs = list(Song.objects.filter(album=title_album))
                    artist_ref = title_album.artist
                    title_ref = title_album.title
                    clean_ref = title_album.clean_title
                    year_ref = release_year or title_album.release_year
                    type_ref = album_type or title_album.album_type
                    title_album.delete()
                    new_alb = cls.objects.create(
                        id=mbid,
                        artist=artist_ref,
                        title=title_ref,
                        clean_title=clean_ref,
                        release_year=year_ref,
                        album_type=type_ref,
                        is_custom_offline=False
                    )
                    for s in saved_songs:
                        s.album = new_alb
                        s.save(update_fields=['album'])
                    return new_alb, False
                except Exception:
                    existing = cls.objects.filter(id=mbid).first() or cls.objects.filter(artist=artist, clean_title=clean_title).first()
                    if existing:
                        return existing, False
                    raise
            else:
                if release_year and not title_album.release_year:
                    title_album.release_year = release_year
                    title_album.save(update_fields=['release_year'])
                return title_album, False

        # 6. Neither exists -> create
        target_id = mbid or generate_offline_album_id(artist.id, clean_title)
        try:
            new_alb = cls.objects.create(
                id=target_id,
                artist=artist,
                title=title,
                clean_title=clean_title,
                release_year=release_year,
                album_type=album_type or 'album',
                is_custom_offline=is_offline_id(target_id)
            )
            return new_alb, True
        except Exception:
            existing = cls.objects.filter(id=target_id).first() or cls.objects.filter(artist=artist, clean_title=clean_title).first()
            if existing:
                return existing, False
            raise


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

    @mbid.setter
    def mbid(self, value):
        if value:
            self.id = value
            self.is_custom_offline = is_offline_id(value)

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

    @setlistfm_id.setter
    def setlistfm_id(self, value):
        if value:
            self.id = value
            self.is_custom_offline = is_offline_id(value)

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

    @musician_mbid.setter
    def musician_mbid(self, value):
        self.musician_id = value


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
