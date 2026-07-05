import openpyxl
from django.core.management.base import BaseCommand
from django.db import transaction

from psgc.models import Region, Province, CityMunicipality, Barangay


class Command(BaseCommand):
    help = "Import PSGC reference data from the PSA Publication Datafile (.xlsx)."

    def add_arguments(self, parser):
        parser.add_argument(
            '--file',
            default='psgc/data/PSGC-1Q-2026-Publication-Datafile.xlsx',
            help='Path to the PSA PSGC Publication Datafile (.xlsx).',
        )

    @transaction.atomic
    def handle(self, *args, **opts):
        path = opts['file']
        self.stdout.write(f"Loading {path} …")
        wb = openpyxl.load_workbook(path, read_only=True)
        ws = wb['PSGC']

        # Reference data — wipe and reimport cleanly (child → parent order for FKs).
        Barangay.objects.all().delete()
        CityMunicipality.objects.all().delete()
        Province.objects.all().delete()
        Region.objects.all().delete()

        cur_region = cur_province = cur_city = None
        bgy_batch = []
        n = {'reg': 0, 'prov': 0, 'city': 0, 'bgy': 0}

        def flush():
            if bgy_batch:
                Barangay.objects.bulk_create(bgy_batch, batch_size=2000)
                bgy_batch.clear()

        for r in ws.iter_rows(min_row=2, values_only=True):
            code = str(r[0]).strip() if r[0] else ''
            name = (r[1] or '').strip()
            level = r[3]
            if not code or not level:
                continue

            if level == 'Reg':
                cur_region = Region.objects.create(code=code, name=name)
                cur_province = cur_city = None
                n['reg'] += 1
            elif level == 'Prov':
                cur_province = Province.objects.create(code=code, name=name, region=cur_region)
                cur_city = None
                n['prov'] += 1
            elif level in ('City', 'Mun'):
                cur_city = CityMunicipality.objects.create(
                    code=code, name=name, province=cur_province, region=cur_region)
                n['city'] += 1
            elif level == 'SubMun':
                # Manila's districts (Tondo, Binondo, …) — structural only.
                # Their barangays roll up to the parent City (City of Manila).
                continue
            elif level == 'Bgy':
                if cur_city is None:
                    continue
                bgy_batch.append(Barangay(code=code, name=name, city=cur_city))
                n['bgy'] += 1
                if len(bgy_batch) >= 2000:
                    flush()

        flush()
        self.stdout.write(self.style.SUCCESS(
            f"Imported {n['reg']} regions, {n['prov']} provinces, "
            f"{n['city']} cities/municipalities, {n['bgy']} barangays."))
