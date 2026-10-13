from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0003_ingestrun_backfill"),
    ]

    operations = [
        migrations.AddField(
            model_name="sitesettings",
            name="pace_sd_floor_pts",
            field=models.FloatField(default=2.0, help_text="Minimum SD, in percentage points, for pace."),
        ),
        migrations.AddField(
            model_name="sitesettings",
            name="yield_sd_floor_pts",
            field=models.FloatField(default=0.5, help_text="Minimum SD, in percentage points, for yield."),
        ),
    ]
