# -*- coding: utf-8 -*-
# Generated by Django 1.11.10 on 2018-10-30 21:40
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('confreg', '0033_speaker_reminders'),
    ]

    operations = [
        migrations.AddField(
            model_name='conferenceseries',
            name='visible',
            field=models.BooleanField(default=True),
        ),
    ]
