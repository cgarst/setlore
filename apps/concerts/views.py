import io
import csv
import json
import re
from datetime import datetime
from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse, HttpResponseBadRequest, HttpResponse
from django.views.decorators.http import require_POST, require_http_methods, require_GET
from django.db import transaction
from django.db.models import Count

import os
from django.contrib.auth.models import User
from apps.catalog.models import ApiCache, Artist, Venue, Song
from apps.concerts.models import Concert, ConcertArtist, ConcertSong
from apps.core.models import Friendship
from apps.concerts.utils import parse_setlist_text, resolve_venue_coordinates, build_manual_setlist_for_concert_artist, save_setlist_for_concert_artist
from src.reporter import generate_plotly_charts
from src.analytics import ConcertAnalytics
from src.csv_parser import parse_csv_rows, normalize_artist_name
from src.setlist_api import SetlistFMClient
from src.gap_analysis import reconcile_history
from src.album_enricher import AlbumEnricher
from src.musician_enricher import MusicianEnricher
from src.venue_mapper import generate_venue_map_data
from src.musician_tracker import analyze_musicians_live, consolidate_musician_bands
from src.config import SETLISTFM_API_KEY, CARTO_API_KEY, USER_CACHE_DIR, MB_CACHE_DIR
from src.upcoming_events import get_upcoming_shows_for_user, get_user_seen_artists_summary
from .services.sync_worker import sync_worker
import unicodedata

def get_or_create_artist(name: str):
    return Artist.get_or_create_artist(name)

def normalize_track_title(title: str) -> str:
    if not title:
        return ""
    norm = unicodedata.normalize('NFKD', title).encode('ASCII', 'ignore').decode('utf-8').lower()
    norm = re.sub(r'\s*[\(\[].*?[\)\]]', '', norm).strip()
    while True:
        prev = norm
        norm = re.sub(r'^(?:act|scene|part|pt|section|movement|side)\s+[a-z0-9ivxlcdm]+[\.\:\s\-]+', '', norm, flags=re.IGNORECASE).strip()
        norm = re.sub(r'^[ivxlcdm0-9]+[\.\:\s\-]+', '', norm, flags=re.IGNORECASE).strip()
        if norm == prev:
            break
    return re.sub(r'[^a-z0-9]', '', norm)


