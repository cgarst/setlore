import json
from django.shortcuts import render
from django.http import JsonResponse, HttpResponseForbidden
from django.views.decorators.http import require_http_methods
from django.contrib.admin.views.decorators import staff_member_required
from src.musicbrainz_dump import MusicBrainzDumpManager

def _is_staff_or_admin(request):
    return request.user.is_authenticated and (request.user.is_staff or request.user.is_superuser or bool(request.session.get('impersonator_id')))

@staff_member_required
def musicbrainz_dump_admin_view(request):
    """Renders the MusicBrainz JSON dump management panel in the admin portal."""
    manager = MusicBrainzDumpManager.get_instance()
    status = manager.get_status()
    context = {
        'title': 'MusicBrainz JSON Dump Manager',
        'status': status,
        'upstream_version': manager.get_latest_upstream_version() or 'Unknown',
        'has_permission': True,
        'site_header': 'Setlore Administration',
        'site_title': 'Setlore Admin',
    }
    return render(request, 'admin/musicbrainz_dump.html', context)

@require_http_methods(["GET"])
def api_musicbrainz_dump_status(request):
    """API endpoint to get real-time status and download progress."""
    if not _is_staff_or_admin(request):
        return HttpResponseForbidden(json.dumps({'error': 'Admin access required'}), content_type='application/json')
    
    manager = MusicBrainzDumpManager.get_instance()
    status = manager.get_status()
    # Also fetch upstream version if not set
    if not status.get('upstream_version'):
        status['upstream_version'] = manager.get_latest_upstream_version()
    return JsonResponse(status)

@require_http_methods(["POST"])
def api_musicbrainz_dump_download(request):
    """API endpoint to initiate background download and indexing of dump files."""
    if not _is_staff_or_admin(request):
        return HttpResponseForbidden(json.dumps({'error': 'Admin access required'}), content_type='application/json')
    
    try:
        data = json.loads(request.body or '{}')
    except Exception:
        data = {}

    components = data.get('components', ['release.tar.xz', 'artist.tar.xz'])
    manager = MusicBrainzDumpManager.get_instance()
    success, message = manager.download_and_build(components=components, background=True)
    
    return JsonResponse({
        'status': 'success' if success else 'error',
        'message': message,
        'dump_status': manager.get_status()
    })

@require_http_methods(["POST"])
def api_musicbrainz_dump_cancel(request):
    """API endpoint to cancel an ongoing download/indexing job."""
    if not _is_staff_or_admin(request):
        return HttpResponseForbidden(json.dumps({'error': 'Admin access required'}), content_type='application/json')
    
    manager = MusicBrainzDumpManager.get_instance()
    manager.cancel_task()
    return JsonResponse({
        'status': 'success',
        'message': 'Task cancelled.',
        'dump_status': manager.get_status()
    })

@require_http_methods(["POST"])
def api_musicbrainz_dump_delete(request):
    """API endpoint to delete the local MusicBrainz dump from disk."""
    if not _is_staff_or_admin(request):
        return HttpResponseForbidden(json.dumps({'error': 'Admin access required'}), content_type='application/json')
    
    manager = MusicBrainzDumpManager.get_instance()
    manager.delete_dump()
    return JsonResponse({
        'status': 'success',
        'message': 'Local MusicBrainz dump deleted from disk. Live API mode restored.',
        'dump_status': manager.get_status()
    })

