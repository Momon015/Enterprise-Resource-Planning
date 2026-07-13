"""Returns, and the profit formula they feed.

Added 2026-07-12. Before this, NEITHER kind of return was subtracted anywhere — the
Dashboard, the Daily Summary, the DailyClose snapshot and Expense Analytics all computed
profit as if a refund never happened. There are two, and they are mirror images:

    PurchaseReturn  — we send goods back to the SUPPLIER   -> reduces COST
    SalesReturn     — a customer brings goods back to US   -> reduces REVENUE

★ They must be fixed TOGETHER. Subtracting only purchase returns lowers cost while
  revenue stays inflated by every customer refund, so net profit comes out HIGHER than
  reality — a half-fix that reads like an improvement. That is why the formula below is
  a single function: it is impossible to apply one side and forget the other.

Everything here lives in core/ so the Dashboard (one day), the Daily Summary (per day),
the freeze snapshot and Analytics (any window) share ONE definition. Models are imported
inside the functions to keep core/ free of app-level import cycles — same pattern as
core/utils/kpis.py.
"""

from decimal import Decimal

from django.db.models import Sum

ZERO = Decimal('0')


def _total(qs, field='refund_total'):
    return qs.aggregate(t=Sum(field))['t'] or ZERO


def sales_returns_total(business, start, end):
    """Money refunded to customers in this window.

    Dated by the RETURN's own `date`, not the original sale's. That's deliberate and it
    matches the append-only design: the past is sealed, so a July refund against a June
    sale lands in JULY. Back-dating it into June would silently rewrite a closed month.

    Both refund methods count. A store-credit refund is still revenue you no longer
    earned — the customer simply holds the money as credit instead of cash. (Store credit
    is currently PAUSED on the sales side, but historic rows exist, so it is not assumed
    away here.)
    """
    from Sales.models import SalesReturn
    return _total(
        SalesReturn.objects.filter(business=business, date__gte=start, date__lte=end)
    )


def purchase_returns_total(business, start, end):
    """Money the supplier gave back in this window — cash refunds AND credit notes.

    Both methods count for the ACCRUAL lens: either way you no longer paid for that
    stock. (They differ only for CASH FLOW, where a credit note never touches the
    drawer — that lens is a separate calculation and is not what this feeds.)
    """
    from Expense.models import PurchaseReturn
    return _total(
        PurchaseReturn.objects.filter(business=business, date__gte=start, date__lte=end)
    )


def split_refund(outstanding, amount):
    """Split a refund into (cash, credit). DEBT FIRST, CASH SECOND.

    Added 2026-07-12 to close a real money hole: nothing stopped a CASH refund on a
    record that had never been paid. An unpaid ₱430 purchase order could book an ₱85
    "cash refund" — the supplier handing back money we never gave them — and on the
    sales side it was worse, because store credit is paused, so cash was the ONLY method
    available: returning goods on an utang sale paid the customer ₱85 they had never
    paid us. Money walking out the door.

    The rule that makes an impossible refund unrepresentable rather than merely rejected:

        credit = min(amount, outstanding)      # wipe the debt first
        cash   = amount - credit               # only what's left can be cash

    ★ The invariant it buys: YOU NEVER RECEIVE CASH WHILE YOU STILL OWE MONEY.
      Cash can only come back once the balance is settled, which is exactly how a real
      supplier (or shop) handles it — a credit note before a cash refund.

    Answers "do they have to pay it off first?" with NO: an unpaid order returning ₱85
    simply owes ₱85 less. Nobody has to settle ₱430 to claw back ₱85.

    `outstanding` must be read BEFORE this return is saved. Several returns against one
    record just apply this in sequence, each against the balance the last one left.
    """
    outstanding = max(outstanding or ZERO, ZERO)   # overpaid records owe nothing
    amount      = amount or ZERO

    credit = min(amount, outstanding)
    cash   = amount - credit
    return cash, credit


def refund_method_for(cash, credit):
    """The single display code for a split — what the badge in the lists reads off."""
    if cash > ZERO and credit > ZERO:
        return 'mixed'
    return 'credit' if credit > ZERO else 'cash'


def net_profit(revenue, purchases, salary, waste, bills,
               sales_returns=ZERO, purchase_returns=ZERO):
    """THE accrual profit formula. One definition, every page.

        (revenue - sales returns) - (purchases - purchase returns) - salary - waste - bills

    Note that either net figure can legitimately go NEGATIVE inside a narrow window — a
    ₱500 refund in a week you bought nothing gives net purchases of -₱500, which is
    correct (the supplier handed money back) and must NOT be clamped to zero. Clamping
    would leak the refund out of the books entirely.
    """
    net_revenue   = revenue   - sales_returns
    net_purchases = purchases - purchase_returns
    return net_revenue - net_purchases - salary - waste - bills