def get_dashboard_context(request, target_user, tab_name='overview', is_public_view=False):
    alias_map = {
        '': 'overview',
        'overview': 'overview',
        'concerts': 'concerts',
        'songs': 'songs',
        'drilldown': 'songs',
        'artists': 'songs',
        'musicians': 'musicians',
        'map': 'map',
        'venues': 'map',
        'albums': 'albums',
        'advanced': 'albums',
        'freshness': 'freshness',
        'setlists': 'freshness',
        'gap': 'gap',
        'audit': 'gap',
        'friends': 'friends',
        'more': 'more',
    }
    initial_tab = alias_map.get(str(tab_name).lower().strip('/'), 'overview')
    if is_public_view and initial_tab == 'friends':
        initial_tab = 'overview'
    profile = target_user.profile

    cache_entry = ApiCache.objects.filter(cache_key=f"user_dashboard_bundle_{target_user.id}").first()

    if cache_entry and cache_entry.payload:
        bundle = cache_entry.payload
        gap_results = bundle.get("gap_results", {})
        stats = bundle.get("stats", {})
        if "musicians" in stats:
            stats["musicians"] = consolidate_musician_bands(stats["musicians"])
        album_enrichments = bundle.get("album_enrichments", {})
    else:
        # Build baseline statistics directly from database
        db_concerts = Concert.objects.filter(user=target_user).select_related('venue').prefetch_related('artists__artist', 'artists__songs')
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

        # Check if user has attended setlists in disk cache to auto-reconcile without network calls
        setlist_username = (profile.setlistfm_username or "").strip()
        user_attended = []
        if setlist_username:
            user_cache_file = USER_CACHE_DIR / f"{setlist_username}_attended.json"
            if user_cache_file.exists():
                try:
                    with open(user_cache_file, "r", encoding="utf-8") as f:
                        user_attended = json.load(f)
                except Exception as e:
                    print(f"Error loading user attended cache: {e}")

        if user_attended:
            gap_results = reconcile_history(csv_records, user_attended, client=None, ignored_artists=profile.ignored_artists)
            matched = gap_results["matched"]
        else:
            matched = []
            gap_results = {
                "matched": [],
                "csv_status": {},
                "csv_only": [r for r in csv_records if not r.get("is_custom_offline")],
                "csv_missing_or_partial": [],
                "offline_shows": [
                    {
                        "record": r,
                        "matched_bands": [],
                        "missing_bands_info": [{"artist": r.get("primary_artist", ""), "status": "custom_offline", "status_label": "Offline / Custom Show (Unlisted)"}],
                        "missing_bands": [r.get("primary_artist", "")],
                        "has_exists": False,
                        "has_missing": False,
                        "is_custom_offline": True,
                        "setlists": [],
                        "is_fully_matched": False,
                        "is_partially_matched": False,
                        "is_unmatched": True
                    } for r in csv_records if r.get("is_custom_offline")
                ],
                "setlist_only": [],
                "total_csv": len(csv_records),
                "total_setlist_user": 0,
                "matched_count": len([r for r in csv_records if r.get("is_custom_offline")]),
                "coverage_percentage": round(len([r for r in csv_records if r.get("is_custom_offline")]) / len(csv_records) * 100, 1) if csv_records else 0.0
            }

        # Merge manual matched pairs without duplicating already-matched pairs
        if manual_matched_pairs:
            existing_keys = {(p["csv"]["id"], p["artist"].lower().strip()) for p in matched}
            existing_sl_ids = {p["setlist"].get("id") for p in matched if isinstance(p.get("setlist"), dict) and p["setlist"].get("id")}
            for mp in manual_matched_pairs:
                mp_key = (mp["csv"]["id"], mp["artist"].lower().strip())
                mp_sl_id = mp["setlist"].get("id") if isinstance(mp.get("setlist"), dict) else None
                if mp_key in existing_keys or (mp_sl_id and mp_sl_id in existing_sl_ids):
                    continue
                matched.append(mp)
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
            gap_results["matched"] = matched

        analytics = ConcertAnalytics(matched, csv_records, ignored_artists=profile.ignored_artists)
        stats = analytics.compute_all_metrics()

        enricher = AlbumEnricher()
        album_enrichments, _, _ = enricher.load_cached_catalog(stats["all_songs_list"])

        stats["concerts_drilldown"] = analytics.compute_concert_drilldown(album_enrichments)
        stats["venue_map"] = generate_venue_map_data(csv_records, matched)
        stats["musicians"] = analyze_musicians_live(csv_records)

        # Cache this bundle so subsequent loads are instant
        if matched or csv_records:
            ApiCache.objects.update_or_create(
                cache_key=f"user_dashboard_bundle_{target_user.id}",
                defaults={
                    'endpoint': 'dashboard_bundle',
                    'payload': {
                        'gap_results': gap_results,
                        'stats': stats,
                        'album_enrichments': album_enrichments
                    }
                }
            )

    if "top_year" not in stats and "yearly_concerts" in stats and stats["yearly_concerts"]:
        best_yr, best_cnt = max(stats["yearly_concerts"].items(), key=lambda x: (x[1], x[0]))
        stats["top_year"] = {"year": best_yr, "count": best_cnt}

    charts = generate_plotly_charts(stats, album_enrichments)

    # Attach album information to each song in artist_drilldown
    drilldown = stats.get("artist_drilldown", {})
    for artist_name, art_data in drilldown.items():
        albums_set = set()
        for s in art_data.get("songs", []):
            song_name = s.get("song")
            key = f"{artist_name}_{song_name}".lower()
            info = album_enrichments.get(key, {})
            album_title = info.get("album", "Non-Album / Singles")
            is_cov = any(o.get("is_cover") for o in s.get("occurrences", [])) or info.get("is_cover")
            if is_cov and album_title == "Non-Album / Singles":
                album_title = "Covers"
            rel_year = info.get("release_year") if album_title != "Covers" else None
            s["album"] = album_title
            s["release_year"] = rel_year
            s["album_display"] = f"{album_title} ({rel_year})" if rel_year else album_title
            albums_set.add(album_title)
        art_data["albums_list"] = sorted(list(albums_set))

    # Build comprehensive albums gallery dataset for Vinyl Album Wall
    tracklist_dir = MB_CACHE_DIR / "tracklists"
    albums_dict = {}
    for artist_name, art_data in drilldown.items():
        for s in art_data.get("songs", []):
            album_title = s.get("album", "Non-Album / Singles")
            if not album_title or album_title == "Non-Album / Singles" or album_title.lower() == "non-album / singles":
                continue
            rel_year = s.get("release_year")
            song_name = s.get("song")
            song_count = s.get("count", len(s.get("occurrences", [])))
            occurrences = s.get("occurrences", [])

            album_key = f"{artist_name}:::{album_title}"
            if album_key not in albums_dict:
                decade = f"{(rel_year // 10) * 10}s" if rel_year else ("Covers" if album_title == "Covers" else "Other")
                
                # Check for cached album tracklist
                cached_tracklist = []
                cache_key = "".join(c if c.isalnum() else "_" for c in f"{artist_name}_{album_title}".lower())
                cache_file = tracklist_dir / f"{cache_key}.json"
                if cache_file.exists():
                    try:
                        with open(cache_file, "r", encoding="utf-8") as f:
                            t_data = json.load(f)
                            if t_data and isinstance(t_data.get("tracks"), list):
                                cached_tracklist = t_data["tracks"]
                    except Exception:
                        pass

                albums_dict[album_key] = {
                    "id": f"album_{len(albums_dict) + 1}",
                    "artist": artist_name,
                    "album": album_title,
                    "release_year": rel_year,
                    "decade": decade,
                    "album_display": s.get("album_display", album_title),
                    "plays_heard": 0,
                    "unique_songs": 0,
                    "songs": [],
                    "tracklist": cached_tracklist,
                    "first_seen_date": None,
                    "first_seen_venue": None,
                    "last_seen_date": None,
                    "last_seen_venue": None,
                }
            alb_entry = albums_dict[album_key]
            alb_entry["plays_heard"] += song_count
            alb_entry["unique_songs"] += 1

            # Match track number from tracklist if available
            track_num = None
            if alb_entry.get("tracklist"):
                s_norm = normalize_track_title(song_name)
                for t in alb_entry["tracklist"]:
                    t_norm = normalize_track_title(t.get("title") or "")
                    if s_norm == t_norm or (len(s_norm) >= 4 and (t_norm.endswith(s_norm) or s_norm.endswith(t_norm) or s_norm in t_norm or t_norm in s_norm)):
                        track_num = t.get("track_number")
                        break

            alb_entry["songs"].append({
                "song": song_name,
                "count": song_count,
                "track_number": track_num,
                "occurrences": occurrences
            })
            for occ in occurrences:
                dt = occ.get("date")
                vn = occ.get("venue")
                if dt:
                    if not alb_entry["first_seen_date"] or dt < alb_entry["first_seen_date"]:
                        alb_entry["first_seen_date"] = dt
                        alb_entry["first_seen_venue"] = vn
                    if not alb_entry["last_seen_date"] or dt > alb_entry["last_seen_date"]:
                        alb_entry["last_seen_date"] = dt
                        alb_entry["last_seen_venue"] = vn

    albums_gallery = list(albums_dict.values())
    for alb in albums_gallery:
        # Sort songs by track number if present, else by count
        alb["songs"].sort(key=lambda x: (0 if x.get("track_number") is not None else 1, x.get("track_number") or 0, -x["count"]))
    albums_gallery.sort(key=lambda x: x["plays_heard"], reverse=True)

    for idx, alb in enumerate(albums_gallery):
        alb["rank"] = idx + 1

    stats["albums_gallery"] = albums_gallery

    artist_drilldown_json = json.dumps(drilldown)
    venue_map_json = json.dumps(stats.get("venue_map", {}))
    musicians_json = json.dumps(stats.get("musicians", {}).get("top_musicians", []))

    is_owner = request.user.is_authenticated and (request.user.id == target_user.id)
    tab_url_base = f"/u/{target_user.username}" if is_public_view else ""
    share_url = request.build_absolute_uri(f"/u/{target_user.username}/")

    drilldown_list = format_concert_drilldown_dates(stats.get("concerts_drilldown", []))
    if request.user.is_authenticated and is_public_view:
        stats_copy = dict(stats)
        stats_copy["concerts_drilldown"] = check_viewer_attendance_for_drilldown(request.user, drilldown_list, target_user)
        stats = stats_copy
    elif request.user.is_authenticated and is_owner:
        stats_copy = dict(stats)
        stats_copy["concerts_drilldown"] = [dict(c, is_attended_by_viewer=True) for c in drilldown_list]
        stats = stats_copy
    else:
        stats_copy = dict(stats)
        stats_copy["concerts_drilldown"] = drilldown_list
        stats = stats_copy

    is_friend = False
    is_mutual_friend = False
    has_friended_you = False
    friends_list = []
    pending_sent_list = []
    friended_by_list = []
    friend_suggestions = []

    if request.user.is_authenticated:
        if is_public_view and not is_owner:
            is_friend = Friendship.objects.filter(user=request.user, friend=target_user).exists()
            has_friended_you = Friendship.objects.filter(user=target_user, friend=request.user).exists()
            is_mutual_friend = is_friend and has_friended_you
        elif not is_public_view:
            viewer_concerts = list(
                Concert.objects.filter(user=request.user)
                .select_related('venue')
                .prefetch_related('artists__artist')
            )
            a_date_artist_map = {}
            a_sl_ids = {}

            for ac in viewer_concerts:
                d_keys = set()
                if ac.date:
                    d_keys.add(ac.date.strftime("%Y-%m-%d"))
                    d_keys.add(ac.date.strftime("%m-%d-%Y"))
                    d_keys.add(ac.date.strftime("%m/%d/%Y"))
                    d_keys.add(ac.date.strftime("%d-%m-%Y"))
                if ac.raw_date:
                    raw_s = ac.raw_date.strip()
                    d_keys.add(raw_s)
                    d_keys.add(raw_s.replace('-', '/'))
                    d_keys.add(raw_s.replace('/', '-'))

                a_art_names = {normalize_artist_name(ca.artist.name) or ca.artist.name.lower().strip() for ca in ac.artists.all()}
                if ac.primary_artist:
                    a_art_names.add(normalize_artist_name(ac.primary_artist) or ac.primary_artist.lower().strip())

                for d_k in d_keys:
                    for a_name in a_art_names:
                        if a_name:
                            a_date_artist_map[(d_k, a_name)] = ac

                for ca in ac.artists.all():
                    if ca.setlistfm_id:
                        a_sl_ids[ca.setlistfm_id] = ac

            def find_co_attended(target_u):
                friend_concerts = list(
                    Concert.objects.filter(user=target_u)
                    .select_related('venue')
                    .prefetch_related('artists__artist')
                    .order_by('-date', '-year', '-id')
                )
                co_list = []
                seen_ac_ids = set()

                for fc in friend_concerts:
                    f_d_keys = set()
                    if fc.date:
                        f_d_keys.add(fc.date.strftime("%Y-%m-%d"))
                        f_d_keys.add(fc.date.strftime("%m-%d-%Y"))
                        f_d_keys.add(fc.date.strftime("%m/%d/%Y"))
                        f_d_keys.add(fc.date.strftime("%d-%m-%Y"))
                    if fc.raw_date:
                        raw_s = fc.raw_date.strip()
                        f_d_keys.add(raw_s)
                        f_d_keys.add(raw_s.replace('-', '/'))
                        f_d_keys.add(raw_s.replace('/', '-'))

                    f_art_names = {normalize_artist_name(ca.artist.name) or ca.artist.name.lower().strip() for ca in fc.artists.all()}
                    if fc.primary_artist:
                        f_art_names.add(normalize_artist_name(fc.primary_artist) or fc.primary_artist.lower().strip())

                    matched_ac = None
                    for ca in fc.artists.all():
                        if ca.setlistfm_id and ca.setlistfm_id in a_sl_ids:
                            matched_ac = a_sl_ids[ca.setlistfm_id]
                            break

                    if not matched_ac:
                        for d_k in f_d_keys:
                            for a_name in f_art_names:
                                if (d_k, a_name) in a_date_artist_map:
                                    matched_ac = a_date_artist_map[(d_k, a_name)]
                                    break
                            if matched_ac:
                                break

                    if matched_ac and matched_ac.id not in seen_ac_ids:
                        seen_ac_ids.add(matched_ac.id)
                        venue_name = fc.venue.name if fc.venue else (matched_ac.venue.name if matched_ac.venue else '')
                        venue_city = fc.venue.city if fc.venue and fc.venue.city else (matched_ac.venue.city if matched_ac.venue and matched_ac.venue.city else '')
                        display_date = fc.raw_date or (fc.date.strftime('%m/%d/%Y') if fc.date else '')
                        primary_art = fc.primary_artist or (matched_ac.primary_artist if matched_ac else '')
                        co_list.append({
                            'date': display_date,
                            'artist': primary_art,
                            'venue': venue_name,
                            'city': venue_city,
                        })
                return co_list

            sent_ids = set(Friendship.objects.filter(user=request.user).values_list('friend_id', flat=True))
            incoming_ids = set(Friendship.objects.filter(friend=request.user).values_list('user_id', flat=True))

            mutual_ids = sent_ids & incoming_ids
            pending_sent_ids = sent_ids - incoming_ids
            friended_by_ids = incoming_ids - sent_ids

            # Mutual Friends
            friends_qs = User.objects.filter(id__in=mutual_ids).select_related('profile').order_by('username')
            for f in friends_qs:
                c_count = Concert.objects.filter(user=f).count()
                top_art = Concert.objects.filter(user=f).values('primary_artist').annotate(shows=Count('id')).order_by('-shows').first()
                co_shows = find_co_attended(f)
                friends_list.append({
                    'id': f.id,
                    'username': f.username,
                    'is_public': f.profile.is_public,
                    'setlistfm_username': f.profile.setlistfm_username,
                    'concert_count': c_count,
                    'top_artist': top_art['primary_artist'] if top_art else None,
                    'top_artist_shows': top_art['shows'] if top_art else 0,
                    'is_friend': True,
                    'is_mutual': True,
                    'co_attended_count': len(co_shows),
                    'co_attended_concerts': co_shows,
                })

            # Pending Requests Sent (User friended them, but not yet mutually confirmed)
            pending_sent_qs = User.objects.filter(id__in=pending_sent_ids).select_related('profile').order_by('username')
            for ps in pending_sent_qs:
                c_count = Concert.objects.filter(user=ps).count()
                top_art = Concert.objects.filter(user=ps).values('primary_artist').annotate(shows=Count('id')).order_by('-shows').first()
                co_shows = find_co_attended(ps) if ps.profile.is_public else []
                pending_sent_list.append({
                    'id': ps.id,
                    'username': ps.username,
                    'is_public': ps.profile.is_public,
                    'setlistfm_username': ps.profile.setlistfm_username,
                    'concert_count': c_count,
                    'top_artist': top_art['primary_artist'] if top_art else None,
                    'top_artist_shows': top_art['shows'] if top_art else 0,
                    'is_friend': True,
                    'is_mutual': False,
                    'is_pending_sent': True,
                    'co_attended_count': len(co_shows),
                    'co_attended_concerts': co_shows,
                })

            # People who friended request.user but request.user hasn't friended back yet
            friended_by_qs = User.objects.filter(id__in=friended_by_ids).select_related('profile').order_by('username')
            for fb in friended_by_qs:
                c_count = Concert.objects.filter(user=fb).count()
                top_art = Concert.objects.filter(user=fb).values('primary_artist').annotate(shows=Count('id')).order_by('-shows').first()
                co_shows = find_co_attended(fb) if fb.profile.is_public else []
                friended_by_list.append({
                    'id': fb.id,
                    'username': fb.username,
                    'is_public': fb.profile.is_public,
                    'setlistfm_username': fb.profile.setlistfm_username,
                    'concert_count': c_count,
                    'top_artist': top_art['primary_artist'] if top_art else None,
                    'top_artist_shows': top_art['shows'] if top_art else 0,
                    'is_friend': False,
                    'is_mutual': False,
                    'has_friended_you': True,
                    'co_attended_count': len(co_shows),
                    'co_attended_concerts': co_shows,
                })

            excluded_suggestion_ids = sent_ids | incoming_ids
            sugg_qs = User.objects.exclude(id=request.user.id).exclude(id__in=excluded_suggestion_ids).select_related('profile').order_by('username')[:30]
            for s in sugg_qs:
                c_count = Concert.objects.filter(user=s).count()
                top_art = Concert.objects.filter(user=s).values('primary_artist').annotate(shows=Count('id')).order_by('-shows').first()
                co_shows = find_co_attended(s) if s.profile.is_public else []
                friend_suggestions.append({
                    'id': s.id,
                    'username': s.username,
                    'is_public': s.profile.is_public,
                    'setlistfm_username': s.profile.setlistfm_username,
                    'concert_count': c_count,
                    'top_artist': top_art['primary_artist'] if top_art else None,
                    'top_artist_shows': top_art['shows'] if top_art else 0,
                    'is_friend': False,
                    'has_friended_you': False,
                    'co_attended_count': len(co_shows),
                    'co_attended_concerts': co_shows,
                })

    # Fetch upcoming shows based on artists seen before
    upcoming_data = get_upcoming_shows_for_user(target_user)
    upcoming_shows = upcoming_data.get('upcoming_shows', [])
    upcoming_seen_artists = upcoming_data.get('seen_artists', [])
    upcoming_hidden_count = upcoming_data.get('hidden_artists_count', 0)
    upcoming_total_artists = upcoming_data.get('total_artists', 0)
    upcoming_has_shows = upcoming_data.get('has_shows', False)

    return {
        'initial_tab': initial_tab,
        'profile_user': target_user,
        'username': profile.setlistfm_username or target_user.username,
        'profile': profile,
        'gap': gap_results,
        'stats': stats,
        'artist_drilldown_json': artist_drilldown_json,
        'albums_gallery_json': json.dumps(stats.get('albums_gallery', [])),
        'venue_map_json': venue_map_json,
        'musicians_json': musicians_json,
        'upcoming_shows': upcoming_shows,
        'upcoming_shows_json': json.dumps(upcoming_shows),
        'upcoming_seen_artists': upcoming_seen_artists,
        'upcoming_seen_artists_json': json.dumps(upcoming_seen_artists),
        'upcoming_hidden_count': upcoming_hidden_count,
        'upcoming_total_artists': upcoming_total_artists,
        'upcoming_has_shows': upcoming_has_shows,
        'upcoming_location': profile.upcoming_location,
        'upcoming_radius_miles': profile.upcoming_radius_miles if (profile.upcoming_radius_miles and profile.upcoming_radius_miles > 0) else 100,
        'all_venues': list(Venue.objects.order_by('name').values_list('name', flat=True).distinct()),
        'all_artists': list(Artist.objects.order_by('name').values_list('name', flat=True).distinct()),
        'is_public_view': is_public_view,
        'is_owner': is_owner,
        'is_friend': is_friend,
        'is_mutual_friend': is_mutual_friend,
        'has_friended_you': has_friended_you,
        'friends_list': friends_list,
        'pending_sent_list': pending_sent_list,
        'friended_by_list': friended_by_list,
        'friend_suggestions': friend_suggestions,
        'is_profile_private': not profile.is_public,
        'share_url': share_url,
        'tab_url_base': tab_url_base,
        'carto_api_key': CARTO_API_KEY,
        **charts
    }

@login_required
def dashboard_view(request, tab_name='overview'):
    context = get_dashboard_context(request, request.user, tab_name=tab_name, is_public_view=False)
    return render(request, 'dashboard.html', context)

def public_profile_view(request, username, tab_name='overview'):
    clean_username = username.lstrip('@').strip()
    target_user = User.objects.filter(username__iexact=clean_username).first()
    if not target_user:
        return render(request, 'public_profile_message.html', {
            'title': 'User Not Found',
            'message_type': 'not_found',
            'requested_username': clean_username,
        }, status=404)

    is_owner = request.user.is_authenticated and (request.user.id == target_user.id)
    is_staff = request.user.is_authenticated and request.user.is_staff
    is_mutual_friend = False
    if request.user.is_authenticated:
        has_friended = Friendship.objects.filter(user=request.user, friend=target_user).exists()
        is_friended_by = Friendship.objects.filter(user=target_user, friend=request.user).exists()
        is_mutual_friend = has_friended and is_friended_by

    target_profile = getattr(target_user, 'profile', None)
    if not target_profile:
        from apps.core.models import UserProfile
        target_profile, _ = UserProfile.objects.get_or_create(user=target_user)

    if not target_profile.is_public and not is_owner and not is_staff and not is_mutual_friend:
        return render(request, 'public_profile_message.html', {
            'title': 'Private Profile',
            'message_type': 'private',
            'target_user': target_user,
        }, status=403)

    if tab_name == 'friends':
        tab_name = 'overview'

    context = get_dashboard_context(request, target_user, tab_name=tab_name, is_public_view=True)
    return render(request, 'dashboard.html', context)