@require_http_methods(["POST"])
def api_musicbrainz_dump_test_lookup(request):
    """API endpoint to test studio album lookup against the local dump vs live API."""
    if not _is_staff_or_admin(request):
        return HttpResponseForbidden(json.dumps({'error': 'Admin access required'}), content_type='application/json')
    
    try:
        data = json.loads(request.body or '{}')
    except Exception:
        data = {}

    artist = data.get('artist', '').strip()
    song = data.get('song', '').strip()

    if not artist or not song:
        return JsonResponse({'status': 'error', 'message': 'Artist and song name are required.'}, status=400)

    manager = MusicBrainzDumpManager.get_instance()
    is_available = manager.is_dump_available()
    online_fallback = manager.get_online_fallback()
    
    local_album, local_year = manager.lookup_studio_album(artist, song)
    live_result = None
    if not local_album and not local_year and online_fallback:
        try:
            from src.album_enricher import AlbumEnricher
            enricher = AlbumEnricher()
            live_album, live_year = enricher._query_musicbrainz_studio_album(artist, song)
            if live_album or live_year:
                live_result = {
                    'album': live_album,
                    'release_year': live_year,
                    'source': 'live_api'
                }
        except Exception:
            pass

    return JsonResponse({
        'status': 'success',
        'artist': artist,
        'song': song,
        'local_dump_available': is_available,
        'online_fallback_enabled': online_fallback,
        'local_result': {
            'album': local_album,
            'release_year': local_year,
            'source': 'local_disk' if (local_album or local_year) else None
        },
        'live_result': live_result
    })

@require_http_methods(["POST"])
def api_musicbrainz_dump_set_mode(request):
    """API endpoint to toggle MusicBrainz dump mode (auto vs off)."""
    if not _is_staff_or_admin(request):
        return HttpResponseForbidden(json.dumps({'error': 'Admin access required'}), content_type='application/json')
    
    try:
        data = json.loads(request.body or '{}')
    except Exception:
        data = {}

    mode = data.get('mode', 'auto').lower().strip()
    if mode not in ('auto', 'off'):
        mode = 'auto'

    manager = MusicBrainzDumpManager.get_instance()
    manager.set_mode(mode)
    
    return JsonResponse({
        'status': 'success',
        'mode': mode,
        'dump_status': manager.get_status()
    })

@require_http_methods(["POST"])
def api_musicbrainz_dump_set_online_fallback(request):
    """API endpoint to toggle MusicBrainz live API fallback for unmatched tracks."""
    if not _is_staff_or_admin(request):
        return HttpResponseForbidden(json.dumps({'error': 'Admin access required'}), content_type='application/json')
    
    try:
        data = json.loads(request.body or '{}')
    except Exception:
        data = {}

    enabled = bool(data.get('enabled', True))
    manager = MusicBrainzDumpManager.get_instance()
    manager.set_online_fallback(enabled)
    
    return JsonResponse({
        'status': 'success',
        'online_fallback': enabled,
        'dump_status': manager.get_status()
    })

@require_http_methods(["POST"])
def api_musicbrainz_dump_set_schedule(request):
    """API endpoint to set scheduled dump update interval and auto cleanup settings."""
    if not _is_staff_or_admin(request):
        return HttpResponseForbidden(json.dumps({'error': 'Admin access required'}), content_type='application/json')
    
    try:
        data = json.loads(request.body or '{}')
    except Exception:
        data = {}

    interval = data.get('interval', 'off').lower().strip()
    auto_delete_raw = bool(data.get('auto_delete_raw', False))

    manager = MusicBrainzDumpManager.get_instance()
    manager.set_schedule(interval, auto_delete_raw=auto_delete_raw)

    return JsonResponse({
        'status': 'success',
        'schedule': interval,
        'auto_delete_raw': auto_delete_raw,
        'dump_status': manager.get_status()
    })

@require_http_methods(["POST"])
def api_musicbrainz_dump_delete_raw(request):
    """API endpoint to delete raw .tar.xz and .part archives while keeping the indexed SQLite database."""
    if not _is_staff_or_admin(request):
        return HttpResponseForbidden(json.dumps({'error': 'Admin access required'}), content_type='application/json')
    
    manager = MusicBrainzDumpManager.get_instance()
    success, message = manager.delete_raw_archives()
    
    return JsonResponse({
        'status': 'success' if success else 'error',
        'message': message,
        'dump_status': manager.get_status()
    })

