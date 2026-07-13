import json
from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth.decorators import login_required
from django.db.models import Count, DecimalField, ExpressionWrapper, F, Min, Sum, Value
from django.db.models.functions import ExtractHour, ExtractIsoWeekDay, TruncMonth, TruncWeek
from django.shortcuts import render

from Employee.models import Shift
from Expense.models import (Expense, ExpenseItem, Purchase, PurchasePayment,
                            PurchaseReturn, Waste)
from Product.models import Product
from Sales.models import Sale, SaleItem, SalesPayment, SalesReturn, SalesReturnItem

from core.utils.metrics import pct_delta
from core.utils.owner import get_business_for_user, permission_required
from subscription.decorators import feature_required

from .periods import RANGE_CHOICES, fmt_day, resolve_period

# Wide enough that a peso column can't overflow mid-aggregate.
MONEY = DecimalField(max_digits=18, decimal_places=6)

# A line's share of the money that actually came in.
#
# Sale.total_revenue is NET of the whole-order discount, but a SaleItem only stores
# the undiscounted price_at_sale. Multiplying the line back down by the same
# percentage is exact (sales discounts are whole-order % only — see the discount
# design), and it's what makes the Top Products column SUM to the Revenue KPI above
# it. A gross line total would quietly exceed the headline number on any discounted
# day, and a table that doesn't add up is a table nobody trusts.
NET_LINE = ExpressionWrapper(
    F('price_at_sale') * F('quantity')
    * (Value(Decimal('100')) - F('sale__discount_percent')) / Value(Decimal('100')),
    output_field=MONEY,
)

DOW_LABELS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']

TOP_N = 8


def _hour_label(h):
    """0 -> '12a', 9 -> '9a', 12 -> '12p', 21 -> '9p'."""
    suffix = 'a' if h < 12 else 'p'
    hour12 = h % 12 or 12
    return f"{hour12}{suffix}"


# A returned line, priced at what was actually refunded for it.
REFUND_LINE = ExpressionWrapper(
    F('unit_refund') * F('quantity'), output_field=MONEY,
)


def _sales_in(business, start, end):
    """active() = posted sales only — no voids, no unconfirmed drafts."""
    return Sale.objects.active().filter(business=business, date__gte=start, date__lte=end)


def _settlement(sales, returns):
    """Billed → collected → still owed, for the Revenue card's dropdown.

    Deliberately NO payment-method breakdown: that lives on the Daily Summary, and
    repeating it here would be noise on a page about trends.

    Receivables nets off only the CREDIT half of a refund — a cash refund already moved
    money and doesn't change what the customer owes. Same definition Sale.outstanding and
    the Daily Summary use, so the three agree.
    """
    collected = SalesPayment.objects.filter(sale__in=sales).aggregate(
        t=Sum('amount'))['t'] or Decimal('0')
    credit    = returns.aggregate(t=Sum('refund_credit'))['t'] or Decimal('0')
    billed    = sales.aggregate(t=Sum('total_revenue'))['t'] or Decimal('0')
    return {
        'collected':   collected,
        'receivables': billed - collected - credit,
    }


def _returns_in(business, start, end):
    """Customer refunds in this window, dated by the RETURN's own date.

    A July refund against a June sale belongs to JULY — the same rule the Dashboard and
    the Daily Summary use. Analytics was the last page still reporting revenue GROSS of
    refunds, which meant it disagreed with both of them for any window containing one.
    """
    return SalesReturn.objects.filter(business=business, date__gte=start, date__lte=end)


def _headline(sales, returns):
    agg = sales.aggregate(revenue=Sum('total_revenue'), count=Count('id'))
    gross   = agg['revenue'] or Decimal('0')
    count   = agg['count'] or 0
    refunds = returns.aggregate(t=Sum('refund_total'))['t'] or Decimal('0')

    sold     = SaleItem.objects.filter(sale__in=sales).aggregate(u=Sum('quantity'))['u'] or 0
    returned = SalesReturnItem.objects.filter(
        sales_return__in=returns).aggregate(u=Sum('quantity'))['u'] or 0

    revenue = gross - refunds

    # count stays the number of SALES — a return is not a sale, and inflating or deflating
    # the transaction count with refunds would make "average sale" meaningless. Average is
    # therefore net revenue over the sales that actually happened.
    return {
        'gross':   gross,
        'refunds': refunds,
        'revenue': revenue,
        'count':   count,
        'units':   sold - returned,
        'avg':     (revenue / count) if count else Decimal('0'),
    }


