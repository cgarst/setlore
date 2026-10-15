from django.db import migrations

def split_peter_jones_tenure(apps, schema_editor):
    Artist = apps.get_model('catalog', 'Artist')
    MusicianTenure = apps.get_model('catalog', 'MusicianTenure')
    ApiCache = apps.get_model('catalog', 'ApiCache')

    haken = Artist.objects.filter(name__iexact='Haken').first()
    if not haken:
        return

    # Delete existing open-ended Peter Jones tenure if present
    MusicianTenure.objects.filter(
        artist=haken,
        musician_name='Peter Jones'
    ).delete()

    # Recreate the two distinct stints (2007-2008 and 2022-present)
    MusicianTenure.objects.create(
        artist=haken,
        musician_name='Peter Jones',
        musician_id='d3c75878-930a-4749-a14f-d6137b1b5d27',
        role='Keyboards',
        instrument='Keyboards',
        start_year=2007,
        end_year=2008
    )

    MusicianTenure.objects.create(
        artist=haken,
        musician_name='Peter Jones',
        musician_id='d3c75878-930a-4749-a14f-d6137b1b5d27',
        role='Keyboards',
        instrument='Keyboards',
        start_year=2022,
        end_year=None
    )

    # Invalidate cached dashboard bundles
    ApiCache.objects.filter(cache_key__startswith='user_dashboard_bundle_').delete()

def reverse_split(apps, schema_editor):
    pass

class Migration(migrations.Migration):

    dependencies = [
        ('catalog', '0008_remove_musiciantenure_musician_mbid_and_more'),
    ]

    operations = [
        migrations.RunPython(split_peter_jones_tenure, reverse_split),
    ]
