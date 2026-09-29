from django.core.management.base import BaseCommand
from django.contrib.auth.models import User
from apps.catalog.models import Artist, MusicianTenure
from apps.concerts.models import ConcertArtist
from src.musician_enricher import MusicianEnricher
from src.csv_parser import normalize_artist_name

class Command(BaseCommand):
    help = "Enriches artist catalog with dynamic musician tenures from MusicBrainz without duplicating existing records."

    def add_arguments(self, parser):
        parser.add_argument(
            '--artist',
            type=str,
            help='Specific artist name to enrich (e.g. --artist "Dream Theater")'
        )
        parser.add_argument(
            '--user',
            type=str,
            help='Enrich all artists seen live by a specific user'
        )
        parser.add_argument(
            '--all',
            action='store_true',
            help='Enrich all artists in the catalog database'
        )
        parser.add_argument(
            '--refresh',
            action='store_true',
            help='Bypass local cache and re-query MusicBrainz'
        )

    def handle(self, *args, **options):
        artist_arg = options.get('artist')
        user_arg = options.get('user')
        all_arg = options.get('all')
        refresh = options.get('refresh', False)

        enricher = MusicianEnricher()

        artists_to_enrich = []

        if artist_arg:
            canon = normalize_artist_name(artist_arg) or artist_arg.strip()
            art_obj, _ = Artist.objects.get_or_create(
                name=canon,
                defaults={'normalized_name': canon.lower()}
            )
            artists_to_enrich.append(art_obj)
        elif user_arg:
            try:
                user = User.objects.get(username__iexact=user_arg)
                art_ids = ConcertArtist.objects.filter(concert__user=user).values_list('artist_id', flat=True).distinct()
                artists_to_enrich = list(Artist.objects.filter(id__in=art_ids).order_by('name'))
                self.stdout.write(f"Found {len(artists_to_enrich)} unique artists for user '{user_arg}'.")
            except User.DoesNotExist:
                self.stderr.write(f"User '{user_arg}' not found.")
                return
        elif all_arg:
            artists_to_enrich = list(Artist.objects.all().order_by('name'))
            self.stdout.write(f"Enriching all {len(artists_to_enrich)} artists in the catalog.")
        else:
            self.stdout.write("Please specify --artist, --user, or --all. Run with --help for details.")
            return

        total_created = 0
        total_updated = 0
        total_artists = len(artists_to_enrich)

        for idx, art_obj in enumerate(artists_to_enrich, 1):
            name = art_obj.name
            self.stdout.write(f"[{idx}/{total_artists}] Processing '{name}'...")

            existing_count = MusicianTenure.objects.filter(artist=art_obj).count()
            tenures = enricher.enrich_artist(name, artist_obj=art_obj, refresh=refresh)

            new_count = MusicianTenure.objects.filter(artist=art_obj).count()
            diff = new_count - existing_count

            if tenures:
                self.stdout.write(self.style.SUCCESS(
                    f"      -> {len(tenures)} MusicBrainz member relations found ({diff} new tenures added, {new_count} total)."
                ))
            else:
                self.stdout.write(f"      -> No MusicBrainz member relations found for '{name}'.")

            total_created += diff

        self.stdout.write(self.style.SUCCESS(
            f"\nCompleted! Added {total_created} new musician tenures across {total_artists} artists (0 duplicates created)."
        ))