def _next_month(d):
    return date(d.year + 1, 1, 1) if d.month == 12 else date(d.year, d.month + 1, 1)


def _axis(period):
    """The bucket start-dates across the window, in order, with their labels.

    ONE definition of the x-axis, shared by every chart on every Analytics page. The
    keys line up with what TruncWeek / TruncMonth return, so a series can be looked up
    against them directly. Two pages inventing their own axis is how a Monday-start
    quietly becomes a Sunday-start on one chart and not the other.

    The bucket widens with the window (day -> week -> month) so an all-time view of a
    two-year-old business draws ~24 points instead of ~730.
    """
    if period.bucket == 'day':
        cursor = period.start
        advance, label_of = lambda d: d + timedelta(days=1), fmt_day

    elif period.bucket == 'week':
        # Start at the Monday of the first week so the keys line up with TruncWeek.
        cursor = period.start - timedelta(days=period.start.weekday())
        advance, label_of = lambda d: d + timedelta(days=7), fmt_day

    else:  # month
        cursor = period.start.replace(day=1)
        advance, label_of = _next_month, lambda d: f"{d.strftime('%b')} {d.year}"

    keys, labels = [], []
    while cursor <= period.end:
        keys.append(cursor)
        labels.append(label_of(cursor))
        cursor = advance(cursor)
    return keys, labels


def _bucketed(qs, date_field, value_field, period):
    """{bucket_start: float} for any dated money column — one grouped query."""
    if period.bucket == 'day':
        rows = qs.values(date_field).annotate(v=Sum(value_field))
        return {r[date_field]: float(r['v'] or 0) for r in rows}

    trunc = TruncWeek if period.bucket == 'week' else TruncMonth
    rows = qs.annotate(b=trunc(date_field)).values('b').annotate(v=Sum(value_field))
    return {r['b']: float(r['v'] or 0) for r in rows}


def _series(qs, date_field, value_field, period, keys):
    """A chart-ready list aligned to _axis(), zero-filled.

    Zero-filling matters: a shop that sold nothing on Sunday should show a floor on
    Sunday, not a straight line hopping over it as if the day didn't exist.
    """
    by_bucket = _bucketed(qs, date_field, value_field, period)
    return [round(by_bucket.get(k, 0.0), 2) for k in keys]


def _trend(sales, returns, period):
    """Revenue per bucket, NET of refunds, zero-filled.

    A refund lands on the bucket it was issued in, so a big return shows up as a dip on
    the day it happened — which is where the money actually left.
    """
    keys, labels = _axis(period)
    sold     = _series(sales,   'date', 'total_revenue', period, keys)
    refunded = _series(returns, 'date', 'refund_total',  period, keys)
    return labels, [round(s - r, 2) for s, r in zip(sold, refunded)]


def _top_products(sales, returns):
    """Ranked twice — owners think in money, but a cheap fast-mover is the thing you
    run out of. Grouped by product_id (not the name snapshot) so renaming a product
    doesn't split it into two rows.

    ★ NET of returns, per product. This column has to SUM to the headline Revenue, and the
    headline is now net — so if the refunds weren't backed out here too, the table would
    quietly exceed the number printed above it and stop being trustable. (Same reason
    NET_LINE exists at all: a table that doesn't add up is a table nobody believes.)

    A product returned in this window but sold in an earlier one can go NEGATIVE here.
    That's correct — money left the till for it during this period — and it sorts to the
    bottom where it belongs.
    """
    sold = {
        r['product_id']: r
        for r in SaleItem.objects
        .filter(sale__in=sales)
        .values('product_id', 'product__name', 'product__slug')
        .annotate(units=Sum('quantity'), revenue=Sum(NET_LINE))
    }

    refunded = (
        SalesReturnItem.objects
        .filter(sales_return__in=returns, original_sale_item__isnull=False)
        .values('original_sale_item__product_id',
                'original_sale_item__product__name',
                'original_sale_item__product__slug')
        .annotate(units=Sum('quantity'), refund=Sum(REFUND_LINE))
    )

    for r in refunded:
        pid = r['original_sale_item__product_id']
        row = sold.get(pid)
        if row is None:
            # Returned this period, sold in an earlier one — it still belongs on the board.
            row = sold[pid] = {
                'product_id':    pid,
                'product__name': r['original_sale_item__product__name'],
                'product__slug': r['original_sale_item__product__slug'],
                'units':   0,
                'revenue': Decimal('0'),
            }
        row['units']   -= r['units']
        row['revenue'] -= r['refund']

    rows = list(sold.values())
    return (
        sorted(rows, key=lambda r: r['revenue'], reverse=True)[:TOP_N],
        sorted(rows, key=lambda r: r['units'],   reverse=True)[:TOP_N],
    )


