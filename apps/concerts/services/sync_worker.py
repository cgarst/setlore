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
from apps.concerts.utils import build_manual_setlist_for_concert_artist

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
                cls._instance._queued_user_ids = set()
                cls._instance._cancelled_user_ids = set()
                cls._instance._thread = threading.Thread(target=cls._instance._worker_loop, daemon=True)
                cls._instance._thread.start()
        return cls._instance

    def resume_interrupted_syncs(self):
        """
        Scans for user profiles that were marked as 'syncing' when the server last stopped
        or restarted, and enqueues them to continue processing automatically.
        """
        try:
            interrupted = list(UserProfile.objects.filter(sync_status='syncing').values_list('user_id', flat=True))
            for uid in interrupted:
                logger.info("Resuming in-process sync for user_id=%d after server startup/restart", uid)
                self.enqueue_sync(uid)
        except Exception as e:
            logger.warning("Could not check for interrupted syncs on startup: %s", e)

    def cancel_sync(self, user_id: int):
        """
        Requests cancellation of an ongoing or queued sync for a user.
        """
        with self._lock:
            self._cancelled_user_ids.add(user_id)
            self._queued_user_ids.discard(user_id)
        try:
            profile = UserProfile.objects.filter(user_id=user_id).first()
            if profile:
                profile.sync_status = 'idle'
                profile.sync_progress = 'Sync cancelled'
                profile.save(update_fields=['sync_status', 'sync_progress'])
        except Exception:
            pass

    def enqueue_sync(self, user_id: int):
        with self._lock:
            self._cancelled_user_ids.discard(user_id)
            if user_id in self._queued_user_ids:
                return
            self._queued_user_ids.add(user_id)
            self._queue.put(user_id)
        try:
            profile = UserProfile.objects.get(user_id=user_id)
            profile.sync_status = 'syncing'
            profile.sync_progress = 'Queued for sync...'
            profile.save(update_fields=['sync_status', 'sync_progress'])
        except Exception:
            pass

    def _worker_loop(self):
        # On worker startup, check for any in-process syncs from previous run
        self.resume_interrupted_syncs()
        while True:
            try:
                user_id = self._queue.get()
                with self._lock:
                    self._queued_user_ids.discard(user_id)
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

        def is_cancelled() -> bool:
            with self._lock:
                return user_id in self._cancelled_user_ids

        def update_progress(msg: str):
            if is_cancelled():
                return
            for _ in range(5):
                try:
                    profile.sync_progress = msg
                    profile.save(update_fields=['sync_progress'])
                    break
                except Exception:
                    time.sleep(0.2)

        try:
            if is_cancelled():
                with self._lock:
                    self._cancelled_user_ids.discard(user_id)
                profile.sync_status = 'idle'
                profile.sync_progress = 'Sync cancelled'
                profile.save(update_fields=['sync_status', 'sync_progress'])
                return

            profile.sync_status = 'syncing'
            profile.save(update_fields=['sync_status'])

            setlist_username = (profile.setlistfm_username or "").strip()
            api_key = profile.setlistfm_api_key or SETLISTFM_API_KEY
            client = None
            if api_key:
                try:
                    client = SetlistFMClient(api_key=api_key)
                except Exception as e:
                    logger.warning("Could not initialize SetlistFMClient: %s", e)

            user_attended = []
            if setlist_username and client:
                update_progress(f"Connecting to Setlist.fm for @{setlist_username}...")
                user_attended = client.get_user_attended(setlist_username, use_cache=True)
                if user_attended:
                    update_progress(f"Synchronizing {len(user_attended)} attended setlists into database...")
                    from apps.concerts.utils import import_setlistfm_shows_into_database
                    import_setlistfm_shows_into_database(user, user_attended, ignored_artists=profile.ignored_artists)
                update_progress(f"Retrieved {len(user_attended)} attended setlists. Reconciling with concert history...")
            else:
                update_progress("Reconciling concerts and metadata...")

            # Build CSV-like records from DB Concerts
            db_concerts = Concert.objects.filter(user=user).select_related('venue').prefetch_related('artists__artist', 'artists__songs')
            csv_records = []
            manual_matched_pairs = []

            for c in db_concerts:
                ca_list = list(c.artists.all())
                artist_names = [ca.artist.name for ca in ca_list if ca.artist]
                artist_favorites = {ca.artist.name.lower().strip(): bool(ca.is_favorite) for ca in ca_list if ca.artist}
                artist_ca_ids = {ca.artist.name.lower().strip(): ca.id for ca in ca_list if ca.artist}
                dt = datetime.combine(c.date, datetime.min.time()) if c.date else None
                has_sl_id = any(bool(ca.setlistfm_id) for ca in ca_list)
                rec = {
                    "id": f"concert_{c.id}",
                    "db_id": c.id,
                    "date": c.date.strftime("%d-%m-%Y") if c.date else None,
                    "raw_date": c.raw_date,
                    "display_date": c.date.strftime("%m-%d-%Y") if c.date else c.raw_date,
                    "date_obj": dt,
                    "year": c.year,
                    "raw_artists": c.raw_artists,
                    "artists": artist_names,
                    "artist_favorites": artist_favorites,
                    "artist_ca_ids": artist_ca_ids,
                    "primary_artist": c.primary_artist or (artist_names[0] if artist_names else ""),
                    "supporting_artists": ", ".join(artist_names[1:]) if len(artist_names) > 1 else "",
                    "venue": c.raw_venue or (c.venue.name if c.venue else ""),
                    "city": c.venue.city if c.venue else "",
                    "state": c.venue.state if c.venue else "",
                    "country": c.venue.country if c.venue else "United States",
                    "seen_before": c.seen_before,
                    "notes": c.notes,
                    "is_custom_offline": c.is_custom_offline,
                    "source": c.source,
                    "has_setlistfm_id": has_sl_id,
                    "is_favorite": bool(c.is_favorite),
                }
                csv_records.append(rec)

                for ca in c.artists.all():
                    if ca.songs.exists():
                        sl_dict = build_manual_setlist_for_concert_artist(ca)
                        if sl_dict:
                            manual_matched_pairs.append({
                                "csv": rec,
                                "artist": ca.artist.name,
                                "setlist": sl_dict,
                                "score": 100.0,
                                "is_manual": True
                            })

            gap_results = reconcile_history(csv_records, user_attended, client=client, ignored_artists=profile.ignored_artists)

            # Merge manual matched pairs without duplicating already-matched pairs
            if manual_matched_pairs:
                existing_keys = {(p["csv"]["id"], p["artist"].lower().strip()) for p in gap_results.get("matched", [])}
                existing_sl_ids = {p["setlist"].get("id") for p in gap_results.get("matched", []) if isinstance(p.get("setlist"), dict) and p["setlist"].get("id")}
                for mp in manual_matched_pairs:
                    mp_key = (mp["csv"]["id"], mp["artist"].lower().strip())
                    mp_sl_id = mp["setlist"].get("id") if isinstance(mp.get("setlist"), dict) else None
                    if mp_key in existing_keys or (mp_sl_id and mp_sl_id in existing_sl_ids):
                        continue
                    gap_results["matched"].append(mp)
                    existing_keys.add(mp_key)
                    if mp_sl_id:
                        existing_sl_ids.add(mp_sl_id)
                    rec_id = mp["csv"]["id"]
                    if rec_id in gap_results.get("csv_status", {}):
                        st = gap_results["csv_status"][rec_id]
                        if mp["artist"] not in st["matched_bands"]:
                            st["matched_bands"].append(mp["artist"])
                        st["setlists"].append(mp["setlist"])
                        st["missing_bands_info"] = [b for b in st["missing_bands_info"] if b["artist"].lower() != mp["artist"].lower()]
                        st["missing_bands"] = [b["artist"] for b in st["missing_bands_info"]]
                        st["is_fully_matched"] = (len(st["missing_bands"]) == 0)
                        st["is_partially_matched"] = (len(st["matched_bands"]) > 0 and len(st["missing_bands"]) > 0)
                        st["is_unmatched"] = (len(st["matched_bands"]) == 0)

            update_progress(f"Matched {gap_results['matched_count']}/{gap_results['total_csv']} concerts. Analyzing songs...")

            analytics = ConcertAnalytics(gap_results["matched"], csv_records, ignored_artists=profile.ignored_artists)
            stats = analytics.compute_all_metrics()

            # 1. Update database records for ConcertArtists with setlist matches
            matched_pairs = gap_results["matched"]
            with transaction.atomic():
                for p in matched_pairs:
                    if p.get("is_manual"):
                        continue
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
            cached_enrichments, uncached_pairs, total_unique = enricher.load_cached_catalog(stats["all_songs_list"], refresh_unresolved=True)

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
                    refresh_unresolved=True,
                    progress_callback=on_progress,
                    batch_save_callback=on_batch,
                    cancel_check=is_cancelled
                )
                if is_cancelled():
                    with self._lock:
                        self._cancelled_user_ids.discard(user_id)
                    profile.sync_status = 'idle'
                    profile.sync_progress = 'Sync cancelled'
                    profile.save(update_fields=['sync_status', 'sync_progress'])
                    return

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
                
                # Active artists: artists with shows in current or future years
                current_year = timezone.now().year
                active_artist_ids = set(
                    ConcertArtist.objects.filter(
                        concert__user=user,
                        concert__date__year__gte=current_year
                    ).values_list('artist_id', flat=True)
                )

                for art in user_artists:
                    has_tenures = MusicianTenure.objects.filter(artist=art).exists()
                    if not has_tenures:
                        m_enricher.enrich_artist(art.name, artist_obj=art)
                    elif art.id in active_artist_ids:
                        # For active/upcoming artists, refresh MusicBrainz relations to capture lineup changes/departures
                        m_enricher.enrich_artist(art.name, artist_obj=art, refresh=True)

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
