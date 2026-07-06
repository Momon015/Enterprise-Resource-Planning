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


@register.simple_tag
def payment_method_badge(code):
    """Render a payment method as an icon + label pill.

    `code` is a Sale/Purchase.payment_method_code value. Reuses the existing
    sl-badge styling so no new CSS is needed. Shows a muted dash when nothing
    has been paid yet (code is None)."""
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
    return format_html(
        '<span class="pay-method pay-method--{}">{} {}</span>',
        code, mark, label,
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
