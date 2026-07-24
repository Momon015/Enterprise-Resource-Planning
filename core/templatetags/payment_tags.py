from django import template
from django.templatetags.static import static
from django.utils.html import format_html

register = template.Library()

# payment method code -> (Bootstrap icon, plain-language label)
# Codes come from Sale/Purchase.payment_method_code (see the models).
_METHOD_META = {
    'cash':   ('bi-cash-stack',  'Cash'),
    'gcash':  ('bi-wallet2',    'GCash'),   # rendered with the GCash logo below
    'bank':   ('bi-bank',       'Bank Transfer'),
    'cod':    ('bi-truck',      'COD'),
    'credit': ('bi-credit-card-2-back',  'Credit'),
    'mixed':  ('bi-collection', 'Mixed'),
}

# Methods that use a brand logo image instead of a Bootstrap icon.
_LOGO_METHODS = {'gcash': 'images/gcash.svg'}

# Refund method code -> (icon, label, colour slot). Kept SEPARATE from _METHOD_META on
# purpose: a return's 'credit' is the customer's or supplier's BALANCE, not a credit card,
# so it must not inherit the card icon and the bare word "Credit" from the payment pill.
# The COLOUR slot is shared, which is the whole point — cash reads emerald on a return
# exactly as it does on the sale it reverses.
#
# 'store_credit' is a legacy code that predates REFUND_METHOD_CHOICES (which is only
# cash/credit/mixed); it maps onto the credit slot so old rows don't render unstyled.
_REFUND_META = {
    'cash':         ('bi-cash-stack', 'Cash',           'cash'),
    'credit':       ('bi-wallet2',    'Balance',        'credit'),
    'store_credit': ('bi-wallet2',    'Balance',        'credit'),
    'mixed':        ('bi-collection', 'Balance + cash', 'mixed'),
}


@register.simple_tag
def payment_method_badge(code, muted=False):
    """Render a payment method as an icon + label pill.

    `code` is a Sale/Purchase.payment_method_code value. Reuses the existing
    sl-badge styling so no new CSS is needed. Shows a muted dash when nothing
    has been paid yet (code is None). Pass `muted=True` (e.g. for a voided
    transaction) to strip the method's hue so the pill reads as inactive."""
    if not code:
        return format_html('<span class="pay-method-empty">{}</span>', '—')
    icon, label = _METHOD_META.get(code, ('bi-cash-stack', code.title()))
    if code in _LOGO_METHODS:
        mark = format_html(
            '<img src="{}" alt="" class="pay-method__logo">',
            static(_LOGO_METHODS[code]),
        )
    else:
        mark = format_html('<i class="bi {}"></i>', icon)
    modifier = 'void' if muted else code
    return format_html(
        '<span class="pay-method pay-method--{}">{} {}</span>',
        modifier, mark, label,
    )


@register.simple_tag
def refund_method_badge(code):
    """Render a return's refund method as a payment-method pill.

    Both return lists used to hand-roll their own chips, and picked different colours from
    the lists they mirror: a CASH refund came out amber (`sl-badge-warning`) while the cash
    that paid for the sale came out emerald. Same money, two colours, depending only on
    which list you were standing in. This reuses the `.pay-method` component so the colour
    language is shared — no new CSS, so no `?v=` bump.

    Labels stay the return's own (see _REFUND_META): the colour is what mirrors, not the
    wording.
    """
    if not code:
        return format_html('<span class="pay-method-empty">{}</span>', '—')
    icon, label, slot = _REFUND_META.get(code, ('bi-cash-stack', code.title(), 'mixed'))
    return format_html(
        '<span class="pay-method pay-method--{}"><i class="bi {}"></i> {}</span>',
        slot, icon, label,
    )


@register.simple_tag
def settlement_badge(obj):
    """Render the paid-status chip for a Sale or Purchase from its
    `settlement_badge` property — one source of truth for the Status column
    and the detail page. Void is handled separately by the caller."""
    b = obj.settlement_badge
    icon = format_html('<i class="bi {}"></i> ', b['icon']) if b['icon'] else ''
    amount = format_html(' · ₱{}', '{:,.2f}'.format(b['amount'])) if b['amount'] is not None else ''
    return format_html(
        '<span class="sl-badge sl-badge-{}">{}{}{}</span>',
        b['level'], icon, b['label'], amount,
    )