def _unsold(business, sales):
    """Stockable goods with no line in this period — capital sitting still.

    goods only: services have nothing on a shelf, so nagging about an unbooked
    service is noise. Ranked by the money tied up (cost x qty on hand), which is the
    number that decides whether it's worth acting on.
    """
    sold_ids = SaleItem.objects.filter(sale__in=sales).values_list('product_id', flat=True)
    qs = (
        Product.goods
        .filter(business=business)
        .exclude(id__in=sold_ids)
        .annotate(idle=ExpressionWrapper(F('cost_price') * F('prepared_quantity'), output_field=MONEY))
        .order_by('-idle', 'name')
    )
    return {
        'rows':  list(qs[:TOP_N]),
        'count': qs.count(),
        'value': qs.aggregate(v=Sum('idle'))['v'] or Decimal('0'),
    }


def _peaks(sales):
    """When the money comes in — by hour of day and by weekday."""
    # Hour comes off created_at (when it was rung up). Django converts to Asia/Manila
    # because USE_TZ is on. A back-dated sale therefore lands on the hour it was
    # ENCODED, not the hour it happened — true for the POS, wrong for catch-up entry,
    # which is why the card says "when sales were recorded" out loud.
    hour_rows = (
        sales.annotate(h=ExtractHour('created_at'))
        .values('h').annotate(rev=Sum('total_revenue'), n=Count('id'))
    )
    by_hour = {r['h']: float(r['rev'] or 0) for r in hour_rows}
    count_by_hour = {r['h']: r['n'] for r in hour_rows}
    hour_data = [round(by_hour.get(h, 0.0), 2) for h in range(24)]

    # Weekday comes off `date`, which has no timezone to get wrong.
    dow_rows = (
        sales.annotate(w=ExtractIsoWeekDay('date'))
        .values('w').annotate(rev=Sum('total_revenue'), n=Count('id'))
    )
    by_dow = {r['w']: float(r['rev'] or 0) for r in dow_rows}   # 1 = Mon .. 7 = Sun
    dow_data = [round(by_dow.get(w, 0.0), 2) for w in range(1, 8)]

    peak_hour = max(range(24), key=lambda h: hour_data[h]) if any(hour_data) else None
    peak_dow  = max(range(7), key=lambda i: dow_data[i]) if any(dow_data) else None

    return {
        'hour_labels': [_hour_label(h) for h in range(24)],
        'hour_data':   hour_data,
        'dow_labels':  DOW_LABELS,
        'dow_data':    dow_data,
        'peak_hour':      _hour_label(peak_hour) if peak_hour is not None else None,
        'peak_hour_sales': count_by_hour.get(peak_hour, 0) if peak_hour is not None else 0,
        'peak_dow':       DOW_LABELS[peak_dow] if peak_dow is not None else None,
    }