@login_required
@require_POST
def trigger_sync(request):
    sync_worker.enqueue_sync(request.user.id)
    return JsonResponse({
        "status": "queued",
        "message": "Setlist.fm and MusicBrainz sync queued in background."
    })

@login_required
def sync_status(request):
    profile = request.user.profile
    return JsonResponse({
        "status": profile.sync_status,
        "progress": profile.sync_progress,
        "last_synced_at": profile.last_synced_at.strftime("%Y-%m-%d %H:%M:%S") if profile.last_synced_at else None
    })

@login_required
@require_POST
def cancel_sync(request):
    sync_worker.cancel_sync(request.user.id)
    return JsonResponse({
        "status": "success",
        "message": "Sync cancelled successfully."
    })

@login_required
@require_POST
def upload_csv(request):
    if 'csv_file' not in request.FILES:
        return HttpResponseBadRequest("Missing csv_file in request")

    uploaded = request.FILES['csv_file']
    try:
        content = uploaded.read().decode('utf-8-sig')
        reader = csv.reader(io.StringIO(content))
        csv_records = parse_csv_rows(reader, ignored_list=request.user.profile.ignored_artists)

        if not csv_records:
            return JsonResponse({"error": "No valid concert rows found in CSV"}, status=400)

        all_songs_to_enrich = []

        with transaction.atomic():
            Concert.objects.filter(user=request.user, source='csv').delete()
            for rec in csv_records:
                dt = rec.get("date_obj")
                venue_str = rec.get("venue", "").strip()

                venue_obj = None
                if venue_str:
                    venue_obj = Venue.objects.filter(name__iexact=venue_str.lower()).first()
                    if not venue_obj:
                        venue_obj, _ = Venue.objects.get_or_create(
                            name=venue_str,
                            defaults={'city': '', 'state': '', 'country': 'United States', 'geocode_source': 'unresolved'}
                        )

                concert = Concert.objects.create(
                    user=request.user,
                    date=dt.date() if dt else None,
                    raw_date=rec.get("raw_date", ""),
                    year=rec.get("year"),
                    venue=venue_obj,
                    raw_venue=venue_str,
                    primary_artist=rec.get("primary_artist", ""),
                    raw_artists=rec.get("raw_artists", ""),
                    seen_before="",
                    notes=rec.get("notes", ""),
                    source='csv',
                    is_custom_offline=False
                )

                ca_map = {}
                for idx, art_name in enumerate(rec.get("artists", [])):
                    can_art = normalize_artist_name(art_name) or art_name
                    art_obj, _ = get_or_create_artist(can_art)
                    ca = ConcertArtist.objects.create(
                        concert=concert,
                        artist=art_obj,
                        billing_order=idx,
                        has_setlist=False
                    )
                    ca_map[can_art.lower()] = (ca, art_obj)

                # Parse and save setlist if present in the CSV row
                raw_setlist = rec.get("setlist", "").strip()
                if raw_setlist and ca_map:
                    artist_segments = {}
                    if ' | ' in raw_setlist:
                        parts = raw_setlist.split(' | ')
                        for p in parts:
                            p_clean = p.strip()
                            matched_art = None
                            for art_low in ca_map.keys():
                                if p_clean.lower().startswith(art_low + ':') or p_clean.lower().startswith(art_low + ' :'):
                                    matched_art = art_low
                                    content_str = p_clean[len(art_low):].lstrip(': ').strip()
                                    artist_segments[matched_art] = content_str
                                    break
                            if not matched_art and parts:
                                first_art_low = list(ca_map.keys())[0]
                                if first_art_low not in artist_segments:
                                    artist_segments[first_art_low] = p_clean
                    else:
                        matched_art = None
                        for art_low in ca_map.keys():
                            if raw_setlist.lower().startswith(art_low + ':') or raw_setlist.lower().startswith(art_low + ' :'):
                                matched_art = art_low
                                content_str = raw_setlist[len(art_low):].lstrip(': ').strip()
                                artist_segments[matched_art] = content_str
                                break
                        if not matched_art:
                            first_art_low = list(ca_map.keys())[0]
                            artist_segments[first_art_low] = raw_setlist

                    for art_low, sl_text in artist_segments.items():
                        if art_low in ca_map and sl_text:
                            ca, art_obj = ca_map[art_low]
                            parsed_tracks = parse_setlist_text(sl_text)
                            if parsed_tracks:
                                enriched = save_setlist_for_concert_artist(ca, art_obj, parsed_tracks)
                                all_songs_to_enrich.extend(enriched)

        # Clear any stale dashboard bundle for this user
        ApiCache.objects.filter(cache_key=f"user_dashboard_bundle_{request.user.id}").delete()

        if all_songs_to_enrich:
            try:
                enricher = AlbumEnricher()
                enricher.load_cached_catalog([{"artist": a, "song": s} for a, s in all_songs_to_enrich])
            except Exception:
                pass

        # Trigger background sync automatically
        sync_worker.enqueue_sync(request.user.id)

        return JsonResponse({
            "status": "success",
            "imported_count": len(csv_records),
            "message": f"Imported {len(csv_records)} concerts! Background sync has started."
        })
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)

@login_required
@require_POST
def add_concert(request):
    try:
        try:
            data = json.loads(request.body.decode('utf-8'))
        except Exception:
            data = request.POST

        date_str = str(data.get('date', '')).strip()
        primary_artist_raw = str(data.get('primary_artist', '')).strip()
        supporting_artists_raw = str(data.get('supporting_artists', '')).strip()
        venue_name_raw = str(data.get('venue_name', '')).strip()
        city = str(data.get('city', '')).strip()
        state = str(data.get('state', '')).strip()
        country = str(data.get('country', '')).strip() or 'United States'
        notes = str(data.get('notes', '')).strip()
        setlist_text = str(data.get('setlist_text', '')).strip()
        is_custom_offline = data.get('is_custom_offline', True)
        if isinstance(is_custom_offline, str):
            is_custom_offline = is_custom_offline.lower() in ['true', '1', 'on', 'yes']

        if not date_str:
            return JsonResponse({"error": "Date is required."}, status=400)
        if not primary_artist_raw:
            return JsonResponse({"error": "Artist/Headliner is required."}, status=400)
        if not venue_name_raw:
            return JsonResponse({"error": "Venue name is required."}, status=400)

        # Parse date
        dt = None
        for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m-%d-%Y", "%d-%m-%Y"):
            try:
                dt = datetime.strptime(date_str, fmt)
                break
            except ValueError:
                pass

        if not dt:
            return JsonResponse({"error": "Invalid date format. Please use YYYY-MM-DD or MM/DD/YYYY."}, status=400)

        year = dt.year
        raw_date = dt.strftime("%m/%d/%Y")

        with transaction.atomic():
            # Venue resolution / creation
            venue_obj = Venue.objects.filter(name__iexact=venue_name_raw.lower()).first()
            if not venue_obj:
                lat, lng, source_type = resolve_venue_coordinates(venue_name_raw, city, state, country)
                venue_obj = Venue.objects.create(
                    name=venue_name_raw,
                    city=city,
                    state=state,
                    country=country,
                    latitude=lat,
                    longitude=lng,
                    geocode_source=source_type
                )
            elif (city or state) and not venue_obj.city:
                venue_obj.city = city or venue_obj.city
                venue_obj.state = state or venue_obj.state
                if venue_obj.latitude is None:
                    lat, lng, source_type = resolve_venue_coordinates(venue_name_raw, venue_obj.city, venue_obj.state, country)
                    if lat is not None:
                        venue_obj.latitude = lat
                        venue_obj.longitude = lng
                        venue_obj.geocode_source = source_type
                venue_obj.save()

            # Artist resolution
            can_primary = normalize_artist_name(primary_artist_raw) or primary_artist_raw
            primary_art_obj, _ = get_or_create_artist(can_primary)

            # Supporting artists
            artists_list = [can_primary]
            supporting_objs = []
            if supporting_artists_raw:
                parts = re.split(r'[,;/]+', supporting_artists_raw)
                for p in parts:
                    p_clean = p.strip()
                    if p_clean:
                        can_supp = normalize_artist_name(p_clean) or p_clean
                        supp_obj, _ = get_or_create_artist(can_supp)
                        artists_list.append(can_supp)
                        supporting_objs.append(supp_obj)

            raw_artists = ", ".join(artists_list)

            # Create Concert
            concert = Concert.objects.create(
                user=request.user,
                date=dt.date(),
                raw_date=raw_date,
                year=year,
                venue=venue_obj,
                raw_venue=venue_obj.name,
                primary_artist=can_primary,
                raw_artists=raw_artists,
                seen_before="",
                notes=notes,
                source='manual',
                is_custom_offline=bool(is_custom_offline)
            )

            # Create primary ConcertArtist
            ca_primary = ConcertArtist.objects.create(
                concert=concert,
                artist=primary_art_obj,
                billing_order=0,
                has_setlist=bool(setlist_text)
            )

            # Create supporting ConcertArtists
            for idx, s_obj in enumerate(supporting_objs, start=1):
                ConcertArtist.objects.create(
                    concert=concert,
                    artist=s_obj,
                    billing_order=idx,
                    has_setlist=False
                )

            # Parse and save setlist if provided
            new_songs_to_enrich = []
            if setlist_text:
                parsed_tracks = parse_setlist_text(setlist_text)
                if parsed_tracks:
                    new_songs_to_enrich = save_setlist_for_concert_artist(ca_primary, primary_art_obj, parsed_tracks)

        # Optional quick album and musician enrichment for new artists/songs
        if new_songs_to_enrich:
            try:
                enricher = AlbumEnricher()
                enricher.load_cached_catalog([{"artist": a, "song": s} for a, s in new_songs_to_enrich])
            except Exception:
                pass

        try:
            m_enricher = MusicianEnricher()
            is_upcoming_or_current = bool(concert.date and concert.date.year >= datetime.now().year)
            if not primary_art_obj.members.exists():
                m_enricher.enrich_artist(primary_art_obj.name, artist_obj=primary_art_obj)
            elif is_upcoming_or_current:
                m_enricher.enrich_artist(primary_art_obj.name, artist_obj=primary_art_obj, refresh=True)
        except Exception:
            pass

        # If user has a Setlist.fm username configured, always save and sync this concert
        sync_info = None
        if hasattr(request.user, 'profile') and (request.user.profile.setlistfm_username or "").strip():
            try:
                from apps.concerts.utils import sync_single_concert
                sync_info = sync_single_concert(concert, user=request.user)
            except Exception as se:
                logger.warning("Single concert sync error on add_concert: %s", se)

        # Clear dashboard bundle cache for this user so changes reflect immediately
        ApiCache.objects.filter(cache_key=f"user_dashboard_bundle_{request.user.id}").delete()

        synced_msg = ""
        if sync_info and sync_info.get("status") == "success" and sync_info.get("matched", 0) > 0:
            synced_msg = f" (synced with Setlist.fm: {sync_info.get('songs_added', 0)} songs linked)"
        elif new_songs_to_enrich:
            synced_msg = f" with {len(new_songs_to_enrich)} songs"

        return JsonResponse({
            "status": "success",
            "message": f"Concert for '{can_primary}' on {raw_date} saved and logged successfully{synced_msg}!",
            "concert_id": concert.id,
            "synced": bool(sync_info and sync_info.get("status") == "success")
        })
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)

@login_required
@require_POST
def sync_single_concert_view(request):
    try:
        try:
            data = json.loads(request.body.decode('utf-8'))
        except Exception:
            data = request.POST
        concert_id = data.get('concert_id')
        if not concert_id:
            return JsonResponse({"error": "Missing concert_id parameter."}, status=400)
        concert = Concert.objects.filter(id=concert_id, user=request.user).first()
        if not concert:
            return JsonResponse({"error": "Concert not found."}, status=404)
        from apps.concerts.utils import sync_single_concert
        res = sync_single_concert(concert, user=request.user)
        return JsonResponse(res)
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)

