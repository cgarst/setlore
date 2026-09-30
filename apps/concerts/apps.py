from django.apps import AppConfig

class ConcertsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.concerts'
    verbose_name = 'Concert Tracker'

    def ready(self):
        # Ensure sync worker starts and resumes any interrupted syncs on app boot/restart
        try:
            import sys
            # Avoid running worker during management commands like migrate, collectstatic, etc.
            if any(cmd in sys.argv for cmd in ['migrate', 'makemigrations', 'collectstatic', 'check', 'test']):
                return
            from .services.sync_worker import sync_worker
            sync_worker.resume_interrupted_syncs()
        except Exception:
            pass