@login_required(login_url='login')
@permission_required('staff_view')          # owner-only — analytics is not a staff surface
@feature_required('has_analytics')          # Pro-only — the one hard gate
def sales_analytics(request, business_slug):
    business = get_business_for_user(request.user, business_slug)

    # 'All time' starts at the first sale ever posted, not at the business's creation
    # date — a shop that registered in January and started selling in March should not
    # be shown two flat months it never traded in.
    first_sale = (
        Sale.objects.active().filter(business=business).aggregate(d=Min('date'))['d']
    )
    period = resolve_period(request, earliest=first_sale)

    sales   = _sales_in(business, period.start, period.end)
    returns = _returns_in(business, period.start, period.end)
    now     = _headline(sales, returns)

    # All time has nothing before it, so it draws no deltas — and we don't run the
    # previous-window queries at all rather than aggregating an empty range to prove it.
    delta = None
    if period.compares:
        then = _headline(
            _sales_in(business, period.prev_start, period.prev_end),
            _returns_in(business, period.prev_start, period.prev_end),
        )
        delta = {
            'revenue': pct_delta(now['revenue'], then['revenue']),
            'count':   pct_delta(now['count'],   then['count']),
            'avg':     pct_delta(now['avg'],     then['avg']),
            'units':   pct_delta(now['units'],   then['units']),
        }

    trend_labels, trend_data = _trend(sales, returns, period)
    top_by_revenue, top_by_units = _top_products(sales, returns)

    # Deliberately NOT cached. The dashboard's KPI cache is keyed on business+day
    # because it always asks the same question; here the question changes with every
    # ?range= / ?start= / ?end= combination, so a cache would mostly miss — and on
    # LocMem it couldn't be busted coherently anyway (see the LocMem gotcha). These
    # are indexed date-range aggregates; revisit if a real dataset says otherwise.
    context = {
        'section': 'sales-analytics',
        'business': business,
        'period': period,
        'range_choices': RANGE_CHOICES,

        'kpi': now,
        'delta': delta,          # None on All time — the template drops the rows entirely

        'trend_labels': json.dumps(trend_labels),
        'trend_data':   json.dumps(trend_data),

        # Rendered as two panels behind one pair of tabs — pairs kept in a list so the
        # template loops once instead of duplicating the whole table markup.
        # Revenue card dropdown: billed → collected → receivables, plus the refund line.
        'settle': _settlement(sales, returns),

        'top_tabs': [('revenue', top_by_revenue), ('units', top_by_units)],
        'unsold': _unsold(business, sales),

        'peaks': _peaks(sales),
        'has_data': now['count'] > 0,
    }
    # The charts read these as JSON — dumped here rather than in the template so the
    # template never has to reason about escaping.
    context['hour_labels'] = json.dumps(context['peaks']['hour_labels'])
    context['hour_data']   = json.dumps(context['peaks']['hour_data'])
    context['dow_labels']  = json.dumps(context['peaks']['dow_labels'])
    context['dow_data']    = json.dumps(context['peaks']['dow_data'])

    return render(request, 'analytics/sales_analytics.html', context)


# ══════════════════════════════════════════════════════════════════════════════
# EXPENSE ANALYTICS — where the money goes
# ══════════════════════════════════════════════════════════════════════════════

# The four ways money leaves the business. Declared ONCE, in one order, and used by the
# KPI strip, the donut and the stacked trend — so a stream cannot appear in one and go
# missing from another.
#
# ★ This is exactly the set the Dashboard subtracts from revenue to get net profit
#   (see Dashboard/views.py: revenue - material - salary - waste - expense). The two
#   MUST agree: if a fifth outflow is ever added, add it here and to the dashboard's
#   net_profit in the same commit, or this page and the dashboard start reporting
#   different totals for the same window.
#
# key -> (label, date field, money field, CSS colour token, icon)
STREAMS = [
    ('stock',  'Stock Purchases',   'purchase_date', 'total_cost',                  '--violet', 'bi-box-seam'),
    ('salary', 'Payroll',           'date',         'shift_employees__daily_rate', '--info',   'bi-people'),
    # Label is "Business expenses" — the same words the Dashboard, Accrual and Cash Flow
    # pages already use for this exact number. ("Overhead" was jargon; one name, everywhere.)
    # The KEY stays 'bills' — it's internal, and renaming it would touch a lot for nothing.
    ('bills',  'Business Expenses', 'date',         'total_amount',                '--orange', 'bi-receipt'),
    ('waste',  'Waste',            'date',          'total_cost',                  '--danger', 'bi-trash3'),
]

WASTE_REASONS = dict(Waste.REASON_CHOICES)


def _stream_querysets(business, start, end):
    """The four money-out streams, each already clipped to the window.

    Purchase.total_cost is ALREADY net of the whole-order supplier discount (verified:
    a ₱74 order at 20% stores 59.20), so unlike SaleItem on the Sales page there is
    nothing to back out — summing it straight is the true spend.

    Purchase returns are NOT deducted. That is deliberate: the Dashboard and the Daily
    ★ Supplier RETURNS are netted off the stock stream (2026-07-13) — see _purchase_returns_in.
    """
    return {
        'stock':  Purchase.objects.active().filter(
                      business=business, purchase_date__gte=start, purchase_date__lte=end),
        'salary': Shift.objects.filter(business=business, date__gte=start, date__lte=end),
        'bills':  Expense.objects.filter(business=business, date__gte=start, date__lte=end),
        'waste':  Waste.objects.filter(business=business, date__gte=start, date__lte=end),
    }