@login_required
@require_POST
def parse_ticketmaster_preview(request):
    try:
        data = json.loads(request.body.decode('utf-8'))
        raw_text = data.get('raw_text', '')
        if not raw_text or not raw_text.strip():
            return JsonResponse({"error": "No Ticketmaster text provided. Please paste your past events list."}, status=400)

        from src.ticketmaster_parser import parse_ticketmaster_text
        from src.gap_analysis import match_score, normalize_name
        from rapidfuzz import fuzz

        events = parse_ticketmaster_text(raw_text)
        if not events:
            return JsonResponse({"error": "Could not detect any events in the pasted text. Please verify the format."}, status=400)

        user = request.user
        profile = user.profile
        existing_concerts = list(Concert.objects.filter(user=user).select_related('venue').prefetch_related('artists__artist'))

        # Prepare client if API key is present
        api_key = profile.setlistfm_api_key or SETLISTFM_API_KEY
        client = None
        if api_key:
            try:
                client = SetlistFMClient(api_key=api_key)
            except Exception:
                client = None

        # Load attended setlists cache if available
        setlist_username = profile.setlistfm_username or user.username
        user_cache_file = USER_CACHE_DIR / f"{setlist_username}_attended.json"
        user_attended = []
        if user_cache_file.exists():
            try:
                with open(user_cache_file, "r", encoding="utf-8") as f:
                    user_attended = json.load(f)
            except Exception:
                pass

        processed_events = []
        duplicates_count = 0

        for idx, ev in enumerate(events):
            ev_date_str = ev.get("date")  # YYYY-MM-DD
            ev_artist = ev.get("artist", "")
            ev_venue = ev.get("venue", "")
            ev_dt = datetime.strptime(ev_date_str, "%Y-%m-%d") if ev_date_str else None

            # 1. Deduplication check against user's existing concert records
            is_dup = False
            dup_reason = ""
            existing_match_id = None
            for ex in existing_concerts:
                if ex.date and ev_dt and ex.date == ev_dt.date():
                    norm_ex_art = normalize_name(ex.primary_artist)
                    norm_ev_art = normalize_name(ev_artist)
                    ratio = fuzz.ratio(norm_ex_art, norm_ev_art)
                    if ratio >= 65 or norm_ev_art in normalize_name(ex.raw_artists) or norm_ex_art in norm_ev_art:
                        is_dup = True
                        dup_reason = f"Matches existing concert '{ex.primary_artist}' on {ex.date.strftime('%m-%d-%Y')}"
                        existing_match_id = ex.id
                        break

            if is_dup:
                duplicates_count += 1

            # 2. Setlist.fm matching
            sl_date_str = ev_dt.strftime("%d-%m-%Y") if ev_dt else ""
            candidate_setlists = []

            # Check user attended setlists first
            if user_attended and sl_date_str:
                for sl in user_attended:
                    if sl.get("eventDate") == sl_date_str:
                        sc = match_score(ev_artist, ev_venue, sl, require_venue=False)
                        if sc >= 50:
                            candidate_setlists.append((sc, sl))

            # If no matches in attended list and client available, search setlist.fm API
            if not candidate_setlists and client and sl_date_str:
                try:
                    search_res = client.search_setlists(artist_name=ev_artist, date_str=sl_date_str)
                    for sl in search_res:
                        if sl.get("eventDate") == sl_date_str:
                            sc = match_score(ev_artist, ev_venue, sl, require_venue=False)
                            if sc >= 50:
                                candidate_setlists.append((sc, sl))
                except Exception:
                    pass

            # Sort candidate setlists by match score descending
            candidate_setlists.sort(key=lambda x: x[0], reverse=True)

            best_match_obj = None
            available_setlists_info = []

            for sc, sl in candidate_setlists[:5]:
                sets = sl.get("sets", {}).get("set", [])
                song_count = sum(len([s for s in st.get("song", []) if not s.get("tape")]) for st in sets)
                v_obj = sl.get("venue", {})
                v_name = v_obj.get("name", "")
                c_name = v_obj.get("city", {}).get("name", "")
                s_name = v_obj.get("city", {}).get("stateCode") or v_obj.get("city", {}).get("state", "")
                loc_disp = f"{v_name} ({c_name}, {s_name})" if c_name else v_name

                info = {
                    "id": sl.get("id"),
                    "score": round(sc, 1),
                    "artist": sl.get("artist", {}).get("name", ev_artist),
                    "venue": v_name,
                    "location": loc_disp,
                    "song_count": song_count,
                    "url": sl.get("url", ""),
                    "event_date": sl.get("eventDate", "")
                }
                available_setlists_info.append(info)
                if best_match_obj is None:
                    best_match_obj = info

            processed_events.append({
                "temp_id": f"tm_evt_{idx}",
                "date": ev_date_str,
                "display_date": ev.get("display_date"),
                "raw_date": ev.get("raw_date"),
                "artist": ev_artist,
                "venue": ev_venue,
                "city": ev.get("city", ""),
                "state": ev.get("state", ""),
                "country": ev.get("country", "United States"),
                "tour_notes": ev.get("tour_notes", ""),
                "order_number": ev.get("order_number", ""),
                "is_duplicate": is_dup,
                "duplicate_reason": dup_reason,
                "existing_match_id": existing_match_id,
                "setlist_match": best_match_obj,
                "available_setlists": available_setlists_info,
                "selected": not is_dup
            })

        return JsonResponse({
            "status": "success",
            "events": processed_events,
            "total_parsed": len(processed_events),
            "duplicates_count": duplicates_count
        })
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)

@login_required
@require_POST
def confirm_ticketmaster_import(request):
    try:
        data = json.loads(request.body.decode('utf-8'))
        events_to_import = data.get('events', [])
        if not events_to_import:
            return JsonResponse({"error": "No events were selected for import."}, status=400)

        user = request.user
        profile = user.profile
        api_key = profile.setlistfm_api_key or SETLISTFM_API_KEY
        client = SetlistFMClient(api_key=api_key) if api_key else None

        created_concerts = []
        new_songs_to_enrich = []

        with transaction.atomic():
            for ev in events_to_import:
                date_str = str(ev.get('date', '')).strip()
                artist_raw = str(ev.get('artist', '')).strip()
                venue_raw = str(ev.get('venue', '')).strip()
                city = str(ev.get('city', '')).strip()
                state = str(ev.get('state', '')).strip()
                country = str(ev.get('country', '')).strip() or 'United States'
                tour_notes = str(ev.get('tour_notes', '')).strip()
                order_num = str(ev.get('order_number', '')).strip()
                setlist_id = str(ev.get('setlist_id', '')).strip()

                if not date_str or not artist_raw or not venue_raw:
                    continue

                dt = None
                for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m-%d-%Y", "%d-%m-%Y"):
                    try:
                        dt = datetime.strptime(date_str, fmt)
                        break
                    except ValueError:
                        pass

                if not dt:
                    continue

                notes_parts = []
                if tour_notes:
                    notes_parts.append(tour_notes)
                if order_num:
                    notes_parts.append(f"Ticketmaster Order: {order_num}")
                combined_notes = " | ".join(notes_parts)

                # Resolve Venue
                venue_obj = Venue.objects.filter(name__iexact=venue_raw.lower()).first()
                if not venue_obj:
                    lat, lng, source_type = resolve_venue_coordinates(venue_raw, city, state, country)
                    venue_obj = Venue.objects.create(
                        name=venue_raw,
                        city=city,
                        state=state,
                        country=country,
                        latitude=lat,
                        longitude=lng,
                        geocode_source=source_type
                    )
                elif (city or state) and not venue_obj.city:
                    venue_obj.city = city or venue_obj.city
                    venue_obj.state = state or venue_obj.state
                    venue_obj.save()

                # Resolve Artist
                can_primary = normalize_artist_name(artist_raw) or artist_raw
                primary_art_obj, _ = get_or_create_artist(can_primary)

                # Fetch setlist if setlist_id is provided
                sl_data = None
                if setlist_id and client:
                    try:
                        sl_data = client.get_setlist_by_id(setlist_id, use_cache=True)
                    except Exception:
                        pass

                # Check if setlist contains songs
                sets_list = sl_data.get("sets", {}).get("set", []) if sl_data else []
                has_songs = any(bool(s.get("song")) for s in sets_list)

                concert = Concert.objects.create(
                    user=user,
                    date=dt.date(),
                    raw_date=dt.strftime("%m/%d/%Y"),
                    year=dt.year,
                    venue=venue_obj,
                    raw_venue=venue_obj.name,
                    primary_artist=can_primary,
                    raw_artists=can_primary,
                    seen_before="",
                    notes=combined_notes,
                    source='ticketmaster',
                    is_custom_offline=not (bool(setlist_id) and has_songs),
                    is_fully_matched=bool(setlist_id) and has_songs
                )

                ca = ConcertArtist.objects.create(
                    concert=concert,
                    artist=primary_art_obj,
                    billing_order=0,
                    setlistfm_id=setlist_id if (setlist_id and sl_data) else "",
                    setlist_url=sl_data.get("url", "") if sl_data else "",
                    has_setlist=has_songs
                )

                if has_songs and sl_data:
                    flat_songs = []
                    for s_idx, s in enumerate(sets_list):
                        is_encore = bool(s.get("encore"))
                        encore_num = s.get("encore")
                        set_name = s.get("name", "")
                        song_list = s.get("song", [])
                        for s_order, song_obj in enumerate(song_list):
                            if song_obj.get("tape"):
                                continue
                            s_name = song_obj.get("name", "").strip()
                            if s_name:
                                flat_songs.append({
                                    "song_obj": song_obj,
                                    "name": s_name,
                                    "is_encore": is_encore,
                                    "encore_num": encore_num,
                                    "set_name": set_name or ("Encore" if is_encore else "Main Set"),
                                    "set_idx": s_idx,
                                    "is_last_in_set": (s_order == len(song_list) - 1)
                                })

                    total_tracks = len(flat_songs)
                    main_sets = [s for s in sets_list if not s.get("encore")]
                    for idx, item in enumerate(flat_songs):
                        track_num = idx + 1
                        pct = round((track_num / total_tracks) * 100) if total_tracks > 0 else 100
                        set_name = item["set_name"]
                        set_idx = item["set_idx"]
                        song_obj_data = item["song_obj"]
                        name = item["name"]

                        if track_num == 1:
                            slot = "Opener"
                            slot_category = "opener"
                        elif item["is_encore"]:
                            if track_num == total_tracks:
                                slot = "Show Closer"
                            else:
                                slot = f"Encore {item['encore_num']}" if item["encore_num"] else "Encore"
                            slot_category = "encore"
                        elif track_num == total_tracks:
                            slot = "Show Closer"
                            slot_category = "closer"
                        elif item["is_last_in_set"]:
                            if len(main_sets) > 1 and set_idx < len(main_sets) - 1:
                                slot = f"{set_name} Closer" if set_name else f"Set {set_idx + 1} Closer"
                                slot_category = "closer"
                            elif any(bool(s.get("encore")) for s in sets_list):
                                slot = "Main Set Closer"
                                slot_category = "closer"
                            elif pct <= 35:
                                slot = "Early Set"
                                slot_category = "early"
                            elif pct <= 70:
                                slot = "Mid-Set"
                                slot_category = "mid"
                            else:
                                slot = "Late Set"
                                slot_category = "late"
                        elif pct <= 35:
                            slot = "Early Set"
                            slot_category = "early"
                        elif pct <= 70:
                            slot = "Mid-Set"
                            slot_category = "mid"
                        else:
                            slot = "Late Set"
                            slot_category = "late"

                        cover_info = song_obj_data.get("cover", {})
                        is_cover = bool(cover_info)
                        orig_artist = cover_info.get("name", "") if is_cover else ""
                        info_str = song_obj_data.get("info", "")

                        clean_title_key = name.lower().strip()
                        song_db_obj, _ = Song.objects.get_or_create(
                            artist=primary_art_obj,
                            clean_title=clean_title_key,
                            defaults={
                                'title': name,
                                'is_cover': is_cover,
                                'original_artist': orig_artist or None
                            }
                        )

                        ConcertSong.objects.create(
                            concert_artist=ca,
                            song=song_db_obj,
                            raw_song_name=name,
                            set_name=set_name,
                            is_encore=item["is_encore"],
                            encore_number=item["encore_num"],
                            track_num=track_num,
                            total_tracks=total_tracks,
                            pct_position=pct,
                            slot=slot,
                            slot_category=slot_category,
                            is_cover=is_cover,
                            original_artist=orig_artist,
                            info=info_str
                        )
                        new_songs_to_enrich.append((primary_art_obj.name, name))

                created_concerts.append(concert)

        # Clear dashboard bundle cache for this user
        ApiCache.objects.filter(cache_key=f"user_dashboard_bundle_{user.id}").delete()

        # Trigger background sync
        sync_worker.enqueue_sync(user.id)

        return JsonResponse({
            "status": "success",
            "imported_count": len(created_concerts),
            "message": f"Successfully imported {len(created_concerts)} concerts from Ticketmaster! Syncing..."
        })
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


