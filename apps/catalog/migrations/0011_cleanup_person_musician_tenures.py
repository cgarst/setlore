from django.db import migrations
import os
from pathlib import Path

def cleanup_person_musician_tenures(apps, schema_editor):
    Artist = apps.get_model('catalog', 'Artist')
    MusicianTenure = apps.get_model('catalog', 'MusicianTenure')
    ApiCache = apps.get_model('catalog', 'ApiCache')

    try:
        from src.musician_enricher import MusicianEnricher
        enricher = MusicianEnricher()
    except Exception:
        enricher = None

    if enricher:
        # 1. Clean up and re-sync tenures for all Person artists from relations cache/dump
        for a in Artist.objects.all():
            if not a.id or a.is_custom_offline:
                continue
            mb_data = enricher.get_artist_relations(a.id)
            if mb_data and mb_data.get('type') == 'Person':
                parsed = enricher.parse_member_tenures(mb_data)
                MusicianTenure.objects.filter(artist=a).delete()
                for inc in parsed:
                    MusicianTenure.objects.create(
                        artist=a,
                        musician_name=inc['musician'],
                        musician_id=inc.get('musician_mbid'),
                        role=inc['role'],
                        instrument=inc['instrument'],
                        start_year=inc['start'],
                        end_year=inc['end']
                    )

    # 2. Delete any remaining corrupted tenures where musician_name is a group artist in DB
    group_names = set(
        Artist.objects.filter(
            members__isnull=False
        ).exclude(
            members__musician_name=models_F('name') if hasattr(apps, 'models_F') else None
        ).values_list('name', flat=True)
    )
    # Explicit known band names that should never be musician_names
    known_bands = [
        'Dream Theater', 'Megadeth', 'Animals as Leaders', 'Deep Purple',
        'Porcupine Tree', 'The Beatles', 'Guns N’ Roses', 'Chickenfoot',
        'Return to Forever', 'G3', 'Liquid Tension Experiment', 'Explorers Club'
    ]
    MusicianTenure.objects.filter(musician_name__in=known_bands).delete()

    # 3. Invalidate cached dashboard bundles
    ApiCache.objects.filter(cache_key__startswith='user_dashboard_bundle_').delete()

    # 4. Remove stale demo cache file if present
    demo_cache_path = Path('data/cache/demo_user_cache.json')
    if demo_cache_path.exists():
        try:
            demo_cache_path.unlink()
        except Exception:
            pass

def reverse_cleanup(apps, schema_editor):
    pass

class Migration(migrations.Migration):

    dependencies = [
        ('catalog', '0010_canonicalize_shattered_fortress'),
    ]

    operations = [
        migrations.RunPython(cleanup_person_musician_tenures, reverse_cleanup),
    ]
