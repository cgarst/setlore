from django.apps import AppConfig

class ConcertsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.concerts'
    verbose_name = 'Concert Tracker'

    def ready(self):
        # Reset any interrupted sync status to idle on startup so boot checks and server start immediately
        try:
            import os
            import sys
            if any(cmd in sys.argv for cmd in ['migrate', 'makemigrations', 'collectstatic', 'check', 'test']):
                return
            # Only run once in the reloader main process or non-reloader environments
            if os.environ.get('RUN_MAIN') == 'true' or 'runserver' not in sys.argv:
                from apps.core.models import UserProfile
                UserProfile.objects.filter(sync_status='syncing').update(
                    sync_status='idle',
                    sync_progress='Sync interrupted by server restart.'
                )
        except Exception:
            pass