@login_required
def autocomplete_view(request):
    field_type = request.GET.get('type', '').strip().lower()
    q = request.GET.get('q', '').strip()
    user = request.user
    results = []

    if field_type == 'venue':
        seen_keys = set()
        # 1. User's venues ranked by attendance frequency
        user_venues_qs = (
            Concert.objects.filter(user=user, venue__isnull=False)
            .values('venue__name', 'venue__city', 'venue__state')
            .annotate(show_count=Count('id'))
            .order_by('-show_count', 'venue__name')
        )
        if q:
            user_venues_qs = user_venues_qs.filter(venue__name__icontains=q)

        for v in user_venues_qs:
            name = (v.get('venue__name') or '').strip()
            city = (v.get('venue__city') or '').strip()
            state = (v.get('venue__state') or '').strip()
            count = v.get('show_count', 1)
            if not name:
                continue
            key = (name.lower(), city.lower(), state.lower())
            if key not in seen_keys:
                seen_keys.add(key)
                results.append({
                    'name': name,
                    'city': city,
                    'state': state,
                    'count': count,
                    'is_frequent': True,
                    'is_recent': True
                })
            if len(results) >= (10 if q else 15):
                break

        # 2. Other venues ranked by overall frequency
        if len(results) < 20:
            other_venues_qs = Venue.objects.annotate(show_count=Count('concerts')).order_by('-show_count', 'name')
            if q:
                other_venues_qs = other_venues_qs.filter(name__icontains=q)
            for v in other_venues_qs.values('name', 'city', 'state', 'show_count')[:50]:
                name = (v.get('name') or '').strip()
                city = (v.get('city') or '').strip()
                state = (v.get('state') or '').strip()
                if not name:
                    continue
                key = (name.lower(), city.lower(), state.lower())
                if key not in seen_keys:
                    seen_keys.add(key)
                    results.append({
                        'name': name,
                        'city': city,
                        'state': state,
                        'is_frequent': False,
                        'is_recent': False
                    })
                if len(results) >= 20:
                    break

    elif field_type == 'artist':
        seen_artists = set()
        # 1. User's artists ranked by attendance frequency
        user_artists_qs = (
            ConcertArtist.objects.filter(concert__user=user, artist__isnull=False)
            .values('artist__name')
            .annotate(show_count=Count('id'))
            .order_by('-show_count', 'artist__name')
        )
        if q:
            user_artists_qs = user_artists_qs.filter(artist__name__icontains=q)

        for a in user_artists_qs:
            clean_name = (a.get('artist__name') or '').strip()
            count = a.get('show_count', 1)
            if not clean_name:
                continue
            norm = clean_name.lower()
            if norm not in seen_artists:
                seen_artists.add(norm)
                results.append({
                    'name': clean_name,
                    'count': count,
                    'is_frequent': True,
                    'is_recent': True
                })
            if len(results) >= (10 if q else 15):
                break

        # 2. Other artists ranked by overall frequency
        if len(results) < 20:
            other_artists_qs = Artist.objects.annotate(show_count=Count('concert_appearances')).order_by('-show_count', 'name')
            if q:
                other_artists_qs = other_artists_qs.filter(name__icontains=q)
            for a in other_artists_qs.values('name')[:50]:
                clean_name = (a.get('name') or '').strip()
                if not clean_name:
                    continue
                norm = clean_name.lower()
                if norm not in seen_artists:
                    seen_artists.add(norm)
                    results.append({
                        'name': clean_name,
                        'is_frequent': False,
                        'is_recent': False
                    })
                if len(results) >= 20:
                    break

    elif field_type == 'city':
        seen_cities = set()
        # 1. User's cities ranked by frequency
        user_cities_qs = (
            Concert.objects.filter(user=user, venue__isnull=False)
            .exclude(venue__city='')
            .values('venue__city')
            .annotate(show_count=Count('id'))
            .order_by('-show_count', 'venue__city')
        )
        if q:
            user_cities_qs = user_cities_qs.filter(venue__city__icontains=q)

        for c in user_cities_qs:
            clean_c = (c.get('venue__city') or '').strip()
            count = c.get('show_count', 1)
            if not clean_c:
                continue
            norm = clean_c.lower()
            if norm not in seen_cities:
                seen_cities.add(norm)
                results.append({
                    'name': clean_c,
                    'count': count,
                    'is_frequent': True,
                    'is_recent': True
                })
            if len(results) >= (10 if q else 15):
                break

        # 2. Global cities ranked by frequency
        if len(results) < 20:
            global_cities_qs = (
                Venue.objects.exclude(city='')
                .values('city')
                .annotate(show_count=Count('concerts'))
                .order_by('-show_count', 'city')
            )
            if q:
                global_cities_qs = global_cities_qs.filter(city__icontains=q)
            for c in global_cities_qs[:50]:
                clean_c = (c.get('city') or '').strip()
                if not clean_c:
                    continue
                norm = clean_c.lower()
                if norm not in seen_cities:
                    seen_cities.add(norm)
                    results.append({
                        'name': clean_c,
                        'is_frequent': False,
                        'is_recent': False
                    })
                if len(results) >= 20:
                    break

    elif field_type == 'state':
        seen_states = set()
        # 1. User's states ranked by frequency
        user_states_qs = (
            Concert.objects.filter(user=user, venue__isnull=False)
            .exclude(venue__state='')
            .values('venue__state')
            .annotate(show_count=Count('id'))
            .order_by('-show_count', 'venue__state')
        )
        if q:
            user_states_qs = user_states_qs.filter(venue__state__icontains=q)

        for s in user_states_qs:
            clean_s = (s.get('venue__state') or '').strip()
            count = s.get('show_count', 1)
            if not clean_s:
                continue
            norm = clean_s.lower()
            if norm not in seen_states:
                seen_states.add(norm)
                results.append({
                    'name': clean_s,
                    'count': count,
                    'is_frequent': True,
                    'is_recent': True
                })
            if len(results) >= (10 if q else 15):
                break

        # 2. Global states ranked by frequency
        if len(results) < 20:
            global_states_qs = (
                Venue.objects.exclude(state='')
                .values('state')
                .annotate(show_count=Count('concerts'))
                .order_by('-show_count', 'state')
            )
            if q:
                global_states_qs = global_states_qs.filter(state__icontains=q)
            for s in global_states_qs[:50]:
                clean_s = (s.get('state') or '').strip()
                if not clean_s:
                    continue
                norm = clean_s.lower()
                if norm not in seen_states:
                    seen_states.add(norm)
                    results.append({
                        'name': clean_s,
                        'is_frequent': False,
                        'is_recent': False
                    })
                if len(results) >= 20:
                    break

    return JsonResponse({'results': results})


def format_concert_drilldown_dates(concerts_drilldown):
    """
    Ensures every concert item in drilldown has month_day (e.g. 'Sept 1') and year (e.g. 2026)
    properly extracted, preventing duplicate years when rendering date cells.
    """
    formatted = []
    for c in concerts_drilldown:
        c_copy = dict(c)
        date_str = str(c_copy.get("date") or c_copy.get("raw_date") or "").strip()
        dt = None
        for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%m-%d-%Y", "%Y/%m/%d", "%m/%d/%Y", "%d/%m/%Y", "%b %d, %Y", "%B %d, %Y"):
            try:
                dt = datetime.strptime(date_str[:12].strip(), fmt).date()
                break
            except (ValueError, TypeError):
                pass

        if dt:
            month_name = dt.strftime("%b")
            if month_name == "Sep":
                month_name = "Sept"
            c_copy["month_day"] = f"{month_name} {dt.day}"
            c_copy["year"] = dt.year
            c_copy["formatted_date"] = f"{month_name} {dt.day}, {dt.year}"
        else:
            m = re.match(r'^([A-Za-z]+)\s+(\d{1,2}),?\s+(\d{4})$', date_str)
            if m:
                c_copy["month_day"] = f"{m.group(1)} {m.group(2)}"
                c_copy["year"] = int(m.group(3))
                c_copy["formatted_date"] = f"{m.group(1)} {m.group(2)}, {m.group(3)}"
            else:
                c_copy["month_day"] = c_copy.get("month_day") or date_str
                c_copy["year"] = c_copy.get("year") or ""
                c_copy["formatted_date"] = c_copy.get("formatted_date") or date_str
        formatted.append(c_copy)
    return formatted


def check_viewer_attendance_for_drilldown(viewer_user, concerts_drilldown, target_user):
    """
    Evaluates whether the logged-in viewing user has logged each concert
    present in the target user's concerts drilldown list.
    """
    if not viewer_user or not viewer_user.is_authenticated:
        return concerts_drilldown

    viewer_concerts = list(
        Concert.objects.filter(user=viewer_user)
        .select_related('venue')
        .prefetch_related('artists__artist')
    )

    viewer_sl_ids = set()
    viewer_date_artist_map = {}  # (date_key, norm_art) -> vc

    for vc in viewer_concerts:
        d_keys = set()
        if vc.date:
            d_keys.add(vc.date.strftime("%Y-%m-%d"))
            d_keys.add(vc.date.strftime("%m-%d-%Y"))
            d_keys.add(vc.date.strftime("%m/%d/%Y"))
            d_keys.add(vc.date.strftime("%d-%m-%Y"))
        if vc.raw_date:
            raw_s = vc.raw_date.strip()
            d_keys.add(raw_s)
            d_keys.add(raw_s.replace('-', '/'))
            d_keys.add(raw_s.replace('/', '-'))

        v_art_names = {normalize_artist_name(ca.artist.name) or ca.artist.name.lower().strip() for ca in vc.artists.all()}
        if vc.primary_artist:
            v_art_names.add(normalize_artist_name(vc.primary_artist) or vc.primary_artist.lower().strip())

        for d_k in d_keys:
            for a_name in v_art_names:
                if a_name:
                    viewer_date_artist_map[(d_k, a_name)] = vc

        for ca in vc.artists.all():
            if ca.setlistfm_id:
                viewer_sl_ids.add(ca.setlistfm_id)

    updated_drilldown = []
    for c in concerts_drilldown:
        c_copy = dict(c)
        c_id = c_copy.get("id", "")
        db_id = c_copy.get("db_id")
        if db_id is None and str(c_id).startswith("concert_"):
            try:
                db_id = int(str(c_id).replace("concert_", ""))
                c_copy["db_id"] = db_id
            except ValueError:
                pass

        is_attended = False
        matching_vc = None

        if target_user and target_user.id == viewer_user.id:
            is_attended = True
        else:
            # 1. Check artist and date matches
            for a in c_copy.get("artists", []):
                a_name = normalize_artist_name(a.get("artist", "")) or a.get("artist", "").lower().strip()
                c_date = c_copy.get("date", "").strip()
                date_variants = {c_date, c_date.replace('-', '/'), c_date.replace('/', '-')}
                for d_var in date_variants:
                    if (d_var, a_name) in viewer_date_artist_map:
                        is_attended = True
                        matching_vc = viewer_date_artist_map[(d_var, a_name)]
                        break
                if is_attended:
                    break

            if not is_attended:
                c_date = c_copy.get("date", "").strip()
                date_variants = {c_date, c_date.replace('-', '/'), c_date.replace('/', '-')}
                raw_arts = c_copy.get("raw_artists", "")
                parts = [p.strip() for p in re.split(r'[,;/]+', raw_arts) if p.strip()]
                for p in parts:
                    norm_p = normalize_artist_name(p) or p.lower().strip()
                    for d_var in date_variants:
                        if (d_var, norm_p) in viewer_date_artist_map:
                            is_attended = True
                            matching_vc = viewer_date_artist_map[(d_var, norm_p)]
                            break
                    if is_attended:
                        break

            # 2. Check Setlist.fm IDs if target concert exists in DB
            if not is_attended and db_id:
                tc = Concert.objects.filter(id=db_id).prefetch_related('artists').first()
                if tc:
                    tc_sl_ids = {ca.setlistfm_id for ca in tc.artists.all() if ca.setlistfm_id}
                    if tc_sl_ids & viewer_sl_ids:
                        is_attended = True

        c_copy["is_attended_by_viewer"] = is_attended
        if matching_vc:
            c_copy["viewer_matching_concert_id"] = matching_vc.id

        updated_drilldown.append(c_copy)

    return updated_drilldown