@require_http_methods(["POST"])
def api_refresh_musician_lineup(request):
    """Admin API endpoint to trigger MusicBrainz live musician lineup refresh."""
    if not _is_staff_or_admin(request):
        return HttpResponseForbidden(json.dumps({'error': 'Admin access required'}), content_type='application/json')

    try:
        data = json.loads(request.body or '{}')
    except Exception:
        data = {}

    artist_name = data.get('artist', '').strip()
    refresh_all = bool(data.get('all', False))

    from src.musician_enricher import MusicianEnricher
    from apps.catalog.models import Artist, MusicianTenure, ApiCache
    from src.csv_parser import normalize_artist_name

    enricher = MusicianEnricher()

    if refresh_all:
        artists = list(Artist.objects.all().order_by('name'))
        total_created = 0
        total_updated = 0
        processed = 0
        for art in artists:
            enricher.enrich_artist(art.name, artist_obj=art, refresh=True)
            created, updated = getattr(enricher, 'last_sync_stats', (0, 0))
            total_created += created
            total_updated += updated
            processed += 1

        ApiCache.objects.filter(endpoint='dashboard_bundle').delete()
        return JsonResponse({
            'status': 'success',
            'mode': 'all',
            'artists_processed': processed,
            'tenures_created': total_created,
            'tenures_updated': total_updated,
            'message': f"Refreshed all {processed} artists from MusicBrainz ({total_created} new, {total_updated} updated)."
        })

    if not artist_name:
        return JsonResponse({'status': 'error', 'message': 'Artist name is required.'}, status=400)

    canon = normalize_artist_name(artist_name) or artist_name
    art_obj, _ = Artist.objects.get_or_create(
        name=canon,
        defaults={'normalized_name': canon.lower()}
    )

    tenures = enricher.enrich_artist(canon, artist_obj=art_obj, refresh=True)
    created, updated = getattr(enricher, 'last_sync_stats', (0, 0))
    total_db_tenures = MusicianTenure.objects.filter(artist=art_obj).count()

    ApiCache.objects.filter(endpoint='dashboard_bundle').delete()

    return JsonResponse({
        'status': 'success',
        'mode': 'single',
        'artist': art_obj.name,
        'relations_found': len(tenures),
        'tenures_created': created,
        'tenures_updated': updated,
        'total_db_tenures': total_db_tenures,
        'members': tenures,
        'message': f"Refreshed '{art_obj.name}': {len(tenures)} relations ({created} added, {updated} updated, {total_db_tenures} in DB)."
    })

@require_http_methods(["POST"])
def api_purge_dashboard_cache(request):
    """Admin API endpoint to purge all cached dashboard bundles and analytics."""
    if not _is_staff_or_admin(request):
        return HttpResponseForbidden(json.dumps({'error': 'Admin access required'}), content_type='application/json')

    from apps.catalog.models import ApiCache
    deleted, _ = ApiCache.objects.filter(endpoint='dashboard_bundle').delete()
    return JsonResponse({
        'status': 'success',
        'deleted_count': deleted,
        'message': f"Purged {deleted} cached dashboard bundles."
    })

@require_http_methods(["POST"])
def api_clear_music_catalog(request):
    """Admin API endpoint to clear MusicBrainz catalog tables with customizable options."""
    if not _is_staff_or_admin(request):
        return HttpResponseForbidden(json.dumps({'error': 'Admin access required'}), content_type='application/json')

    import io
    from django.core.management import call_command

    try:
        data = json.loads(request.body or '{}')
    except Exception:
        data = {}

    clear_cache = bool(data.get('clear_cache', False))
    resync = bool(data.get('resync', False))

    out = io.StringIO()
    try:
        call_command('clear_music_catalog', clear_cache=clear_cache, resync=resync, stdout=out)
        output_text = out.getvalue()
        return JsonResponse({
            'status': 'success',
            'output': output_text,
            'message': 'Music catalog tables successfully cleared.'
        })
    except Exception as e:
        return JsonResponse({
            'status': 'error',
            'output': out.getvalue(),
            'message': f'Failed to clear catalog: {str(e)}'
        }, status=500)
