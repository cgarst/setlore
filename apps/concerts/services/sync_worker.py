import threading
import queue
import time
import logging
from datetime import datetime
from django.utils import timezone
from django.contrib.auth.models import User
from django.db import transaction

from apps.core.models import UserProfile
from apps.catalog.models import Artist, Album, Song, Venue, ApiCache, MusicianTenure
from apps.concerts.models import Concert, ConcertArtist, ConcertSong

from src.setlist_api import SetlistFMClient
from src.gap_analysis import reconcile_history
from src.analytics import ConcertAnalytics
from src.album_enricher import AlbumEnricher
from src.musician_enricher import MusicianEnricher
from src.venue_mapper import generate_venue_map_data
from src.musician_tracker import analyze_musicians_live
from src.config import SETLISTFM_API_KEY

logger = logging.getLogger(__name__)

class SyncWorker:
    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._queue = queue.Queue()
                cls._instance._thread = threading.Thread(target=cls._instance._worker_loop, daemon=True)
                cls._instance._thread.start()
        return cls._instance

    def enqueue_sync(self, user_id: int):
        self._queue.put(user_id)
        try:
            profile = UserProfile.objects.get(user_id=user_id)
            profile.sync_status = 'syncing'
            profile.sync_progress = 'Queued for sync...'
            profile.save(update_fields=['sync_status', 'sync_progress'])
        except Exception:
            pass

    def _worker_loop(self):
        while True:
            try:
                user_id = self._queue.get()
                self._process_user_sync(user_id)
            except Exception as e:
                logger.exception("Error in sync worker loop: %s", e)
            finally:
                self._queue.task_done()
                time.sleep(1)

    def _process_user_sync(self, user_id: int):
        user = User.objects.filter(id=user_id).first()
        if not user:
            return
        profile = user.profile

        def update_progress(msg: str):
            profile.sync_progress = msg
            profile.save(update_fields=['sync_progress'])

        try:
            profile.sync_status = 'syncing'
            profile.save(update_fields=['sync_status'])

            setlist_username = profile.setlistfm_username or user.username
            api_key = profile.setlistfm_api_key or SETLISTFM_API_KEY
            if not api_key:
                raise ValueError("No Setlist.fm API key configured. Please set in .env or your profile.")

            update_progress(f"Connecting to Setlist.fm for @{setlist_username}...")
            client = SetlistFMClient(api_key=api_key)
            user_attended = client.get_user_attended(setlist_username, use_cache=True)

            update_progress(f"Retrieved {len(user_attended)} attended setlists. Reconciling with concert history...")

            # Build CSV-like records from DB Concerts
            db_concerts = Concert.objects.filter(user=user).select_related('venue').prefetch_related('artists__artist')
            csv_records = []
            for c in db_concerts:
                artist_names = [ca.artist.name for ca in c.artists.all()]
                dt = datetime.combine(c.date, datetime.min.time()) if c.date else None
                csv_records.append({
                    "id": f"concert_{c.id}",
                    "db_id": c.id,
                    "date": c.date.strftime("%d-%m-%Y") if c.date else None,
                    "raw_date": c.raw_date,
                    "display_date": c.date.strftime("%m-%d-%Y") if c.date else c.raw_date,
                    "date_obj": dt,
                    "year": c.year,
                    "raw_artists": c.raw_artists,
                    "artists": artist_names,
                    "primary_artist": c.primary_artist,
                    "venue": c.raw_venue or (c.venue.name if c.venue else ""),
                    "seen_before": c.seen_before
                })

            gap_results = reconcile_history(csv_records, user_attended, client=client, ignored_artists=profile.ignored_artists)

            update_progress(f"Matched {gap_results['matched_count']}/{gap_results['total_csv']} concerts. Analyzing songs...")

            analytics = ConcertAnalytics(gap_results["matched"], csv_records, ignored_artists=profile.ignored_artists)
            stats = analytics.compute_all_metrics()

            # 1. Update database records for ConcertArtists with setlist matches
            matched_pairs = gap_results["matched"]
            with transaction.atomic():
                for p in matched_pairs:
                    rec = p.get("csv", {})
                    c_id = rec.get("db_id")
                    art_name = p.get("artist")
                    sl = p.get("setlist")
                    if c_id and sl:
                        c_obj = Concert.objects.filter(id=c_id).first()
                        if c_obj:
                            ca = c_obj.artists.filter(artist__name__iexact=art_name).first()
                            if ca:
                                ca.setlistfm_id = sl.get("id", "")
                                ca.setlist_url = sl.get("url", "")
                                ca.has_setlist = True
                                ca.save(update_fields=['setlistfm_id', 'setlist_url', 'has_setlist'])

            # 2. Immediately cache the initial bundle with whatever album data is locally available
            enricher = AlbumEnricher()
            cached_enrichments, uncached_pairs, total_unique = enricher.load_cached_catalog(stats["all_songs_list"])

            stats["concerts_drilldown"] = analytics.compute_concert_drilldown(cached_enrichments)
            stats["venue_map"] = generate_venue_map_data(csv_records, gap_results["matched"])
            stats["musicians"] = analyze_musicians_live(csv_records)

            cache_key = f"user_dashboard_bundle_{user_id}"
            ApiCache.objects.update_or_create(
                cache_key=cache_key,
                defaults={
                    'endpoint': 'dashboard_bundle',
                    'payload': {
                        'gap_results': gap_results,
                        'stats': stats,
                        'album_enrichments': cached_enrichments
                    }
                }
            )

            # 3. If there are uncached songs, fetch them in the background with progress reporting
            album_enrichments = cached_enrichments
            if uncached_pairs:
                def on_progress(done, total, song_disp):
                    pct = round((done / total) * 100)
                    update_progress(f"Enriching albums: {done}/{total} ({pct}%) - {song_disp}")

                def on_batch(partial_enrichments):
                    stats["concerts_drilldown"] = analytics.compute_concert_drilldown(partial_enrichments)
                    ApiCache.objects.update_or_create(
                        cache_key=cache_key,
                        defaults={
                            'endpoint': 'dashboard_bundle',
                            'payload': {
                                'gap_results': gap_results,
                                'stats': stats,
                                'album_enrichments': partial_enrichments
                            }
                        }
                    )

                album_enrichments = enricher.enrich_catalog(
                    stats["all_songs_list"],
                    progress_callback=on_progress,
                    batch_save_callback=on_batch
                )
                stats["concerts_drilldown"] = analytics.compute_concert_drilldown(album_enrichments)
                ApiCache.objects.update_or_create(
                    cache_key=cache_key,
                    defaults={
                        'endpoint': 'dashboard_bundle',
                        'payload': {
                            'gap_results': gap_results,
                            'stats': stats,
                            'album_enrichments': album_enrichments
                        }
                    }
                )

            # 4. Enrich musician lineup tenures for artists in user's concert history
            try:
                update_progress("Enriching musician lineup tenures...")
                m_enricher = MusicianEnricher()
                user_artist_ids = ConcertArtist.objects.filter(concert__user=user).values_list('artist_id', flat=True).distinct()
                user_artists = Artist.objects.filter(id__in=user_artist_ids)
                for art in user_artists:
                    if not MusicianTenure.objects.filter(artist=art).exists():
                        m_enricher.enrich_artist(art.name, artist_obj=art)
                stats["musicians"] = analyze_musicians_live(csv_records)
                ApiCache.objects.update_or_create(
                    cache_key=cache_key,
                    defaults={
                        'endpoint': 'dashboard_bundle',
                        'payload': {
                            'gap_results': gap_results,
                            'stats': stats,
                            'album_enrichments': album_enrichments
                        }
                    }
                )
            except Exception as e:
                logger.warning("Error enriching musicians during sync: %s", e)

            profile.sync_status = 'completed'
            profile.last_synced_at = timezone.now()
            profile.sync_progress = f"Sync complete! {gap_results['matched_count']} shows matched, {stats['total_songs_heard']} songs analyzed."
            profile.save(update_fields=['sync_status', 'last_synced_at', 'sync_progress'])
            logger.info("Successfully finished sync for user %s", user.username)

        except Exception as e:
            logger.exception("Failed sync for user %s: %s", user.username, e)
            profile.sync_status = 'error'
            profile.sync_progress = f"Error: {str(e)[:200]}"
            profile.save(update_fields=['sync_status', 'sync_progress'])

sync_worker = SyncWorker()
