from django.db import migrations

def canonicalize_shattered_fortress(apps, schema_editor):
    Artist = apps.get_model('catalog', 'Artist')
    MusicianTenure = apps.get_model('catalog', 'MusicianTenure')
    ConcertArtist = apps.get_model('concerts', 'ConcertArtist')
    ApiCache = apps.get_model('catalog', 'ApiCache')

    # Delete dummy 404 artist if present
    dummy = Artist.objects.filter(id='b1ee2eed-ffe7-4a5b-992c-b01efecc7f6f').first()
    if dummy:
        dummy.delete()

    # Find canonical MusicBrainz artist record
    real = Artist.objects.filter(id='b75cc380-0ecf-4186-88b4-abbf2189c353').first()
    if not real:
        real = Artist.objects.filter(name__iexact='Shattered Fortress').first()

    if real:
        canonical_name = 'Mike Portnoy’s Shattered Fortress'
        real.name = canonical_name
        real.normalized_name = canonical_name.lower().strip()
        real.save()

        # Update all 7 musicians for Mike Portnoy's Shattered Fortress (2017 tour lineup)
        members = [
            ('Charlie Griffiths', 'Electric Guitar', 'Guitar', 2017, 2017),
            ('Conner Green', 'Bass', 'Bass', 2017, 2017),
            ('Diego Tejeida', 'Keyboards', 'Keyboards', 2017, 2017),
            ('Eric Gillette', 'Electric Guitar', 'Guitar', 2017, 2017),
            ('Mike Portnoy', 'Drums / Eponymous', 'Drums', 2017, 2017),
            ('Richard Henshall', 'Electric Guitar', 'Guitar', 2017, 2017),
            ('Ross Jennings', 'Lead Vocals', 'Vocals', 2017, 2017),
        ]
        for m_name, role, instr, s_yr, e_yr in members:
            MusicianTenure.objects.update_or_create(
                artist=real,
                musician_name=m_name,
                defaults={
                    'role': role,
                    'instrument': instr,
                    'start_year': s_yr,
                    'end_year': e_yr
                }
            )

    # Invalidate cached dashboard bundles
    ApiCache.objects.filter(cache_key__startswith='user_dashboard_bundle_').delete()

def reverse_canonicalize(apps, schema_editor):
    pass

class Migration(migrations.Migration):

    dependencies = [
        ('catalog', '0009_split_peter_jones_haken_tenure'),
    ]

    operations = [
        migrations.RunPython(canonicalize_shattered_fortress, reverse_canonicalize),
    ]
