import json, logging, os

# Dispatcharr's worker only knows its own tasks, so the schedule goes through its
# generic plugin-action task, which runs the "refresh" action like a button press.
HOST_TASK = "apps.plugins.tasks.run_plugin_action"
SCHEDULE_NAME = "Lineup Manager: rebuild lineup + check members"
KEY = os.path.basename(os.path.dirname(os.path.abspath(__file__)))
log = logging.getLogger("plugins.lineup_manager")


def config_row():
    from apps.plugins.models import PluginConfig
    return (PluginConfig.objects.filter(key=KEY).first()
            or PluginConfig.objects.filter(settings__has_key="members").filter(settings__has_key="lineup_config").first())


def install_schedule(hours):
    from django_celery_beat.models import IntervalSchedule, PeriodicTask
    hours = max(1, int(hours or 2))
    every, _ = IntervalSchedule.objects.get_or_create(every=hours, period=IntervalSchedule.HOURS)
    PeriodicTask.objects.update_or_create(name=SCHEDULE_NAME, defaults={
        "task": HOST_TASK, "args": json.dumps([KEY, "refresh", {}]), "kwargs": "{}",
        "queue": "plugins", "interval": every, "enabled": True})
    return hours
