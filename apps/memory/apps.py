from django.apps import AppConfig


class MemoryConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.memory"
    verbose_name = "Memory"

    def ready(self) -> None:
        import apps.memory.signals  # noqa: F401