import io
import csv
import json
import re
from datetime import datetime
from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse, HttpResponseBadRequest
from django.views.decorators.http import require_POST
from django.db import transaction

import os
from django.contrib.auth.models import User
from apps.catalog.models import ApiCache, Artist, Venue, Song
from apps.concerts.models import Concert, ConcertArtist, ConcertSong
from apps.concerts.utils import parse_setlist_text, resolve_venue_coordinates, build_manual_setlist_for_concert_artist
from src.reporter import generate_plotly_charts
from src.analytics import ConcertAnalytics
from src.csv_parser import parse_csv_rows, normalize_artist_name
from src.setlist_api import SetlistFMClient
from src.gap_analysis import reconcile_history
from src.album_enricher import AlbumEnricher
from src.musician_enricher import MusicianEnricher
from src.venue_mapper import generate_venue_map_data
from src.musician_tracker import analyze_musicians_live
from src.config import SETLISTFM_API_KEY, USER_CACHE_DIR
from .services.sync_worker import sync_worker

def get_dashboard_context(request, target_user, tab_name='overview', is_public_view=False):
    alias_map = {
        '': 'overview',
        'overview': 'overview',
        'concerts': 'concerts',
        'drilldown': 'drilldown',
        'artists': 'drilldown',
        'musicians': 'musicians',
        'map': 'map',
        'venues': 'map',
        'advanced': 'advanced',
        'albums': 'advanced',
        'setlists': 'setlists',
        'freshness': 'setlists',
        'gap': 'gap',
        'audit': 'gap',
    }
    initial_tab = alias_map.get(str(tab_name).lower().strip('/'), 'overview')
    profile = target_user.profile

    cache_entry = ApiCache.objects.filter(cache_key=f"user_dashboard_bundle_{target_user.id}").first()

    if cache_entry and cache_entry.payload:
        bundle = cache_entry.payload
        gap_results = bundle.get("gap_results", {})
        stats = bundle.get("stats", {})
        album_enrichments = bundle.get("album_enrichments", {})
    else:
        # Build baseline statistics directly from database
        db_concerts = Concert.objects.filter(user=target_user).select_related('venue').prefetch_related('artists__artist', 'artists__songs')
        csv_records = []
        manual_matched_pairs = []

        for c in db_concerts:
            artist_names = [ca.artist.name for ca in c.artists.all()]
            dt = datetime.combine(c.date, datetime.min.time()) if c.date else None
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
                "primary_artist": c.primary_artist,
                "venue": c.raw_venue or (c.venue.name if c.venue else ""),
                "seen_before": c.seen_before,
                "is_custom_offline": c.is_custom_offline,
                "source": c.source
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

        # Merge manual matched pairs
        if manual_matched_pairs:
            matched.extend(manual_matched_pairs)
            gap_results["matched"] = matched
            for mp in manual_matched_pairs:
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

    artist_drilldown_json = json.dumps(drilldown)
    venue_map_json = json.dumps(stats.get("venue_map", {}))

    is_owner = request.user.is_authenticated and (request.user.id == target_user.id)
    tab_url_base = f"/u/{target_user.username}" if is_public_view else ""
    share_url = request.build_absolute_uri(f"/u/{target_user.username}/")

    return {
        'initial_tab': initial_tab,
        'profile_user': target_user,
        'username': profile.setlistfm_username or target_user.username,
        'profile': profile,
        'gap': gap_results,
        'stats': stats,
        'artist_drilldown_json': artist_drilldown_json,
        'venue_map_json': venue_map_json,
        'all_venues': list(Venue.objects.order_by('name').values_list('name', flat=True).distinct()),
        'all_artists': list(Artist.objects.order_by('name').values_list('name', flat=True).distinct()),
        'is_public_view': is_public_view,
        'is_owner': is_owner,
        'is_profile_private': not profile.is_public,
        'share_url': share_url,
        'tab_url_base': tab_url_base,
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

    if not target_user.profile.is_public and not is_owner and not is_staff:
        return render(request, 'public_profile_message.html', {
            'title': 'Private Profile',
            'message_type': 'private',
            'target_user': target_user,
        }, status=403)

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
                    seen_before=rec.get("seen_before", ""),
                    notes="",
                    source='csv',
                    is_custom_offline=False
                )

                for idx, art_name in enumerate(rec.get("artists", [])):
                    can_art = normalize_artist_name(art_name) or art_name
                    art_obj, _ = Artist.objects.get_or_create(name=can_art, defaults={'normalized_name': can_art.lower()})
                    ConcertArtist.objects.create(concert=concert, artist=art_obj, billing_order=idx)

        # Clear any stale dashboard bundle for this user
        ApiCache.objects.filter(cache_key=f"user_dashboard_bundle_{request.user.id}").delete()

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
            primary_art_obj, _ = Artist.objects.get_or_create(
                name=can_primary,
                defaults={'normalized_name': can_primary.lower()}
            )

            # Supporting artists
            artists_list = [can_primary]
            supporting_objs = []
            if supporting_artists_raw:
                parts = re.split(r'[,;/]+', supporting_artists_raw)
                for p in parts:
                    p_clean = p.strip()
                    if p_clean:
                        can_supp = normalize_artist_name(p_clean) or p_clean
                        supp_obj, _ = Artist.objects.get_or_create(
                            name=can_supp,
                            defaults={'normalized_name': can_supp.lower()}
                        )
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
                total_tracks = len(parsed_tracks)

                for idx, t in enumerate(parsed_tracks):
                    track_num = idx + 1
                    pct = round((track_num / total_tracks) * 100) if total_tracks > 0 else 100

                    if track_num == 1:
                        slot = "Opener"
                        slot_category = "opener"
                    elif t["is_encore"]:
                        if track_num == total_tracks:
                            slot = "Show Closer"
                        else:
                            slot = f"Encore {t['encore_number']}" if t['encore_number'] else "Encore"
                        slot_category = "encore"
                    elif track_num == total_tracks:
                        slot = "Show Closer"
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

                    clean_title_key = t["title"].lower().strip()
                    song_obj, _ = Song.objects.get_or_create(
                        artist=primary_art_obj,
                        clean_title=clean_title_key,
                        defaults={
                            'title': t["title"],
                            'is_cover': t["is_cover"],
                            'original_artist': t["original_artist"] or None
                        }
                    )

                    ConcertSong.objects.create(
                        concert_artist=ca_primary,
                        song=song_obj,
                        raw_song_name=t["title"],
                        set_name=t["set_name"],
                        is_encore=t["is_encore"],
                        encore_number=t["encore_number"],
                        track_num=track_num,
                        total_tracks=total_tracks,
                        pct_position=pct,
                        slot=slot,
                        slot_category=slot_category,
                        is_cover=t["is_cover"],
                        original_artist=t["original_artist"] or '',
                        info=t["info"] or ''
                    )
                    new_songs_to_enrich.append((primary_art_obj.name, t["title"]))

        # Optional quick album and musician enrichment for new artists/songs
        if new_songs_to_enrich:
            try:
                enricher = AlbumEnricher()
                enricher.load_cached_catalog([{"artist": a, "song": s} for a, s in new_songs_to_enrich])
            except Exception:
                pass

        try:
            m_enricher = MusicianEnricher()
            if not primary_art_obj.members.exists():
                m_enricher.enrich_artist(primary_art_obj.name, artist_obj=primary_art_obj)
        except Exception:
            pass

        # Clear dashboard bundle cache for this user so changes reflect immediately
        ApiCache.objects.filter(cache_key=f"user_dashboard_bundle_{request.user.id}").delete()

        song_count_msg = f" with {len(new_songs_to_enrich)} songs" if new_songs_to_enrich else ""
        return JsonResponse({
            "status": "success",
            "message": f"Concert for '{can_primary}' on {raw_date} added successfully{song_count_msg}!",
            "concert_id": concert.id
        })
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
                primary_art_obj, _ = Artist.objects.get_or_create(
                    name=can_primary,
                    defaults={'normalized_name': can_primary.lower()}
                )

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
        if q:
            user_venue_ids = Concert.objects.filter(user=user, venue__isnull=False).values_list('venue_id', flat=True).distinct()
            user_venues = list(Venue.objects.filter(id__in=user_venue_ids, name__icontains=q).values('name', 'city', 'state')[:10])
            for v in user_venues:
                v['is_recent'] = True
            
            seen_names = {v['name'].lower() for v in user_venues}
            other_venues = list(Venue.objects.filter(name__icontains=q).exclude(name__in=[v['name'] for v in user_venues]).values('name', 'city', 'state')[:15])
            for v in other_venues:
                v['is_recent'] = False
                
            results = user_venues + other_venues
        else:
            recent_user_concerts = Concert.objects.filter(user=user, venue__isnull=False).select_related('venue').order_by('-date', '-id')[:60]
            seen = set()
            for c in recent_user_concerts:
                if c.venue and c.venue.name and c.venue.name.lower() not in seen:
                    seen.add(c.venue.name.lower())
                    results.append({
                        'name': c.venue.name,
                        'city': c.venue.city or '',
                        'state': c.venue.state or '',
                        'is_recent': True
                    })
                if len(results) >= 12:
                    break
            if not results:
                venues = Venue.objects.all().order_by('name').values('name', 'city', 'state')[:12]
                results = list(venues)

    elif field_type == 'artist':
        if q:
            user_artists = list(ConcertArtist.objects.filter(concert__user=user, artist__name__icontains=q).values_list('artist__name', flat=True).distinct()[:10])
            user_artist_results = [{'name': name, 'is_recent': True} for name in user_artists]
            seen_artists = {name.lower() for name in user_artists}
            
            other_artists = list(Artist.objects.filter(name__icontains=q).exclude(name__in=user_artists).values_list('name', flat=True)[:15])
            other_artist_results = [{'name': name, 'is_recent': False} for name in other_artists if name.lower() not in seen_artists]
            
            results = user_artist_results + other_artist_results
        else:
            recent_artists = ConcertArtist.objects.filter(concert__user=user).select_related('artist').order_by('-concert__date', '-concert__id')[:60]
            seen = set()
            for ca in recent_artists:
                if ca.artist and ca.artist.name and ca.artist.name.lower() not in seen:
                    seen.add(ca.artist.name.lower())
                    results.append({'name': ca.artist.name, 'is_recent': True})
                if len(results) >= 12:
                    break
            if not results:
                artists = Artist.objects.all().order_by('name').values_list('name', flat=True)[:12]
                results = [{'name': a} for a in artists]

    elif field_type == 'city':
        if q:
            cities = Venue.objects.filter(city__icontains=q).exclude(city='').values_list('city', flat=True).distinct().order_by('city')[:15]
            results = [{'name': c} for c in cities]
        else:
            recent_user_concerts = Concert.objects.filter(user=user, venue__isnull=False).select_related('venue').order_by('-date', '-id')[:60]
            seen = set()
            for c in recent_user_concerts:
                if c.venue and c.venue.city and c.venue.city.lower() not in seen:
                    seen.add(c.venue.city.lower())
                    results.append({'name': c.venue.city, 'is_recent': True})
                if len(results) >= 10:
                    break
            if not results:
                cities = Venue.objects.exclude(city='').values_list('city', flat=True).distinct().order_by('city')[:10]
                results = [{'name': c} for c in cities]

    elif field_type == 'state':
        if q:
            states = Venue.objects.filter(state__icontains=q).exclude(state='').values_list('state', flat=True).distinct().order_by('state')[:15]
            results = [{'name': s} for s in states]
        else:
            recent_user_concerts = Concert.objects.filter(user=user, venue__isnull=False).select_related('venue').order_by('-date', '-id')[:60]
            seen = set()
            for c in recent_user_concerts:
                if c.venue and c.venue.state and c.venue.state.lower() not in seen:
                    seen.add(c.venue.state.lower())
                    results.append({'name': c.venue.state, 'is_recent': True})
                if len(results) >= 10:
                    break
            if not results:
                states = Venue.objects.exclude(state='').values_list('state', flat=True).distinct().order_by('state')[:10]
                results = [{'name': s} for s in states]

    return JsonResponse({'results': results})



