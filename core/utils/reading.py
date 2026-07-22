"""THE X/Z reading formula — the single source of truth for a BIR End-of-Day reading.

★★ X and Z MUST compute from THIS one function. An X reading that disagrees with the
Z that follows it is exactly what a BIR examiner looks for, so there is deliberately no
second code path: the mid-shift X page, the end-of-day reading, and (later) the sealed
ZReading snapshot all call `compute_reading()` and render the same dict.

Field list follows RMO 24-2023 Annex D-2. The annex's *arithmetic* is a broken mock
(see project_z_reading_spec memory) — we copy its STRUCTURE and LABELS only and derive
every number from the regulations and our own data.

Design decisions baked in here (documented, because someone will question them):

  • GROSS basis. `Present`/`Previous`/`Sales for the Day` come off the append-only
    grand-total odometer (AccumulatedGrandSalesEntry), which posts `Sale.subtotal`
    (revenue + discount + vat_adjustment) at completion — i.e. gross before discount.
    The reading then deducts discount/void/return/vat-adjustment down to Net.

  • The deduction ladder RECONCILES exactly:
        Gross (odometer sales-for-day, incl. sales later voided)
          − Less Discount        (Σ discount_amount, ACTIVE sales only)
          − Less VAT Adjustment   (Σ vat_adjustment,  ACTIVE sales only)
          − Less Void             (Σ subtotal of sales VOIDED that were rung that day)
          − Less Return           (Σ refund_total that day)
          = Net Amount            (== Σ total_revenue of ACTIVE sales − returns)
    Voids are deducted at GROSS (subtotal) to match the basis they were POSTED to the
    void odometer channel; the discount/vat lines therefore exclude voided sales so the
    ladder still lands on active net revenue. This resolves the annex's ambiguous
    `Less Void` line (flagged as an open question in the z-reading spec) with the one
    self-consistent model.

  • VAT block is gated on `business.is_vat_registered`, reusing the same switch the
    official invoice uses. NON-VAT is our common case — for those sellers there is no
    VAT breakdown and no VAT-adjustment line (vat_adjustment is always 0 for them).

The window here is a single BUSINESS DAY (`Sale.date`), which is what the owner-facing
reading page asks for. A future sealed Z that windows by timestamp (Z-to-Z) can pass its
own pre-filtered queryset via `sales=` without changing any of the math below.
"""
from __future__ import annotations

from decimal import Decimal


CENTS = Decimal('0.01')
ZERO = Decimal('0.00')


def _q(value):
    return (value or ZERO).quantize(CENTS)


def _odometer_reading(business, channel, *, before_date=None, on_or_before_date=None):
    """The odometer value for one channel as of a day boundary.

    running_total is monotonic per channel, so the reading as of a date is simply the
    largest running_total among entries dated on/before that boundary. Uses Max rather
    than "the last row" so it is robust to any created_at/business_date ordering quirk.
    """
    from django.db.models import Max
    from activity.models import AccumulatedGrandSalesEntry

    qs = AccumulatedGrandSalesEntry.objects.filter(business=business, channel=channel)
    if on_or_before_date is not None:
        qs = qs.filter(business_date__lte=on_or_before_date)
    if before_date is not None:
        qs = qs.filter(business_date__lt=before_date)
    return qs.aggregate(m=Max('running_total'))['m'] or ZERO


