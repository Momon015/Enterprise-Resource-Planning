from django.apps import AppConfig


class EmployeeConfig(AppConfig):
    name = 'Employee'

    def ready(self):
        from Employee import signals
        signals.register()
