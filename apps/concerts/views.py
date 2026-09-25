import io
import csv
import json
from datetime import datetime
from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse, HttpResponseBadRequest
from django.views.decorators.http import require_POST
from django.db import transaction

import os
from apps.catalog.models import ApiCache, Artist, Venue
from apps.concerts.models import Concert, ConcertArtist
from src.reporter import generate_plotly_charts
from src.analytics import ConcertAnalytics
from src.csv_parser import parse_csv_rows, normalize_artist_name
from src.setlist_api import SetlistFMClient
from src.gap_analysis import reconcile_history
from src.album_enricher import AlbumEnricher
from src.venue_mapper import generate_venue_map_data
from src.musician_tracker import analyze_musicians_live
from src.config import SETLISTFM_API_KEY, USER_CACHE_DIR
from .services.sync_worker import sync_worker

@login_required
def dashboard_view(request):
    user = request.user
    profile = user.profile

    cache_entry = ApiCache.objects.filter(cache_key=f"user_dashboard_bundle_{user.id}").first()

    if cache_entry and cache_entry.payload:
        bundle = cache_entry.payload
        gap_results = bundle.get("gap_results", {})
        stats = bundle.get("stats", {})
        album_enrichments = bundle.get("album_enrichments", {})
    else:
        # Build baseline statistics directly from database
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

        # Check if user has attended setlists in disk cache to auto-reconcile without network calls
        setlist_username = profile.setlistfm_username or user.username
        user_cache_file = USER_CACHE_DIR / f"{setlist_username}_attended.json"
        user_attended = []
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
                "csv_only": csv_records,
                "csv_missing_or_partial": [],
                "setlist_only": [],
                "total_csv": len(csv_records),
                "total_setlist_user": 0,
                "matched_count": 0,
                "coverage_percentage": 0.0
            }

        analytics = ConcertAnalytics(matched, csv_records, ignored_artists=profile.ignored_artists)
        stats = analytics.compute_all_metrics()

        enricher = AlbumEnricher()
        album_enrichments, _, _ = enricher.load_cached_catalog(stats["all_songs_list"])

        stats["concerts_drilldown"] = analytics.compute_concert_drilldown(album_enrichments)
        stats["venue_map"] = generate_venue_map_data(csv_records, matched)
        stats["musicians"] = analyze_musicians_live(csv_records)

        # Cache this bundle so subsequent loads are instant
        if matched:
            ApiCache.objects.update_or_create(
                cache_key=f"user_dashboard_bundle_{user.id}",
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

    context = {
        'username': profile.setlistfm_username or user.username,
        'profile': profile,
        'gap': gap_results,
        'stats': stats,
        'artist_drilldown_json': artist_drilldown_json,
        'venue_map_json': venue_map_json,
        **charts
    }
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
            Concert.objects.filter(user=request.user).delete()
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
                    notes=""
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
            "message": f"Imported {len(csv_records)} concerts! Background sync with Setlist.fm has started."
        })
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)