def compute_reading(business, day, *, sales=None):
    """Compute every Annex D-2 field for `business` on business-day `day`.

    Returns a plain dict of Decimals/strings ready for the template. Pure and
    side-effect-free — seals nothing, burns no counter. `sales` may be passed to
    override the day-window queryset (used by a timestamp-windowed Z later); it must
    already be scoped to completed sales for this business.
    """
    from django.db.models import Sum, Min, Max
    from Sales.models import Sale, SalesReturn
    from activity.models import AccumulatedGrandSalesEntry as AG

    # ── Sales in the window (completed; INCLUDES voided — the reading reports gross
    #    then deducts voids) ──────────────────────────────────────────────────────
    if sales is None:
        sales = Sale.objects.filter(business=business, status='completed', date=day)
    completed = sales.select_related('business').prefetch_related('sale_items')
    active = [s for s in completed if not s.is_void]
    voided = [s for s in completed if s.is_void]

    # ── Accumulated grand total — the PERPETUAL odometer (Present/Previous) ──────
    # These read the append-only grand-total counter, which is only populated by live
    # checkout. Sales rung before the odometer was wired (or imported/seeded directly)
    # legitimately read 0 here — that is the odometer's own history, not this day's.
    present = _odometer_reading(business, AG.CHANNEL_SALE, on_or_before_date=day)
    previous = _odometer_reading(business, AG.CHANNEL_SALE, before_date=day)
    odometer_sales_for_day = present - previous

    # ── The DAY's own figures come off the Sale rows (always accurate) ──────────
    # Deliberately NOT the odometer delta: the odometer is a cache that can lag behind
    # historical data, but the Sale rows are the truth for what was actually sold today.
    # For live data `gross` == `odometer_sales_for_day`; they diverge only for the
    # pre-odometer rows above, and confining that gap to the Present/Previous lines keeps
    # the deduction ladder and the VAT breakdown from ever contradicting each other.
    gross = sum((s.subtotal for s in completed), ZERO)   # incl. voided — deducted below

    less_discount = sum((s.discount_amount or ZERO for s in active), ZERO)
    less_vat_adj = sum((s.vat_adjustment or ZERO for s in active), ZERO)
    less_void = sum((s.subtotal for s in voided), ZERO)

    returns_qs = SalesReturn.objects.filter(business=business, date=day)
    less_return = returns_qs.aggregate(t=Sum('refund_total'))['t'] or ZERO

    net_amount = gross - less_discount - less_vat_adj - less_void - less_return

    # ── Discount summary by statutory type (ACTIVE sales) ───────────────────────
    disc_labels = dict(Sale.DISCOUNT_TYPE_CHOICES)
    discount_summary = {t: ZERO for t in disc_labels}
    discount_other = ZERO
    for s in active:
        amt = s.discount_amount or ZERO
        if not amt:
            continue
        if s.discount_type in discount_summary:
            discount_summary[s.discount_type] += amt
        else:
            discount_other += amt
    discount_lines = [
        {'label': disc_labels[t], 'amount': _q(discount_summary[t])}
        for t in disc_labels
    ]
    discount_lines.append({'label': 'Other', 'amount': _q(discount_other)})

    # ── VAT breakdown (VAT-registered sellers only) ─────────────────────────────
    is_vat = bool(getattr(business, 'is_vat_registered', False))
    vat_block = None
    if is_vat:
        vatable = vat = exempt = zero = ZERO
        for s in active:
            v = s.vat_summary()
            vatable += v['vatable']
            vat += v['vat']
            exempt += v['exempt']
            zero += v['zero']
        vat_block = {
            'vatable': _q(vatable), 'vat': _q(vat),
            'exempt': _q(exempt), 'zero': _q(zero),
        }

    # ── Document-number ranges (that day) ───────────────────────────────────────
    si_range = completed.aggregate(lo=Min('reference'), hi=Max('reference'))
    void_range = (completed.filter(is_void=True)
                  .aggregate(lo=Min('void_reference'), hi=Max('void_reference')))
    ret_range = returns_qs.aggregate(lo=Min('reference'), hi=Max('reference'))

    # Reset counter is stamped identically on every sale of a books period; take any.
    reset_counter = (completed.exclude(books_reset_counter__isnull=True)
                     .aggregate(m=Max('books_reset_counter'))['m'])

    # ── Tender / cash position (best-effort; blank when no drawer is used) ───────
    tender = _tender_section(business, day)

    return {
        'business': business,
        'day': day,
        'is_vat_registered': is_vat,

        # accumulated odometer
        'present_accumulated': _q(present),
        'previous_accumulated': _q(previous),
        'sales_for_day': _q(odometer_sales_for_day),

        # deduction ladder (gross off the Sale rows — see note above)
        'gross': _q(gross),
        'less_discount': _q(less_discount),
        'less_vat_adjustment': _q(less_vat_adj),
        'less_void': _q(less_void),
        'less_return': _q(less_return),
        'net_amount': _q(net_amount),

        # counts
        'transaction_count': len(active),
        'void_count': len(voided),
        'return_count': returns_qs.count(),

        # document ranges
        'si_beg': si_range['lo'] or '—', 'si_end': si_range['hi'] or '—',
        'void_beg': void_range['lo'] or '—', 'void_end': void_range['hi'] or '—',
        'return_beg': ret_range['lo'] or '—', 'return_end': ret_range['hi'] or '—',
        'reset_counter': reset_counter if reset_counter is not None else 0,

        'discount_lines': discount_lines,
        'vat_block': vat_block,
        'tender': tender,
    }


def _tender_section(business, day):
    """Payments received per method + drawer cash position for the day.

    Payments-received come off SalesPayment (authoritative, always present). Opening
    fund, withdrawals and SHORT/OVER come off the day's drawers (ShiftEmployee) and are
    simply zero for businesses that don't run cash reconciliation.
    """
    from django.db.models import Sum
    from Sales.models import SalesPayment

    method_labels = dict(SalesPayment.PAYMENT_METHOD_CHOICES)
    rows = (SalesPayment.objects
            .filter(sale__business=business, sale__status='completed',
                    sale__is_void=False, date=day)
            .values('method')
            .annotate(total=Sum('amount')))
    by_method = {r['method']: _q(r['total']) for r in rows}
    payments = [
        {'label': method_labels.get(m, m.title()), 'amount': by_method[m]}
        for m in method_labels if m in by_method
    ]
    payments_received = sum((r['amount'] for r in payments), ZERO)

    opening_fund = withdrawals = short_over = ZERO
    has_drawer = False
    try:
        from Employee.models import ShiftEmployee, CashPayout
        drawers = ShiftEmployee.objects.filter(
            shift__business=business, clock_in__date=day)
        has_drawer = drawers.exists()
        if has_drawer:
            opening_fund = drawers.aggregate(t=Sum('opening_cash'))['t'] or ZERO
            withdrawals = (CashPayout.objects
                           .filter(shift__shift__business=business, created_at__date=day)
                           .aggregate(t=Sum('amount'))['t'] or ZERO)
            for d in drawers:
                v = d.cash_variance          # None until the drawer is counted at close
                if v is not None:
                    short_over += v
    except Exception:
        # Drawer models are optional to this reading — never let them break the Z.
        has_drawer = False

    return {
        'payments': payments,
        'payments_received': _q(payments_received),
        'opening_fund': _q(opening_fund),
        'withdrawals': _q(withdrawals),
        'short_over': _q(short_over),
        'has_drawer': has_drawer,
    }
