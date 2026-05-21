from django.apps import AppConfig


class SupplierConfig(AppConfig):
    name = 'Supplier'

    def ready(self):
        import Supplier.signals