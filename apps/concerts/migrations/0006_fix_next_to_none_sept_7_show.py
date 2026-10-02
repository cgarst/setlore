from django.db import migrations

def fix_sept_7_show(apps, schema_editor):
    Concert = apps.get_model('concerts', 'Concert')
    ConcertArtist = apps.get_model('concerts', 'ConcertArtist')
    ConcertSong = apps.get_model('concerts', 'ConcertSong')

    # Find Sept 7 2017 show
    concerts = Concert.objects.filter(date__year=2017, date__month=9, date__day=7, raw_artists__icontains='Next to None')
    for c in concerts:
        c.primary_artist = 'Between the Buried and Me'
        c.raw_artists = 'Between the Buried and Me, Vanden Plas, Twilight Force, Next to None'
        c.save()

        ca_bbtam = ConcertArtist.objects.filter(concert=c, artist__name__icontains='Between the Buried and Me').first()
        ca_vp = ConcertArtist.objects.filter(concert=c, artist__name__icontains='Vanden Plas').first()
        ca_tf = ConcertArtist.objects.filter(concert=c, artist__name__icontains='Twilight Force').first()
        ca_ntn = ConcertArtist.objects.filter(concert=c, artist__name__icontains='Next to None').first()

        if ca_bbtam:
            ca_bbtam.billing_order = 0
            ca_bbtam.save()
        if ca_vp:
            ca_vp.billing_order = 1
            ca_vp.save()
        if ca_tf:
            ca_tf.billing_order = 2
            ca_tf.save()
        if ca_ntn:
            ca_ntn.billing_order = 3
            ca_ntn.has_setlist = False
            ConcertSong.objects.filter(concert_artist=ca_ntn).delete()
            ca_ntn.save()

def reverse_fix(apps, schema_editor):
    pass

class Migration(migrations.Migration):

    dependencies = [
        ('concerts', '0005_concertartist_is_favorite'),
    ]

    operations = [
        migrations.RunPython(fix_sept_7_show, reverse_fix),
    ]