@login_required
@require_POST
def toggle_concert_attendance(request):
    """
    Toggles attendance for a concert from a public profile to the authenticated user's profile.
    If already attended (active), de-selects and removes the concert from the user's profile.
    If not yet attended, logs the concert and its full artists and setlists to the user's profile.
    """
    try:
        try:
            data = json.loads(request.body.decode('utf-8'))
        except Exception:
            data = request.POST

        concert_id_raw = data.get('concert_id')
        if not concert_id_raw:
            return JsonResponse({"error": "Missing concert_id in request"}, status=400)

        c_id_str = str(concert_id_raw).strip()
        if c_id_str.startswith("concert_"):
            c_id_str = c_id_str.replace("concert_", "")

        try:
            target_concert_id = int(c_id_str)
        except ValueError:
            return JsonResponse({"error": f"Invalid concert_id: {concert_id_raw}"}, status=400)

        target_concert = Concert.objects.filter(id=target_concert_id).select_related('venue', 'user__profile').prefetch_related('artists__artist', 'artists__songs__song').first()
        if not target_concert:
            return JsonResponse({"error": "Concert not found"}, status=404)

        is_owner = (request.user.id == target_concert.user_id)
        if not target_concert.user.profile.is_public and not is_owner and not request.user.is_staff:
            return JsonResponse({"error": "This profile is private."}, status=403)

        # Find existing matching concert for request.user
        viewer_concerts = list(Concert.objects.filter(user=request.user).prefetch_related('artists__artist', 'artists__songs'))
        existing_match = None

        if target_concert.user_id == request.user.id:
            existing_match = target_concert
        else:
            # 1. Check setlistfm_id match
            target_sl_ids = {ca.setlistfm_id for ca in target_concert.artists.all() if ca.setlistfm_id}
            if target_sl_ids:
                for vc in viewer_concerts:
                    vc_sl_ids = {ca.setlistfm_id for ca in vc.artists.all() if ca.setlistfm_id}
                    if target_sl_ids & vc_sl_ids:
                        existing_match = vc
                        break

            # 2. Check date + artist match
            if not existing_match:
                target_art_names = {normalize_artist_name(ca.artist.name) or ca.artist.name.lower().strip() for ca in target_concert.artists.all()}
                if target_concert.primary_artist:
                    target_art_names.add(normalize_artist_name(target_concert.primary_artist) or target_concert.primary_artist.lower().strip())

                for vc in viewer_concerts:
                    date_match = False
                    if vc.date and target_concert.date and vc.date == target_concert.date:
                        date_match = True
                    elif vc.raw_date and target_concert.raw_date and vc.raw_date.strip().replace('-', '/') == target_concert.raw_date.strip().replace('-', '/'):
                        date_match = True

                    if date_match:
                        vc_art_names = {normalize_artist_name(ca.artist.name) or ca.artist.name.lower().strip() for ca in vc.artists.all()}
                        if vc.primary_artist:
                            vc_art_names.add(normalize_artist_name(vc.primary_artist) or vc.primary_artist.lower().strip())

                        if target_art_names & vc_art_names:
                            existing_match = vc
                            break

        if existing_match:
            # De-select / remove from profile
            existing_match_id = existing_match.id
            artist_display = existing_match.primary_artist
            date_display = existing_match.raw_date
            existing_match.delete()

            # Invalidate cache for request.user
            ApiCache.objects.filter(cache_key=f"user_dashboard_bundle_{request.user.id}").delete()
            sync_worker.enqueue_sync(request.user.id)

            return JsonResponse({
                "status": "success",
                "action": "removed",
                "is_attended": False,
                "concert_id": target_concert.id,
                "message": f"Removed '{artist_display}' on {date_display} from your profile."
            })
        else:
            # Log concert to request.user's profile
            # If user does not have a setlist.fm username, or target concert is offline, treat as custom offline
            is_non_setlistfm = not bool(request.user.profile.setlistfm_username)
            is_offline = target_concert.is_custom_offline or is_non_setlistfm

            with transaction.atomic():
                new_concert = Concert.objects.create(
                    user=request.user,
                    date=target_concert.date,
                    raw_date=target_concert.raw_date,
                    year=target_concert.year,
                    venue=target_concert.venue,
                    raw_venue=target_concert.raw_venue,
                    primary_artist=target_concert.primary_artist,
                    raw_artists=target_concert.raw_artists,
                    seen_before="",
                    notes=target_concert.notes,
                    source='manual',
                    is_custom_offline=is_offline,
                    is_fully_matched=target_concert.is_fully_matched,
                    is_partially_matched=target_concert.is_partially_matched
                )

                if target_concert.artists.exists():
                    for ca in target_concert.artists.all():
                        new_ca = ConcertArtist.objects.create(
                            concert=new_concert,
                            artist=ca.artist,
                            billing_order=ca.billing_order,
                            setlistfm_id='' if is_non_setlistfm else ca.setlistfm_id,
                            setlist_url='' if is_non_setlistfm else ca.setlist_url,
                            has_setlist=ca.has_setlist
                        )
                        for cs in ca.songs.all():
                            ConcertSong.objects.create(
                                concert_artist=new_ca,
                                song=cs.song,
                                raw_song_name=cs.raw_song_name,
                                set_name=cs.set_name,
                                is_encore=cs.is_encore,
                                encore_number=cs.encore_number,
                                track_num=cs.track_num,
                                total_tracks=cs.total_tracks,
                                pct_position=cs.pct_position,
                                slot=cs.slot,
                                slot_category=cs.slot_category,
                                is_cover=cs.is_cover,
                                original_artist=cs.original_artist,
                                info=cs.info
                            )
                elif target_concert.primary_artist:
                    can_primary = normalize_artist_name(target_concert.primary_artist) or target_concert.primary_artist
                    art_obj, _ = get_or_create_artist(can_primary)
                    ConcertArtist.objects.create(
                        concert=new_concert,
                        artist=art_obj,
                        billing_order=0,
                        has_setlist=False
                    )

            # Invalidate cache for request.user
            ApiCache.objects.filter(cache_key=f"user_dashboard_bundle_{request.user.id}").delete()
            sync_worker.enqueue_sync(request.user.id)

            return JsonResponse({
                "status": "success",
                "action": "added",
                "is_attended": True,
                "concert_id": target_concert.id,
                "user_concert_id": new_concert.id,
                "message": f"Logged '{target_concert.primary_artist}' on {target_concert.raw_date} to your profile!"
            })
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


@login_required
@require_POST
def toggle_concert_favorite(request):
    """
    Toggles the is_favorite boolean for a concert belonging to the authenticated user.
    """
    try:
        try:
            data = json.loads(request.body.decode('utf-8'))
        except Exception:
            data = request.POST

        concert_id_raw = data.get('concert_id')
        if not concert_id_raw:
            return JsonResponse({"error": "Missing concert_id in request"}, status=400)

        c_id_str = str(concert_id_raw).strip()
        if c_id_str.startswith("concert_"):
            c_id_str = c_id_str.replace("concert_", "")

        try:
            target_concert_id = int(c_id_str)
        except ValueError:
            return JsonResponse({"error": f"Invalid concert_id: {concert_id_raw}"}, status=400)

        target_concert = Concert.objects.filter(id=target_concert_id, user=request.user).first()
        if not target_concert:
            return JsonResponse({"error": "Concert not found or not owned by user"}, status=404)

        target_concert.is_favorite = not target_concert.is_favorite
        target_concert.save(update_fields=['is_favorite'])

        # Update cache bundle if present
        cache_key = f"user_dashboard_bundle_{request.user.id}"
        bundle = ApiCache.objects.filter(cache_key=cache_key).first()
        if bundle and isinstance(bundle.payload, dict):
            drilldown = bundle.payload.get("stats", {}).get("concerts_drilldown", [])
            for c in drilldown:
                if c.get("db_id") == target_concert.id or c.get("id") == f"concert_{target_concert.id}":
                    c["is_favorite"] = target_concert.is_favorite
            bundle.save(update_fields=['payload'])

        return JsonResponse({
            "status": "success",
            "concert_id": target_concert.id,
            "is_favorite": target_concert.is_favorite
        })
    except Exception as e:
        logger.error("Error toggling favorite for concert: %s", e)
        return JsonResponse({"error": str(e)}, status=500)


@login_required
@require_POST
def toggle_concert_artist_favorite(request):
    """
    Toggles the is_favorite boolean for a specific ConcertArtist appearance belonging to the authenticated user.
    """
    try:
        try:
            data = json.loads(request.body.decode('utf-8'))
        except Exception:
            data = request.POST

        ca_id_raw = data.get('ca_id')
        concert_id_raw = data.get('concert_id')
        artist_name = (data.get('artist') or '').strip()

        target_ca = None
        if ca_id_raw:
            try:
                target_ca = ConcertArtist.objects.select_related('concert', 'artist').filter(
                    id=int(ca_id_raw),
                    concert__user=request.user
                ).first()
            except (ValueError, TypeError):
                pass

        if not target_ca and concert_id_raw and artist_name:
            c_id_str = str(concert_id_raw).strip()
            if c_id_str.startswith("concert_"):
                c_id_str = c_id_str.replace("concert_", "")
            try:
                c_id = int(c_id_str)
                target_ca = ConcertArtist.objects.select_related('concert', 'artist').filter(
                    concert__id=c_id,
                    concert__user=request.user,
                    artist__name__iexact=artist_name
                ).first()
            except (ValueError, TypeError):
                pass

        if not target_ca:
            return JsonResponse({"error": "Concert artist appearance not found or not owned by user"}, status=404)

        target_ca.is_favorite = not target_ca.is_favorite
        target_ca.save(update_fields=['is_favorite'])

        # Check if any artist in the concert is favorited
        has_fav_artist = ConcertArtist.objects.filter(concert=target_ca.concert, is_favorite=True).exists()

        # Update dashboard cache bundle if present
        cache_key = f"user_dashboard_bundle_{request.user.id}"
        bundle = ApiCache.objects.filter(cache_key=cache_key).first()
        if bundle and isinstance(bundle.payload, dict):
            drilldown = bundle.payload.get("stats", {}).get("concerts_drilldown", [])
            for c in drilldown:
                if c.get("db_id") == target_ca.concert.id or c.get("id") == f"concert_{target_ca.concert.id}":
                    c["has_favorite_artist"] = has_fav_artist
                    for a in c.get("artists", []):
                        if (target_ca.id and a.get("ca_id") == target_ca.id) or a.get("artist", "").strip().lower() == target_ca.artist.name.strip().lower():
                            a["is_favorite"] = target_ca.is_favorite
                            if not a.get("ca_id"):
                                a["ca_id"] = target_ca.id
            bundle.save(update_fields=['payload'])

        return JsonResponse({
            "status": "success",
            "ca_id": target_ca.id,
            "concert_id": target_ca.concert.id,
            "artist": target_ca.artist.name,
            "is_favorite": target_ca.is_favorite,
            "has_favorite_artist": has_fav_artist
        })
    except Exception as e:
        logger.error("Error toggling favorite for concert artist: %s", e)
        return JsonResponse({"error": str(e)}, status=500)


