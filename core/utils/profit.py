"""THE profit formula. One definition, every page.

Added 2026-07-13, replacing the `net_profit` that lived in core/utils/returns.py.

★★ WHAT CHANGED AND WHY — read this before "simplifying" anything below.

The old formula subtracted the stock you BOUGHT in the window:

    net = revenue - PURCHASES - payroll - waste - bills          # <- the old one

That is not profit, and it is not accrual either. Buy ₱10,000 of stock in July and sell
₱2,000 of it, and July reported a catastrophic loss while the goods sat safely on the
shelf. The next month, selling that same stock without rebuying, reported a fake windfall.
The number swung with the BUYING cycle instead of the TRADING one.

The formula now subtracts the cost of the goods that actually LEFT the shelf:

    net = (revenue - refunds) - COGS - payroll - waste - bills   # <- this one

COGS = Σ (SaleItem.cost_price × quantity) over the sales in the window. `cost_price` is a
SNAPSHOT taken at the moment of sale (Sales/views.py sets it from the product's cost when
the line is added to the cart), so a later price change never rewrites an old sale's cost.
That snapshot is the only reason true COGS is possible here at all.

★ CONSEQUENCE 1 — PURCHASE RETURNS NO LONGER TOUCH PROFIT, and that is correct.
  Buying stock moves money into inventory; sending it back moves it out again. Neither
  event is a sale, so neither belongs in a profit-and-loss. (They still very much matter
  to CASH FLOW and to Expense Analytics — that is a different question and a different
  page.) The old formula had to net them off only because it was subtracting purchases.
  So `purchase_returns` is deliberately ABSENT from net_profit() below. Do not add it back.

★ CONSEQUENCE 2 — A SALES RETURN RELIEVES COGS TOO, not just revenue.
  When a customer brings goods back you refund the money AND the goods return to you, so
  you no longer bore their cost. Reduce revenue but NOT cost and every return would book a
  phantom loss equal to the item's full cost.

  ★ This applies to RESELLABLE **AND** DAMAGED returns alike — subtle, and the whole reason
    returned_cogs() ignores the `resellable` flag. A damaged return does not go back on the
    shelf, but Sales/views.py already writes a **Waste record** for its cost. So the cost is
    re-charged there. Relieve COGS in both cases and the books come out right:

        resellable:  −revenue, −COGS            → profit unchanged (sale fully unwound)
        damaged:     −revenue, −COGS, +waste    → profit falls by the COST (you ate it)

    Skip the relief for damaged items and you would charge that cost TWICE — once in COGS,
    once in Waste — booking double the real loss.

Everything is keyed on the RETURN's own date, never the original sale's: a July refund
against a June sale lands in JULY. Same rule as everywhere else — the past is sealed.
"""

from decimal import Decimal

from django.db.models import DecimalField, ExpressionWrapper, F, Sum

ZERO = Decimal('0')

# Wide enough that a peso column can't overflow mid-aggregate.
MONEY = DecimalField(max_digits=18, decimal_places=6)

# What a sold line COST us (not what it sold for).
COGS_LINE = ExpressionWrapper(F('cost_price') * F('quantity'), output_field=MONEY)

# What a returned line cost us, read back through the original sale line's snapshot.
RETURNED_COGS_LINE = ExpressionWrapper(
    F('original_sale_item__cost_price') * F('quantity'), output_field=MONEY,
)


def cogs_of(sales):
    """Cost of the goods sold on these sales.

    Services legitimately carry cost_price = 0 — there is nothing on a shelf behind a
    haircut — so they contribute nothing here and that is right, not a gap.
    """
    from Sales.models import SaleItem
    return SaleItem.objects.filter(sale__in=sales).aggregate(
        t=Sum(COGS_LINE))['t'] or ZERO


def returned_cogs_of(returns):
    """Cost of goods handed back to us by these returns — the COGS relief.

    Counts resellable AND damaged lines; see CONSEQUENCE 2 in the module docstring for why
    excluding the damaged ones would double-charge their cost.

    `original_sale_item` is nullable (SET_NULL), so a return whose sale line was deleted
    contributes no relief rather than crashing. Those rows can't be priced at all.
    """
    from Sales.models import SalesReturnItem
    return SalesReturnItem.objects.filter(
        sales_return__in=returns, original_sale_item__isnull=False,
    ).aggregate(t=Sum(RETURNED_COGS_LINE))['t'] or ZERO


def cogs_in(business, start, end):
    """Net COGS for a window: cost of what was sold, less the cost of what came back.

    Can go NEGATIVE in a narrow window — return goods in a week you sold nothing and the
    cost of sales is genuinely below zero for that week. Real; never clamp it.
    """
    from Sales.models import Sale, SalesReturn
    sales = Sale.objects.active().filter(
        business=business, date__gte=start, date__lte=end)
    returns = SalesReturn.objects.filter(
        business=business, date__gte=start, date__lte=end)
    return cogs_of(sales) - returned_cogs_of(returns)


def net_profit(revenue, cogs, salary, waste, bills, sales_returns=ZERO):
    """THE accrual profit formula.

        (revenue - sales returns) - COGS - payroll - waste - business expenses

    `cogs` is expected to be ALREADY NET of returned goods (use cogs_in, or subtract
    returned_cogs_of yourself) — the relief belongs with the cost, not bolted on here,
    so that a caller holding a pre-netted figure can't accidentally relieve it twice.

    ★ There is no `purchase_returns` argument. Stock bought and stock sent back are both
      inventory movements, not trading results. See the module docstring.

    Can legitimately go negative. Never clamp.
    """
    return (revenue - sales_returns) - cogs - salary - waste - bills


def gross_margin(revenue, cogs, sales_returns=ZERO):
    """What's left of a peso of sales after paying for the goods, before running costs.

    The number that says whether the SHOP works, independent of how big the rent is.
    """
    return (revenue - sales_returns) - cogs


def margin_pct(revenue, cogs, sales_returns=ZERO):
    """Gross margin as a % of net revenue. None when there is no revenue to divide by —
    a 0.0% would read as "we made nothing on our sales" rather than "there were none"."""
    net_revenue = revenue - sales_returns
    if not net_revenue:
        return None
    return float(gross_margin(revenue, cogs, sales_returns) / net_revenue * 100)
