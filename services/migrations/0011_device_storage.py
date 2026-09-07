"""Macht das Becken wieder nullbar — für Geräte „auf Halde".

Der Wächter in ``0008_device_tank_required`` bleibt unangetastet: er erzwang
beim Umstieg von ``tank_label`` eine bewusste Zuordnung. Dass das Becken später
wieder leer sein darf, widerspricht dem nicht — ``tank IS NULL`` heißt jetzt
„nicht im Einsatz". ``on_delete`` wechselt dabei von ``CASCADE`` auf
``SET_NULL``: ein gelöschtes Becken lagert seine Geräte ein, statt sie
mitzunehmen.
"""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('services', '0010_device_cover_image'),
        ('tanks', '0009_detection_limits'),
    ]

    operations = [
        migrations.AddField(
            model_name='device',
            name='stored_since',
            field=models.DateField(blank=True, help_text='Gesetzt, solange das Gerät nicht im Einsatz ist.', null=True, verbose_name='Eingelagert seit'),
        ),
        migrations.AlterField(
            model_name='device',
            name='tank',
            field=models.ForeignKey(blank=True, help_text='Leer lassen, wenn das Gerät gerade nicht im Einsatz ist.', null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='devices', to='tanks.tank', verbose_name='Becken'),
        ),
    ]
