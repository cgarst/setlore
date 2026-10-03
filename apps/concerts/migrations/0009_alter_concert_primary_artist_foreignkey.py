import re
from django.db import migrations, models
import django.db.models.deletion
from src.csv_parser import normalize_artist_name
from src.id_utils import generate_offline_artist_id, is_offline_id


def populate_primary_artist_foreignkey(apps, schema_editor):
    Concert = apps.get_model('concerts', 'Concert')
    Artist = apps.get_model('catalog', 'Artist')
    ConcertArtist = apps.get_model('concerts', 'ConcertArtist')

    for concert in Concert.objects.all():
        # 1. First check if primary ConcertArtist exists
        ca = ConcertArtist.objects.filter(concert=concert).order_by('billing_order').first()
        if ca and ca.artist_id:
            concert.primary_artist = ca.artist
            concert.save(update_fields=['primary_artist'])
            continue

        # 2. Match from raw_primary_artist string
        raw_art = (getattr(concert, 'raw_primary_artist', '') or getattr(concert, 'raw_artists', '') or '').strip()
        if raw_art:
            can_name = normalize_artist_name(raw_art) or raw_art
            norm = can_name.lower()
            art = (
                Artist.objects.filter(normalized_name=norm).first()
                or Artist.objects.filter(name__iexact=can_name).first()
            )
            if not art:
                art_id = generate_offline_artist_id(can_name)
                art = Artist.objects.create(
                    id=art_id,
                    name=can_name,
                    normalized_name=norm,
                    is_custom_offline=is_offline_id(art_id)
                )
            concert.primary_artist = art
            concert.save(update_fields=['primary_artist'])


class Migration(migrations.Migration):

    dependencies = [
        ('concerts', '0008_alter_concertartist_id'),
        ('catalog', '0007_remove_redundant_mbid_and_rekey'),
    ]

    operations = [
        migrations.RenameField(
            model_name='concert',
            old_name='primary_artist',
            new_name='raw_primary_artist',
        ),
        migrations.AddField(
            model_name='concert',
            name='primary_artist',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='headlined_concerts',
                to='catalog.artist',
            ),
        ),
        migrations.RunPython(populate_primary_artist_foreignkey, reverse_code=migrations.RunPython.noop),
        migrations.RemoveField(
            model_name='concert',
            name='raw_primary_artist',
        ),
    ]