def _purchase_returns_in(business, start, end):
    """Stock sent back to the supplier in this window, dated by the RETURN's own date.

    This page used to NOT deduct these — deliberately, because at the time neither the
    Dashboard nor the Daily Summary did, and a page that quietly used a different
    definition would have disagreed with both. All three now net returns off, so this one
    follows: the spend figures here match the Dashboard's for the same window.
    """
    return PurchaseReturn.objects.filter(business=business, date__gte=start, date__lte=end)


def _spend(business, start, end):
    """Each stream's total, plus opex and the grand total, as Decimals.

    `stock` is NET of supplier refunds. It can legitimately go NEGATIVE in a narrow window
    — return ₱180 of goods in a week you bought ₱55 and the supplier handed you money back
    on balance. That is real and must NOT be clamped to zero; clamping would leak the
    refund out of the books.
    """
    qs = _stream_querysets(business, start, end)
    out = {}
    for key, _label, _date_f, money_f, _tok, _icon in STREAMS:
        out[key] = qs[key].aggregate(t=Sum(money_f))['t'] or Decimal('0')

    out['gross_stock']      = out['stock']
    out['stock_returns']    = _purchase_returns_in(business, start, end).aggregate(
                                  t=Sum('refund_total'))['t'] or Decimal('0')
    out['stock']            = out['gross_stock'] - out['stock_returns']

    out['opex']  = out['salary'] + out['bills']       # the Dashboard's "operating expenses"
    out['total'] = sum(out[k] for k, *_ in STREAMS)
    return out


def _stock_settlement(business, start, end):
    """Billed → paid → still owed, for the Stock purchases dropdown. Mirror of
    _settlement on the sales side, same no-payment-methods rule."""
    purchases = Purchase.objects.active().filter(
        business=business, purchase_date__gte=start, purchase_date__lte=end)
    paid   = PurchasePayment.objects.filter(purchase__in=purchases).aggregate(
        t=Sum('amount'))['t'] or Decimal('0')
    credit = _purchase_returns_in(business, start, end).aggregate(
        t=Sum('refund_credit'))['t'] or Decimal('0')
    billed = purchases.aggregate(t=Sum('total_cost'))['t'] or Decimal('0')
    return {
        'paid':     paid,
        'payables': billed - paid - credit,
    }


def _earliest_spend(business):
    """First day money ever went out — where 'All time' starts on this page.

    Each Analytics page has its own first record; the Sales page starts at the first
    SALE, and starting this one there too would draw empty months for a shop that
    bought stock in March and only started selling in May.
    """
    firsts = [
        Purchase.objects.active().filter(business=business).aggregate(d=Min('purchase_date'))['d'],
        Shift.objects.filter(business=business).aggregate(d=Min('date'))['d'],
        Expense.objects.filter(business=business).aggregate(d=Min('date'))['d'],
        Waste.objects.filter(business=business).aggregate(d=Min('date'))['d'],
    ]
    firsts = [d for d in firsts if d]
    return min(firsts) if firsts else None


def _composition(spend):
    """The donut: one row per stream, biggest first, with its share of total spend.

    A donut with no numbers on it is decoration, so every slice is also a legend row
    carrying its peso amount and percentage.
    """
    total = spend['total']
    rows = []
    for key, label, _date_f, _money_f, token, icon in STREAMS:
        amount = spend[key]
        rows.append({
            'key': key, 'label': label, 'token': token, 'icon': icon,
            'amount': amount,
            'share': float(amount / total * 100) if total else 0.0,
        })
    rows.sort(key=lambda r: r['amount'], reverse=True)
    return rows


def _stacked_trend(business, period):
    """Spend per bucket, split by stream — stacked so the SHAPE of the spending shows,
    not just its height. A flat total can hide waste quietly replacing stock.

    The stock band is NET of supplier refunds, matching the KPI card above it — otherwise
    the chart and the number it belongs to would tell different stories. A bucket where
    the refunds outweigh the buying dips below the axis, which is exactly what happened.
    """
    keys, labels = _axis(period)
    qs = _stream_querysets(business, period.start, period.end)

    refunds = _series(
        _purchase_returns_in(business, period.start, period.end),
        'date', 'refund_total', period, keys,
    )

    datasets = []
    for key, label, date_f, money_f, token, _icon in STREAMS:
        data = _series(qs[key], date_f, money_f, period, keys)
        if key == 'stock':
            data = [round(d - r, 2) for d, r in zip(data, refunds)]
        datasets.append({
            'key':   key,
            'label': label,
            'token': token,
            'data':  data,
        })
    return labels, datasets


