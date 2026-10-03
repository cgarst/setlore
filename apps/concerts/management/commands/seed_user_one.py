import os
from django.core.management.base import BaseCommand
from django.contrib.auth.models import User
from django.db import transaction

from apps.core.models import UserProfile
from apps.catalog.models import Artist, Album, Song, Venue, MusicianTenure
from apps.concerts.models import Concert, ConcertArtist

from src.config import DEFAULT_SHEET_URL, SETLISTFM_USER
from src.csv_parser import parse_concerts_source, normalize_artist_name
from src.venue_mapper import VENUE_COORDINATES, CANONICAL_VENUE_NAMES
from src.musician_tracker import BAND_MEMBERS_TENURE
from src.album_enricher import CANONICAL_ALBUM_YEARS, CANONICAL_TRACK_ALBUMS, clean_album_title

class Command(BaseCommand):
    help = "Seeds User 1 (admin), populates canonical music catalog, and imports initial concert history"

    def add_arguments(self, parser):
        parser.add_argument('--force-import', action='store_true', help='Force re-import of concerts even if already populated')
        parser.add_argument('--source', default=None, help='Custom CSV or Google Sheets URL')

    def handle(self, *args, **options):
        self.stdout.write(self.style.MIGRATE_HEADING("[1/4] Setting up User 1 (Admin)..."))

        admin_username = os.getenv("ADMIN_USERNAME", SETLISTFM_USER or "admin").strip()
        admin_password = os.getenv("ADMIN_PASSWORD", "admin12345").strip()
        admin_email = os.getenv("ADMIN_EMAIL", "admin@example.com").strip()
        setlistfm_user = os.getenv("SETLISTFM_USER", "").strip()

        user, created = User.objects.get_or_create(username=admin_username, defaults={'email': admin_email})
        user.is_staff = True
        user.is_superuser = True
        if created:
            user.set_password(admin_password)
            user.save()
            self.stdout.write(self.style.SUCCESS(f"      Created Superuser '{admin_username}' (Password: {admin_password})"))
        else:
            self.stdout.write(f"      Superuser '{admin_username}' already exists.")

        profile, _ = UserProfile.objects.get_or_create(user=user)
        if setlistfm_user:
            profile.setlistfm_username = setlistfm_user
            profile.save()
            self.stdout.write(f"      Linked Setlist.fm account: @{setlistfm_user}")

        self.stdout.write(self.style.MIGRATE_HEADING("[2/4] Seeding canonical Venues, Lineups & Discography..."))
        with transaction.atomic():
            # Seed Venues
            v_created = 0
            for v_name, (lat, lng, city_state, country) in VENUE_COORDINATES.items():
                canonical_v_name = CANONICAL_VENUE_NAMES.get(v_name.lower(), v_name.title())
                city = city_state.split(',')[0].strip() if ',' in city_state else city_state
                state = city_state.split(',')[1].strip() if ',' in city_state else ''
                _, is_new = Venue.objects.get_or_create(
                    name=canonical_v_name,
                    defaults={
                        'city': city,
                        'state': state,
                        'country': country,
                        'latitude': lat,
                        'longitude': lng,
                        'geocode_source': 'canonical_lookup'
                    }
                )
                if is_new:
                    v_created += 1
            self.stdout.write(f"      Populated {v_created} canonical venues.")

            # Seed Musician Tenures & Canonical Artists
            m_created = 0
            for band_key, members in BAND_MEMBERS_TENURE.items():
                canonical_band_name = normalize_artist_name(band_key) or band_key.title()
                artist_obj, _ = Artist.get_or_create_artist(canonical_band_name)

                for mem in members:
                    m_name = mem["musician"]
                    m_role = mem["role"]
                    m_start = mem.get("start", 1900)
                    m_end = mem.get("end")

                    r_low = m_role.lower()
                    if "drum" in r_low:
                        instr = "Drums"
                    elif "guitar" in r_low:
                        instr = "Guitar"
                    elif "bass" in r_low:
                        instr = "Bass"
                    elif "vocal" in r_low or "singer" in r_low:
                        instr = "Vocals"
                    elif "key" in r_low or "piano" in r_low or "synth" in r_low:
                        instr = "Keyboards"
                    else:
                        instr = "Other"

                    _, is_m_new = MusicianTenure.objects.get_or_create(
                        artist=artist_obj,
                        musician_name=m_name,
                        start_year=m_start,
                        end_year=m_end,
                        defaults={
                            'role': m_role,
                            'instrument': instr
                        }
                    )
                    if is_m_new:
                        m_created += 1
            self.stdout.write(f"      Populated {m_created} band member tenures across canonical artists.")

            # Seed Canonical Albums
            a_created = 0
            for (art_norm, alb_title), yr in CANONICAL_ALBUM_YEARS.items():
                art_name = normalize_artist_name(art_norm) or art_norm.title()
                art_obj, _ = Artist.get_or_create_artist(art_name)
                clean_t = clean_album_title(alb_title)
                _, is_a_new = Album.objects.get_or_create(
                    artist=art_obj,
                    clean_title=clean_t.lower(),
                    defaults={'title': clean_t, 'release_year': yr, 'album_type': 'album'}
                )
                if is_a_new:
                    a_created += 1
            self.stdout.write(f"      Populated {a_created} canonical studio albums.")

            # Seed Canonical Tracks
            t_created = 0
            for (art_norm, song_norm), (alb_name, yr) in CANONICAL_TRACK_ALBUMS.items():
                art_name = normalize_artist_name(art_norm) or art_norm.title()
                art_obj, _ = Artist.get_or_create_artist(art_name)
                clean_alb = clean_album_title(alb_name)
                alb_obj, _ = Album.objects.get_or_create(
                    artist=art_obj,
                    clean_title=clean_alb.lower(),
                    defaults={'title': clean_alb, 'release_year': yr}
                )

                _, is_t_new = Song.objects.get_or_create(
                    artist=art_obj,
                    clean_title=song_norm.lower(),
                    defaults={
                        'title': song_norm.title(),
                        'album': alb_obj,
                        'release_year': yr,
                        'is_cover': False
                    }
                )
                if is_t_new:
                    t_created += 1
            self.stdout.write(f"      Populated {t_created} canonical track mappings.")

        # [3/4] Ingest User 1 Concerts
        self.stdout.write(self.style.MIGRATE_HEADING("[3/4] Ingesting concert history for User 1..."))
        existing_concert_count = Concert.objects.filter(user=user).count()

        if existing_concert_count > 0 and not options['force_import']:
            self.stdout.write(f"      User '{admin_username}' already has {existing_concert_count} concerts. Skipping re-import.")
        else:
            if options['force_import'] and existing_concert_count > 0:
                self.stdout.write("      --force-import specified. Clearing existing concerts for User 1...")
                Concert.objects.filter(user=user).delete()

            source_url = options['source'] or DEFAULT_SHEET_URL
            if not source_url:
                self.stdout.write("      No concert source provided (DEFAULT_SHEET_URL not set). Skipping concert ingestion.")
                self.stdout.write("      Use --source <file-path-or-url> to populate concerts for User 1.")
            else:
                self.stdout.write(f"      Fetching concerts from: {source_url[:60]}...")
                try:
                    csv_records = parse_concerts_source(source_url)
                    self.stdout.write(f"      Parsed {len(csv_records)} concert entries.")

                    with transaction.atomic():
                        c_count = 0
                        for rec in csv_records:
                            dt = rec.get("date_obj")
                            venue_str = rec.get("venue", "").strip()

                            venue_obj = None
                            if venue_str:
                                v_clean = venue_str.lower()
                                venue_obj = Venue.objects.filter(name__iexact=v_clean).first()
                                if not venue_obj:
                                    venue_obj, _ = Venue.objects.get_or_create(
                                        name=venue_str,
                                        defaults={
                                            'city': '',
                                            'state': '',
                                            'country': 'United States',
                                            'geocode_source': 'unresolved'
                                        }
                                    )

                            prim_art_name = rec.get("primary_artist", "")
                            prim_art_obj = None
                            if prim_art_name:
                                canonical_prim = normalize_artist_name(prim_art_name) or prim_art_name
                                prim_art_obj, _ = Artist.get_or_create_artist(canonical_prim)
                            elif rec.get("artists"):
                                first_art = rec.get("artists")[0]
                                canonical_prim = normalize_artist_name(first_art) or first_art
                                prim_art_obj, _ = Artist.get_or_create_artist(canonical_prim)

                            concert = Concert.objects.create(
                                user=user,
                                date=dt.date() if dt else None,
                                raw_date=rec.get("raw_date", ""),
                                year=rec.get("year"),
                                venue=venue_obj,
                                raw_venue=venue_str,
                                raw_artists=rec.get("raw_artists", ""),
                                seen_before="",
                                notes=""
                            )

                            for idx, art_name in enumerate(rec.get("artists", [])):
                                canonical_art = normalize_artist_name(art_name) or art_name
                                art_obj, _ = Artist.get_or_create_artist(canonical_art)
                                ConcertArtist.objects.create(
                                    concert=concert,
                                    artist=art_obj,
                                    billing_order=idx
                                )

                            c_count += 1

                    self.stdout.write(self.style.SUCCESS(f"      Successfully imported {c_count} concerts for User '{admin_username}'!"))
                except Exception as e:
                    self.stdout.write(self.style.ERROR(f"      Failed to import concert history: {e}"))

        self.stdout.write(self.style.SUCCESS("[4/4] User 1 seeding and catalog initialization complete!\n"))