@login_required
@require_POST
def edit_concert(request):
    try:
        try:
            data = json.loads(request.body.decode('utf-8'))
        except Exception:
            data = request.POST

        concert_id_raw = str(data.get('concert_id') or data.get('id') or '').strip()
        if concert_id_raw.startswith('concert_'):
            concert_id_raw = concert_id_raw.replace('concert_', '')

        try:
            concert_id = int(concert_id_raw)
        except (ValueError, TypeError):
            return JsonResponse({"error": "Invalid concert ID"}, status=400)

        concert = Concert.objects.filter(id=concert_id, user=request.user).first()
        if not concert:
            return JsonResponse({"error": "Concert not found or access denied"}, status=404)

        date_str = str(data.get('date', '')).strip()
        primary_artist_raw = str(data.get('primary_artist', '')).strip()
        supporting_artists_raw = str(data.get('supporting_artists', '')).strip()
        venue_name_raw = str(data.get('venue_name', '')).strip()
        city = str(data.get('city', '')).strip()
        state = str(data.get('state', '')).strip()
        country = str(data.get('country', '')).strip() or 'United States'
        notes = str(data.get('notes', '')).strip()
        setlist_text = str(data.get('setlist_text', '')).strip()
        convert_to_local = data.get('convert_to_local', False)
        if isinstance(convert_to_local, str):
            convert_to_local = convert_to_local.lower() in ['true', '1', 'on', 'yes']

        is_custom_offline = data.get('is_custom_offline')
        if is_custom_offline is not None:
            if isinstance(is_custom_offline, str):
                is_custom_offline = is_custom_offline.lower() in ['true', '1', 'on', 'yes']
        else:
            is_custom_offline = concert.is_custom_offline

        if convert_to_local:
            is_custom_offline = True
            concert.source = 'manual'

        if not date_str:
            return JsonResponse({"error": "Date is required."}, status=400)
        if not primary_artist_raw:
            return JsonResponse({"error": "Headliner / Primary Artist is required."}, status=400)
        if not venue_name_raw:
            return JsonResponse({"error": "Venue name is required."}, status=400)

        dt = None
        for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m-%d-%Y", "%d-%m-%Y"):
            try:
                dt = datetime.strptime(date_str, fmt)
                break
            except ValueError:
                pass

        if not dt:
            return JsonResponse({"error": "Invalid date format. Please use YYYY-MM-DD or MM/DD/YYYY."}, status=400)

        year = dt.year
        raw_date = dt.strftime("%m/%d/%Y")

        new_songs_to_enrich = []
        with transaction.atomic():
            # Venue resolution / update
            venue_obj = Venue.objects.filter(name__iexact=venue_name_raw.lower()).first()
            if not venue_obj:
                lat, lng, source_type = resolve_venue_coordinates(venue_name_raw, city, state, country)
                venue_obj = Venue.objects.create(
                    name=venue_name_raw,
                    city=city,
                    state=state,
                    country=country,
                    latitude=lat,
                    longitude=lng,
                    geocode_source=source_type
                )
            elif (city or state) and (not venue_obj.city or not venue_obj.state):
                venue_obj.city = city or venue_obj.city
                venue_obj.state = state or venue_obj.state
                if venue_obj.latitude is None:
                    lat, lng, source_type = resolve_venue_coordinates(venue_name_raw, venue_obj.city, venue_obj.state, country)
                    if lat is not None:
                        venue_obj.latitude = lat
                        venue_obj.longitude = lng
                        venue_obj.geocode_source = source_type
                venue_obj.save()

            # Artist resolution
            can_primary = normalize_artist_name(primary_artist_raw) or primary_artist_raw
            primary_art_obj, _ = get_or_create_artist(can_primary)

            artists_list = [can_primary]
            supporting_objs = []
            if supporting_artists_raw:
                parts = re.split(r'[,;/]+', supporting_artists_raw)
                for p in parts:
                    p_clean = p.strip()
                    if p_clean:
                        can_supp = normalize_artist_name(p_clean) or p_clean
                        supp_obj, _ = get_or_create_artist(can_supp)
                        artists_list.append(can_supp)
                        supporting_objs.append(supp_obj)

            raw_artists = ", ".join(artists_list)

            # Update Concert fields
            concert.date = dt.date()
            concert.raw_date = raw_date
            concert.year = year
            concert.venue = venue_obj
            concert.raw_venue = venue_obj.name
            concert.primary_artist = can_primary
            concert.raw_artists = raw_artists
            concert.notes = notes
            concert.is_custom_offline = bool(is_custom_offline)
            concert.save()

            # Update primary ConcertArtist
            ca_primary = ConcertArtist.objects.filter(concert=concert, billing_order=0).first()
            if not ca_primary:
                ca_primary = ConcertArtist.objects.create(
                    concert=concert,
                    artist=primary_art_obj,
                    billing_order=0,
                    has_setlist=bool(setlist_text)
                )
            else:
                ca_primary.artist = primary_art_obj
                if convert_to_local:
                    ca_primary.setlistfm_id = ''
                    ca_primary.setlist_url = ''
                ca_primary.save()

            # Remove existing supporting ConcertArtists and recreate with new ones
            ConcertArtist.objects.filter(concert=concert, billing_order__gt=0).delete()
            for idx, s_obj in enumerate(supporting_objs, start=1):
                ConcertArtist.objects.create(
                    concert=concert,
                    artist=s_obj,
                    billing_order=idx,
                    has_setlist=False
                )

            # Update setlist songs for primary artist if setlist_text is specified
            if 'setlist_text' in data:
                if setlist_text:
                    parsed_tracks = parse_setlist_text(setlist_text)
                    if parsed_tracks:
                        ca_primary.songs.all().delete()
                        new_songs_to_enrich = save_setlist_for_concert_artist(ca_primary, primary_art_obj, parsed_tracks)
                        ca_primary.has_setlist = True
                        ca_primary.save()
                else:
                    ca_primary.songs.all().delete()
                    ca_primary.has_setlist = False
                    ca_primary.save()

        # Invalidate cached analytics bundle for this user
        ApiCache.objects.filter(cache_key=f"user_dashboard_bundle_{request.user.id}").delete()

        # Optional quick album and musician enrichment for new artists/songs
        if new_songs_to_enrich:
            try:
                enricher = AlbumEnricher()
                enricher.load_cached_catalog([{"artist": a, "song": s} for a, s in new_songs_to_enrich])
            except Exception:
                pass

        try:
            m_enricher = MusicianEnricher()
            is_upcoming_or_current = bool(concert.date and concert.date.year >= datetime.now().year)
            if not primary_art_obj.members.exists():
                m_enricher.enrich_artist(primary_art_obj.name, artist_obj=primary_art_obj)
            elif is_upcoming_or_current:
                m_enricher.enrich_artist(primary_art_obj.name, artist_obj=primary_art_obj, refresh=True)
        except Exception:
            pass

        return JsonResponse({
            "status": "success",
            "message": f"Concert for '{can_primary}' on {raw_date} updated successfully!",
            "concert_id": concert.id
        })
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


@login_required
@require_POST
def delete_concert(request):
    try:
        try:
            data = json.loads(request.body.decode('utf-8'))
        except Exception:
            data = request.POST

        concert_id_raw = str(data.get('concert_id') or data.get('id') or '').strip()
        if concert_id_raw.startswith('concert_'):
            concert_id_raw = concert_id_raw.replace('concert_', '')

        try:
            concert_id = int(concert_id_raw)
        except (ValueError, TypeError):
            return JsonResponse({"error": "Invalid concert ID"}, status=400)

        concert = Concert.objects.filter(id=concert_id, user=request.user).first()
        if not concert:
            return JsonResponse({"error": "Concert not found or access denied"}, status=404)

        artist_display = concert.primary_artist
        date_display = concert.raw_date
        concert.delete()

        # Invalidate dashboard cache
        ApiCache.objects.filter(cache_key=f"user_dashboard_bundle_{request.user.id}").delete()

        return JsonResponse({
            "status": "success",
            "message": f"Deleted concert for '{artist_display}' on {date_display}."
        })
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


@login_required
@require_POST
def convert_concert_to_local(request):
    try:
        try:
            data = json.loads(request.body.decode('utf-8'))
        except Exception:
            data = request.POST

        concert_id_raw = str(data.get('concert_id') or data.get('id') or '').strip()
        if concert_id_raw.startswith('concert_'):
            concert_id_raw = concert_id_raw.replace('concert_', '')

        try:
            concert_id = int(concert_id_raw)
        except (ValueError, TypeError):
            return JsonResponse({"error": "Invalid concert ID"}, status=400)

        concert = Concert.objects.filter(id=concert_id, user=request.user).first()
        if not concert:
            return JsonResponse({"error": "Concert not found or access denied"}, status=404)

        with transaction.atomic():
            concert.is_custom_offline = True
            concert.source = 'manual'
            concert.save()

            for ca in concert.artists.all():
                ca.setlistfm_id = ''
                ca.setlist_url = ''
                ca.save()

        # Invalidate dashboard cache
        ApiCache.objects.filter(cache_key=f"user_dashboard_bundle_{request.user.id}").delete()

        return JsonResponse({
            "status": "success",
            "message": f"Converted '{concert.primary_artist}' ({concert.raw_date}) to a local show.",
            "concert_id": concert.id,
            "is_custom_offline": True,
            "source": "manual"
        })
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


def export_concerts_csv(request, username=None):
    """
    Exports a CSV file of concerts containing Date, Artist(s), Venue, and Setlist columns.
    """
    clean_username = (username or request.GET.get('username', '')).lstrip('@').strip()

    if clean_username:
        target_user = User.objects.filter(username__iexact=clean_username).first()
        if not target_user:
            return HttpResponseBadRequest("User not found")

        is_owner = request.user.is_authenticated and (request.user.id == target_user.id)
        is_staff = request.user.is_authenticated and request.user.is_staff
        if not target_user.profile.is_public and not is_owner and not is_staff:
            return HttpResponseBadRequest("This profile is private.")
        is_public_view = not is_owner
    else:
        if not request.user.is_authenticated:
            return redirect('login')
        target_user = request.user
        is_public_view = False

    context = get_dashboard_context(request, target_user, tab_name='concerts', is_public_view=is_public_view)
    concerts_drilldown = context.get('stats', {}).get('concerts_drilldown', [])
    # Export from oldest on top to newest date on bottom
    chronological_concerts = list(reversed(concerts_drilldown))

    response = HttpResponse(content_type='text/csv; charset=utf-8')
    filename = f"{target_user.username}_concerts.csv"
    response['Content-Disposition'] = f'attachment; filename="{filename}"'

    writer = csv.writer(response)
    writer.writerow(['Date', 'Artist(s)', 'Venue', 'Setlist'])

    for c in chronological_concerts:
        date_str = c.get('date', '') or ''
        artists_str = c.get('raw_artists', '') or ''
        venue_str = c.get('venue', '') or ''

        # Extract setlist
        artist_setlists = []
        artists_list = c.get('artists', [])
        for a in artists_list:
            art_name = a.get('artist', '')
            set_parts = []
            grouped_sets = a.get('grouped_sets', [])
            for sg in grouped_sets:
                set_label = sg.get('set_label', '')
                is_encore = sg.get('is_encore', False)
                songs_in_sg = []
                for track in sg.get('songs', []):
                    s_name = track.get('song', '').strip()
                    if s_name:
                        if track.get('is_cover') and track.get('cover_original'):
                            s_name = f"{s_name} ({track.get('cover_original')} cover)"
                        elif track.get('is_cover'):
                            s_name = f"{s_name} (Cover)"
                        if track.get('info'):
                            s_name = f"{s_name} ({track.get('info')})"
                        songs_in_sg.append(s_name)

                if songs_in_sg:
                    if is_encore:
                        prefix = f"{set_label}: " if set_label else "Encore: "
                    elif set_label and set_label not in ("Main Set", "Set 1"):
                        prefix = f"{set_label}: "
                    elif len(grouped_sets) > 1 and set_label == "Set 1":
                        prefix = "Set 1: "
                    else:
                        prefix = ""
                    set_parts.append(f"{prefix}{', '.join(songs_in_sg)}")

            if set_parts:
                art_setlist_str = ", ".join(set_parts)
                if len(artists_list) > 1:
                    artist_setlists.append(f"{art_name}: {art_setlist_str}")
                else:
                    artist_setlists.append(art_setlist_str)

        setlist_str = " | ".join(artist_setlists) if artist_setlists else ""
        writer.writerow([date_str, artists_str, venue_str, setlist_str])

    return response


@login_required
@require_POST
def save_setlist(request):
    """
    Saves or updates the setlist tracks for a specific artist at a specific concert.
    Accepts JSON with:
      - concert_id: int or string (e.g. 10 or 'concert_10')
      - artist: artist name
      - setlist_text: raw multiline text of songs
    """
    try:
        if request.content_type == 'application/json':
            try:
                data = json.loads(request.body)
            except Exception:
                return JsonResponse({'error': 'Invalid JSON body'}, status=400)
        else:
            data = request.POST

        raw_concert_id = str(data.get('concert_id', '')).strip()
        if raw_concert_id.startswith('concert_'):
            raw_concert_id = raw_concert_id.replace('concert_', '')

        try:
            concert_id = int(raw_concert_id)
        except (ValueError, TypeError):
            return JsonResponse({'error': 'Invalid concert ID'}, status=400)

        artist_name = str(data.get('artist', '')).strip()
        setlist_text = str(data.get('setlist_text', '')).strip()

        if not artist_name:
            return JsonResponse({'error': 'Artist name is required'}, status=400)
        if not setlist_text:
            return JsonResponse({'error': 'Please enter at least one song.'}, status=400)

        concert = Concert.objects.filter(id=concert_id, user=request.user).first()
        if not concert:
            return JsonResponse({'error': 'Concert not found or access denied'}, status=404)

        # Get or create Artist
        artist_obj, _ = get_or_create_artist(artist_name)

        # Get or create ConcertArtist
        ca = ConcertArtist.objects.filter(concert=concert, artist__name__iexact=artist_name).first()
        if not ca:
            billing_order = concert.artists.count()
            ca = ConcertArtist.objects.create(
                concert=concert,
                artist=artist_obj,
                billing_order=billing_order,
                has_setlist=False
            )

        # Parse setlist text
        parsed_tracks = parse_setlist_text(setlist_text)
        if not parsed_tracks:
            return JsonResponse({'error': 'Could not parse any valid songs from input.'}, status=400)

        with transaction.atomic():
            new_songs_to_enrich = save_setlist_for_concert_artist(ca, artist_obj, parsed_tracks)

        total_tracks = len(parsed_tracks)

        # Invalidate cached analytics bundle for this user
        ApiCache.objects.filter(cache_key=f"user_dashboard_bundle_{request.user.id}").delete()

        # Quick album enrichment for new tracks
        if new_songs_to_enrich:
            try:
                enricher = AlbumEnricher()
                enricher.load_cached_catalog([{"artist": a, "song": s} for a, s in new_songs_to_enrich])
            except Exception:
                pass

        return JsonResponse({
            'success': True,
            'message': f"Successfully saved {total_tracks} songs for {artist_name}.",
            'songs_count': total_tracks
        })
    except Exception as e:
        return JsonResponse({'error': f"Failed to save setlist: {str(e)}"}, status=500)