def _top_categories(business, start, end, bills_total):
    """Where the overhead goes — rent, electricity, wifi…

    ExpenseItem.amount sums EXACTLY to Expense.total_amount (verified on real data), so
    this table reconciles with the Bills figure above it with no correction — unlike the
    Sales page, where the line items needed NET_LINE to add up to the headline.

    The % denominator is ALL bills in the window, not just the top N, so the shares
    stay honest instead of always summing to 100.
    """
    rows = list(
        ExpenseItem.objects
        .filter(expense__business=business,
                expense__date__gte=start, expense__date__lte=end)
        .values('category')
        .annotate(total=Sum('amount'), n=Count('id'))
        .order_by('-total')[:TOP_N]
    )
    for r in rows:
        r['label'] = r['category'] or 'Uncategorized'
        r['share'] = float(r['total'] / bills_total * 100) if bills_total else 0.0
    return rows


def _waste_by_reason(business, start, end, waste_total):
    """Waste is the only cost on this page an owner can just decide to stop paying, and
    the reason is what says whether that's possible: spoilage is a buying/rotation
    problem, theft is a people problem, 'service use' isn't really a loss at all."""
    rows = list(
        Waste.objects
        .filter(business=business, date__gte=start, date__lte=end)
        .values('reason')
        .annotate(total=Sum('total_cost'), n=Count('id'))
        .order_by('-total')
    )
    for r in rows:
        r['label'] = WASTE_REASONS.get(r['reason'], r['reason'] or 'Other')
        r['share'] = float(r['total'] / waste_total * 100) if waste_total else 0.0
    return rows


@login_required(login_url='login')
@permission_required('staff_view')          # owner-only — analytics is not a staff surface
@feature_required('has_analytics')          # Pro-only — the same hard gate as Sales Analytics
def expense_analytics(request, business_slug):
    business = get_business_for_user(request.user, business_slug)

    period = resolve_period(request, earliest=_earliest_spend(business))

    now = _spend(business, period.start, period.end)

    # Same rule as the Sales page: All time has nothing before it, so it draws no deltas
    # and the previous-window queries never run.
    #
    # ★ Every delta on this page renders with .kpi-delta--inverse — on a COST card,
    #   spending more is the red direction. Without it the page would congratulate an
    #   owner in green for burning more money.
    delta = None
    if period.compares:
        then = _spend(business, period.prev_start, period.prev_end)
        delta = {
            'total': pct_delta(now['total'], then['total']),
            'stock': pct_delta(now['stock'], then['stock']),
            'opex':  pct_delta(now['opex'],  then['opex']),
            'waste': pct_delta(now['waste'], then['waste']),
        }

    trend_labels, trend_datasets = _stacked_trend(business, period)
    composition = _composition(now)

    context = {
        'section': 'expense-analytics',
        'business': business,
        'period': period,
        'range_choices': RANGE_CHOICES,

        'spend': now,
        'delta': delta,

        'composition': composition,
        # The donut and the stacked bars are driven from the same rows, so a slice and
        # its band can never disagree about a colour or a number.
        #
        # ★ Chart data is floored at 0, the LEGEND is not. Net stock can go negative when a
        #   window's refunds outweigh its buying, and a doughnut cannot draw a negative arc
        #   — Chart.js would silently render it as a positive wedge, which is a lie. So the
        #   ring simply omits it (no money went out on balance) while the legend row still
        #   reports the true −₱ figure.
        'donut_labels': json.dumps([r['label'] for r in composition]),
        'donut_data':   json.dumps([max(0.0, float(r['amount'])) for r in composition]),
        'donut_tokens': json.dumps([r['token'] for r in composition]),

        'trend_labels':   json.dumps(trend_labels),
        'trend_datasets': json.dumps(trend_datasets),

        # Stock purchases dropdown: billed → paid → payables, plus the refund line.
        'settle': _stock_settlement(business, period.start, period.end),

        'categories': _top_categories(business, period.start, period.end, now['bills']),
        'waste_rows': _waste_by_reason(business, period.start, period.end, now['waste']),

        'biggest': composition[0] if now['total'] else None,
        'has_data': now['total'] > 0,
    }
    return render(request, 'analytics/expense_analytics.html', context)
