import logging, os
from celery import shared_task

TASK = "lineup_manager.scheduled_refresh"
log = logging.getLogger("plugins.lineup_manager")


def config_row():
    from apps.plugins.models import PluginConfig
    key = os.path.basename(os.path.dirname(os.path.abspath(__file__)))
    return (PluginConfig.objects.filter(key=key).first()
            or PluginConfig.objects.filter(settings__has_key="members").filter(settings__has_key="lineup_config").first())


@shared_task(name=TASK)
def scheduled_refresh():
    from . import plugin
    row = config_row()
    if not row or not row.enabled:
        return {"status": "ok", "message": "plugin disabled, nothing done"}
    return plugin.Plugin().refresh(row.settings or {}, log)


def install_schedule(hours):
    from django_celery_beat.models import IntervalSchedule, PeriodicTask
    hours = max(1, int(hours or 2))
    every, _ = IntervalSchedule.objects.get_or_create(every=hours, period=IntervalSchedule.HOURS)
    PeriodicTask.objects.update_or_create(name="Lineup Manager: rebuild lineup + check members",
                                          defaults={"task": TASK, "interval": every, "enabled": True})
    return hours
