from django.db import migrations, models

def deduplicate_concert_artists(apps, schema_editor):
    Concert = apps.get_model('concerts', 'Concert')
    ConcertArtist = apps.get_model('concerts', 'ConcertArtist')
    ConcertSong = apps.get_model('concerts', 'ConcertSong')

    # 1. Deduplicate ConcertArtist rows per concert
    for concert in Concert.objects.all():
        seen_artists = {}
        for ca in concert.artists.order_by('billing_order', 'id'):
            art_id = ca.artist_id
            if art_id not in seen_artists:
                seen_artists[art_id] = ca
            else:
                primary_ca = seen_artists[art_id]
                # Re-parent songs to primary_ca
                ConcertSong.objects.filter(concert_artist=ca).update(concert_artist=primary_ca)

                # Merge metadata
                if ca.has_setlist:
                    primary_ca.has_setlist = True
                if ca.is_favorite:
                    primary_ca.is_favorite = True
                if ca.setlist_url and not primary_ca.setlist_url:
                    primary_ca.setlist_url = ca.setlist_url
                if ca.setlistfm_id and not primary_ca.setlistfm_id:
                    primary_ca.setlistfm_id = ca.setlistfm_id
                primary_ca.save()

                # Delete duplicate ConcertArtist row
                ca.delete()

        # 2. Clean up raw_artists comma-separated strings
        if concert.raw_artists:
            tokens = [t.strip() for t in concert.raw_artists.split(',') if t.strip()]
            seen_tokens = set()
            deduped = []
            for t in tokens:
                if t.lower() not in seen_tokens:
                    seen_tokens.add(t.lower())
                    deduped.append(t)
            new_raw = ', '.join(deduped)
            if new_raw != concert.raw_artists:
                concert.raw_artists = new_raw
                concert.save(update_fields=['raw_artists'])

class Migration(migrations.Migration):

    dependencies = [
        ('concerts', '0006_fix_next_to_none_sept_7_show'),
    ]

    operations = [
        migrations.RunPython(deduplicate_concert_artists, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name='concertartist',
            constraint=models.UniqueConstraint(fields=['concert', 'artist'], name='unique_concert_artist'),
        ),
    ]