@login_required
@require_POST
def toggle_upcoming_hidden_artist(request):
    """Toggle or set whether a specific artist is hidden from the upcoming shows overview."""
    try:
        if request.content_type == 'application/json':
            data = json.loads(request.body)
        else:
            data = request.POST

        artist_name = str(data.get('artist', '')).strip()
        is_hidden_param = data.get('is_hidden')

        if not artist_name:
            return JsonResponse({'error': 'Artist name is required'}, status=400)

        profile = request.user.profile
        hidden_list = list(profile.hidden_upcoming_artists or [])

        # Case-insensitive search
        matched_idx = -1
        for idx, a in enumerate(hidden_list):
            if a.lower().strip() == artist_name.lower().strip():
                matched_idx = idx
                break

        if is_hidden_param is None:
            # Toggle
            if matched_idx >= 0:
                hidden_list.pop(matched_idx)
                now_hidden = False
            else:
                hidden_list.append(artist_name)
                now_hidden = True
        else:
            target_hidden = bool(is_hidden_param)
            if target_hidden and matched_idx < 0:
                hidden_list.append(artist_name)
            elif not target_hidden and matched_idx >= 0:
                hidden_list.pop(matched_idx)
            now_hidden = target_hidden

        profile.hidden_upcoming_artists = hidden_list
        profile.save(update_fields=['hidden_upcoming_artists', 'updated_at'])

        # Invalidate cached dashboard bundle
        ApiCache.objects.filter(cache_key=f"user_dashboard_bundle_{request.user.id}").delete()

        return JsonResponse({
            'success': True,
            'artist': artist_name,
            'is_hidden': now_hidden,
            'hidden_count': len(hidden_list),
            'hidden_artists': hidden_list
        })
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)


@login_required
@require_POST
def save_upcoming_settings(request):
    """Save upcoming shows preferences including area (location), range (radius miles), and hidden artists."""
    try:
        if request.content_type == 'application/json':
            data = json.loads(request.body)
        else:
            data = request.POST

        profile = request.user.profile
        update_fields = []

        if 'hidden_artists' in data:
            hidden_artists = data.get('hidden_artists', [])
            if isinstance(hidden_artists, str):
                hidden_artists = [a.strip() for a in hidden_artists.split(',') if a.strip()]
            elif not isinstance(hidden_artists, list):
                hidden_artists = []
            cleaned_list = [str(a).strip() for a in hidden_artists if str(a).strip()]
            profile.hidden_upcoming_artists = cleaned_list
            update_fields.append('hidden_upcoming_artists')

        if 'location' in data or 'upcoming_location' in data:
            raw_loc = str(data.get('location', data.get('upcoming_location', ''))).strip()
            if raw_loc != profile.upcoming_location:
                profile.upcoming_location = raw_loc
                update_fields.append('upcoming_location')
                # Resolve coordinates
                if raw_loc:
                    res_lat, res_lon, _ = resolve_venue_coordinates(raw_loc)
                    profile.upcoming_latitude = res_lat
                    profile.upcoming_longitude = res_lon
                    update_fields.extend(['upcoming_latitude', 'upcoming_longitude'])
                else:
                    profile.upcoming_latitude = None
                    profile.upcoming_longitude = None
                    update_fields.extend(['upcoming_latitude', 'upcoming_longitude'])

        if 'radius_miles' in data or 'upcoming_radius_miles' in data:
            raw_rad = data.get('radius_miles', data.get('upcoming_radius_miles'))
            if raw_rad in (None, '', 'null'):
                radius_val = 100
            else:
                try:
                    radius_val = int(raw_rad)
                    if radius_val <= 0:
                        radius_val = 100
                except (ValueError, TypeError):
                    radius_val = 100
            profile.upcoming_radius_miles = radius_val
            update_fields.append('upcoming_radius_miles')

        if update_fields:
            update_fields.append('updated_at')
            profile.save(update_fields=update_fields)

        # Invalidate cached dashboard bundle
        ApiCache.objects.filter(cache_key=f"user_dashboard_bundle_{request.user.id}").delete()

        # Return fresh upcoming shows payload
        upcoming_data = get_upcoming_shows_for_user(request.user)

        return JsonResponse({
            'success': True,
            'location': profile.upcoming_location,
            'radius_miles': profile.upcoming_radius_miles,
            'hidden_count': len(profile.hidden_upcoming_artists),
            'hidden_artists': profile.hidden_upcoming_artists,
            **upcoming_data
        })
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)


@login_required
def api_upcoming_shows(request):
    """Fetch updated upcoming shows and seen artists metadata for current user."""
    force_refresh = request.GET.get('refresh', '').lower() in ('true', '1', 'yes')
    upcoming_data = get_upcoming_shows_for_user(request.user, force_refresh=force_refresh)
    return JsonResponse({
        'success': True,
        **upcoming_data
    })


@login_required
@require_POST
def track_upcoming_show(request):
    """
    Tracks or pre-adds an upcoming concert to the user's database.
    Pre-adds the show with source='setlistfm' and is_custom_offline=False so setlists can later be synced.
    Toggles between tracked (pre-added) and untracked if already tracked.
    Future concerts are excluded from metrics until they have occurred.
    """
    try:
        if request.content_type == 'application/json':
            data = json.loads(request.body)
        else:
            data = request.POST

        artist_raw = str(data.get('artist') or data.get('artist_name') or '').strip()
        date_str = str(data.get('date') or data.get('datetime') or '').strip()
        venue_name_raw = str(data.get('venue') or data.get('venue_name') or '').strip()
        city = str(data.get('city') or '').strip()
        state = str(data.get('state') or data.get('region') or '').strip()
        country = str(data.get('country') or '').strip() or 'United States'
        action = str(data.get('action') or '').strip().lower()

        if not artist_raw:
            return JsonResponse({'error': 'Artist name is required'}, status=400)
        if not date_str:
            return JsonResponse({'error': 'Date is required'}, status=400)

        dt = None
        clean_date_str = date_str[:10]
        for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m-%d-%Y", "%d-%m-%Y"):
            try:
                dt = datetime.strptime(clean_date_str, fmt)
                break
            except ValueError:
                pass

        if not dt:
            return JsonResponse({'error': f'Invalid date format: {date_str}'}, status=400)

        d_obj = dt.date()
        raw_date = d_obj.strftime("%Y-%m-%d")
        year = d_obj.year

        can_artist = normalize_artist_name(artist_raw) or artist_raw

        existing_concerts = Concert.objects.filter(
            user=request.user,
            date=d_obj
        ).prefetch_related('artists__artist')

        existing_match = None
        for ec in existing_concerts:
            art_names = {normalize_artist_name(ca.artist.name) for ca in ec.artists.all() if ca.artist}
            if ec.primary_artist:
                art_names.add(normalize_artist_name(ec.primary_artist))
            if can_artist in art_names or artist_raw.lower().strip() in {a.lower().strip() for a in art_names if a}:
                existing_match = ec
                break

        if existing_match and action != 'track':
            existing_match.delete()
            ApiCache.objects.filter(cache_key=f"user_dashboard_bundle_{request.user.id}").delete()
            return JsonResponse({
                'success': True,
                'is_tracked': False,
                'action': 'untracked',
                'message': f"Removed upcoming show for '{can_artist}' on {raw_date}."
            })

        if existing_match:
            return JsonResponse({
                'success': True,
                'is_tracked': True,
                'action': 'already_tracked',
                'concert_id': existing_match.id,
                'message': f"Already tracking '{can_artist}' on {raw_date}."
            })

        with transaction.atomic():
            venue_obj = None
            if venue_name_raw:
                venue_obj = Venue.objects.filter(name__iexact=venue_name_raw.lower()).first()
                if not venue_obj:
                    lat_val = data.get('latitude') or data.get('lat')
                    lon_val = data.get('longitude') or data.get('lon')
                    try:
                        lat_val = float(lat_val) if lat_val is not None else None
                        lon_val = float(lon_val) if lon_val is not None else None
                    except (ValueError, TypeError):
                        lat_val, lon_val = None, None

                    if lat_val is None or lon_val is None:
                        res_lat, res_lon, source_type = resolve_venue_coordinates(venue_name_raw, city, state, country)
                    else:
                        res_lat, res_lon, source_type = lat_val, lon_val, 'bandsintown'

                    venue_obj = Venue.objects.create(
                        name=venue_name_raw,
                        city=city,
                        state=state,
                        country=country,
                        latitude=res_lat,
                        longitude=res_lon,
                        geocode_source=source_type
                    )

            primary_art_obj, _ = get_or_create_artist(can_artist)

            lineup = data.get('lineup') or []
            if isinstance(lineup, str):
                lineup = [x.strip() for x in lineup.split(',') if x.strip()]

            all_lineup_names = [can_artist]
            for supp in lineup:
                supp_can = normalize_artist_name(supp) or supp
                if supp_can.lower() != can_artist.lower() and supp_can not in all_lineup_names:
                    all_lineup_names.append(supp_can)

            new_concert = Concert.objects.create(
                user=request.user,
                date=d_obj,
                raw_date=raw_date,
                year=year,
                venue=venue_obj,
                raw_venue=venue_name_raw or (venue_obj.name if venue_obj else ''),
                primary_artist=can_artist,
                raw_artists=", ".join(all_lineup_names),
                notes=f"Pre-added from upcoming shows ({data.get('event_url', '')})".strip(),
                source='setlistfm',
                is_custom_offline=False,
                is_fully_matched=False,
                is_partially_matched=False
            )

            for order, a_name in enumerate(all_lineup_names):
                a_obj, _ = get_or_create_artist(a_name)
                ConcertArtist.objects.create(
                    concert=new_concert,
                    artist=a_obj,
                    billing_order=order,
                    has_setlist=False
                )

            try:
                m_enricher = MusicianEnricher()
                for a_name in all_lineup_names:
                    a_obj, _ = get_or_create_artist(a_name)
                    if not a_obj.members.exists():
                        m_enricher.enrich_artist(a_obj.name, artist_obj=a_obj)
                    elif year >= datetime.now().year:
                        m_enricher.enrich_artist(a_obj.name, artist_obj=a_obj, refresh=True)
            except Exception:
                pass

        ApiCache.objects.filter(cache_key=f"user_dashboard_bundle_{request.user.id}").delete()

        return JsonResponse({
            'success': True,
            'is_tracked': True,
            'action': 'tracked',
            'concert_id': new_concert.id,
            'message': f"Tracking '{can_artist}' on {raw_date}! Pre-added to your database."
        })
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)


@require_http_methods(["GET"])
def api_album_tracklist(request):
    """Returns canonical studio album tracklist with track numbers and titles."""
    artist = request.GET.get('artist', '').strip()
    album = request.GET.get('album', '').strip()
    if not artist or not album:
        return JsonResponse({'status': 'error', 'message': 'artist and album parameters required'}, status=400)

    try:
        enricher = AlbumEnricher()
        tracklist_data = enricher.get_album_tracklist(artist, album)
        return JsonResponse({
            'status': 'success',
            'artist': artist,
            'album': album,
            'tracks': tracklist_data.get('tracks', []),
            'release_year': tracklist_data.get('release_year')
        })
    except Exception as e:
        return JsonResponse({'status': 'error', 'message': str(e), 'tracks': []}, status=500)








