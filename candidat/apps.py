from django.apps import AppConfig


class CandidatConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "candidat"

    def ready(self):
        import candidat.signals_scripts