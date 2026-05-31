import csv
import io
import zipfile
from datetime import datetime


def _build_datasets(business):
    """Returns [(sheet_name, headers, rows), ...] for one business."""
    from Product.models import Product
    from Supplier.models import Material, Supplier
    from Sales.models import Sale
    from Expense.models import Purchase, Waste, Expense, Employee

    def d(val):
        return val.strftime('%Y-%m-%d') if val else ''

    datasets = []

    products = Product.objects.filter(business=business, is_active=True).select_related('category')
    datasets.append((
        'Products',
        ['Name', 'Category', 'Cost', 'Price', 'Quantity', 'Unit', 'Locked', 'Created'],
        [[p.name, getattr(p.category, 'name', ''), p.cost_price, p.selling_price,
          p.prepared_quantity, p.unit or '', 'Yes' if p.is_locked else 'No', d(p.created_at)]
         for p in products],
    ))

    materials = Material.objects.filter(business=business).select_related('supplier', 'category')
    datasets.append((
        'Materials',
        ['Name', 'Supplier', 'Category', 'Price', 'Quantity', 'Unit', 'Locked', 'Created'],
        [[m.name, getattr(m.supplier, 'name', ''), getattr(m.category, 'name', ''),
          m.price, m.quantity, m.unit, 'Yes' if m.is_locked else 'No', d(m.created_at)]
         for m in materials],
    ))

    suppliers = Supplier.objects.filter(business=business)
    datasets.append((
        'Suppliers',
        ['Name', 'Locked', 'Created'],
        [[s.name, 'Yes' if s.is_locked else 'No', d(s.created_at)] for s in suppliers],
    ))

    employees = Employee.objects.filter(business=business)
    datasets.append((
        'Employees',
        ['Name', 'Daily Rate', 'Created'],
        [[e.name, e.daily_rate, d(e.created_at)] for e in employees],
    ))

    sales = Sale.objects.filter(business=business).order_by('-date').select_related('created_by')
    datasets.append((
        'Sales',
        ['Date', 'Reference', 'Total Revenue', 'Line Count', 'Recorded By'],
        [[d(s.date), s.reference or '', s.total_revenue or 0, s.line_count,
          getattr(s.created_by, 'username', '')] for s in sales],
    ))

    purchases = Purchase.objects.filter(business=business).order_by('-purchase_date').select_related('created_by')
    datasets.append((
        'Purchases',
        ['Date', 'Reference', 'Total Cost', 'Paid', 'Recorded By'],
        [[d(p.purchase_date), p.reference or '', p.total_cost or 0,
          'Yes' if p.is_paid else 'No', getattr(p.created_by, 'username', '')] for p in purchases],
    ))

    wastes = Waste.objects.filter(business=business).order_by('-date').select_related('created_by')
    datasets.append((
        'Waste',
        ['Date', 'Total Cost', 'Recorded By'],
        [[d(w.date), w.total_cost, getattr(w.created_by, 'username', '')] for w in wastes],
    ))

    expenses = Expense.objects.filter(business=business).order_by('-date').select_related('created_by')
    datasets.append((
        'Expenses',
        ['Date', 'Total Amount', 'Recorded By'],
        [[d(e.date), e.total_amount, getattr(e.created_by, 'username', '')] for e in expenses],
    ))

    return datasets


def export_csv_zip(business):
    """Zip of CSVs — one per data type. Returns (filename, bytes)."""
    datasets = _build_datasets(business)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
        for sheet_name, headers, rows in datasets:
            csv_buf = io.StringIO()
            writer = csv.writer(csv_buf)
            writer.writerow(headers)
            writer.writerows(rows)
            zf.writestr(f"{sheet_name.lower()}.csv", csv_buf.getvalue())
    buffer.seek(0)
    stamp = datetime.now().strftime('%Y%m%d')
    return f"{business.slug}-export-{stamp}.zip", buffer.getvalue()


def export_excel(business):
    """Single .xlsx workbook, one sheet per data type. Returns (filename, bytes)."""
    from openpyxl import Workbook
    datasets = _build_datasets(business)
    wb = Workbook()
    wb.remove(wb.active)
    for sheet_name, headers, rows in datasets:
        ws = wb.create_sheet(title=sheet_name[:31])  # Excel caps sheet names at 31 chars
        ws.append(headers)
        for row in rows:
            ws.append(row)
    buffer = io.BytesIO()
    wb.save(buffer)
    buffer.seek(0)
    stamp = datetime.now().strftime('%Y%m%d')
    return f"{business.slug}-export-{stamp}.xlsx", buffer.getvalue()
