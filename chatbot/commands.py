"""
Rule-based bilingual command parser. Stateless except for language toggle
(stored in Django session). No model needed.
"""
from decimal import Decimal
from django.db.models import Sum, Q
from django.utils import timezone

from core.constants import LOW_STOCK_THRESHOLD


LANG_KEY = 'chatbot_lang'
DEFAULT_LANG = 'en'

# Filipino → English alias. Also self-maps English to English.
ALIASES = {
    '/help':     '/help',
    '/tulong':   '/help',
    '/stock':    '/stock',
    '/sales':    '/sales',
    '/benta':    '/sales',
    '/expense':  '/expense',
    '/gastos':   '/expense',          # Filipino general "spending"
    '/purchase': '/purchase',
    '/pagbili':  '/purchase',         # Filipino "buying from supplier"
    '/bili':     '/purchase',  
    '/low':      '/low',
    '/kaunti':   '/low',
    '/kakaunti': '/low',
    '/out':      '/out',
    '/ubos':     '/out',
    '/wala':     '/out',
    '/english':  '/lang_en',     # special — language switch
    '/filipino': '/lang_fil',
    '/tagalog':  '/lang_fil',
}

# Period args — accept both languages
TODAY_WORDS = {'today', 'ngayon', 'today\'s', 'araw'}
MONTH_WORDS = {'month', 'buwan', 'monthly', 'buwang-ito'}

def _scope_to_user(qs, user):
    """Staff see only their own transactions. Owners/dev see everything."""
    if user.role == 'staff':
        return qs.filter(created_by=user)
    return qs

def parse_and_execute(request, query, business):
    lang = request.session.get(LANG_KEY, DEFAULT_LANG)
    user = request.user
    query = (query or '').strip()

    if not query.startswith('/'):
        return _t(lang,
                  en="Commands start with `/`. Try `/help`.",
                  fil="Mag-start sa `/` ang mga utos. Subukan ang `/tulong`.")

    parts = query.split(maxsplit=1)
    raw_cmd = parts[0].lower()
    args = parts[1] if len(parts) > 1 else ''
    cmd = ALIASES.get(raw_cmd, raw_cmd)

    # Language switch commands
    if cmd == '/lang_en':
        request.session[LANG_KEY] = 'en'
        return "Switched to English. Type `/help` for commands."
    if cmd == '/lang_fil':
        request.session[LANG_KEY] = 'fil'
        return "Lumipat sa Filipino. I-type ang `/tulong` para sa mga utos."

    handler = COMMANDS.get(cmd)
    if not handler:
        return _t(lang,
                  en=f"Unknown command: `{raw_cmd}`. Try `/help`.",
                  fil=f"Hindi kilala ang utos: `{raw_cmd}`. Subukan ang `/tulong`.")
    
    try:
        return handler(business, user, args, lang)
    except Exception as e:
        return _t(lang,
                  en=f"Something went wrong: {e}",
                  fil=f"May mali: {e}")
        
def _t(lang, en, fil):
    """Translation helper. Returns string in chosen language."""
    return fil if lang == 'fil' else en

# ── Handlers ──────────────────────────────────────────────────────────

def handle_help(business, user, args, lang):
    if lang == 'fil':
        return (
            "Mga utos:\n"
            "• `/stock <pangalan>` — tingnan ang stock\n"
            "• `/benta ngayon` o `/benta buwan` — buod ng benta\n"
            "• `/pagbili ngayon` o `/pagbili buwan` — buod ng pagbili sa supplier\n"
            "• `/gastos ngayon` o `/gastos buwan` — buod ng gastos (kuryente, tubig, etc.)\n"
            "• `/kaunti` — listahan ng halos-ubos\n"
            "• `/wala` — listahan ng wala nang stock\n"
            "• `/english` o `/filipino` — palitan ang wika"
        )
    return (
        "Available commands:\n"
        "• `/stock <name>` — check quantity of an item\n"
        "• `/sales today` or `/sales month` — sales summary\n"
        "• `/purchase today` or `/purchase month` — supplier purchases\n"
        "• `/expense today` or `/expense month` — misc expenses (rent, utilities)\n"
        "• `/low` — list low-stock items\n"
        "• `/out` — list out-of-stock items\n"
        "• `/english` or `/filipino` — switch language"
    )

    
def handle_stock(business, args, lang):
    from Inventory.models import Stock
    if not args:
        return _t(lang,
                  en="Usage: `/stock <item name>` — e.g. `/stock coke`",
                  fil="Paggamit: `/stock <pangalan>` — halimbawa `/stock coke`")

    matches = (
        Stock.objects.filter(business=business)
        .exclude(material__status='inactive')
        .filter(Q(name__icontains=args) | Q(material__name__icontains=args))
        .order_by('-quantity')[:5]
    )
    
    if not matches:
        return _t(lang,
                  en=f"No stock found matching `{args}`.",
                  fil=f"Walang nahanap na stock para sa `{args}`.")

    header = _t(lang, en=f"Stock matching `{args}`:",
                       fil=f"Stock na tumutugma sa `{args}`:")
    lines = [header]
    for s in matches:
        lines.append(f"• {s.name}: {s.quantity} {s.unit or ''}".strip())
    return "\n".join(lines)

def handle_sales(business, user, args, lang):
    from Sales.models import Sale
    period = args.strip().lower()
    today = timezone.localdate()

    if period in TODAY_WORDS:
        qs = Sale.objects.filter(business=business, date=today)
        label = _t(lang, en="today", fil="ngayon")
    elif period in MONTH_WORDS:
        qs = Sale.objects.filter(business=business, date__gte=today.replace(day=1))
        label = _t(lang, en="this month", fil="ngayong buwan")
    else:
        return _t(lang,
                  en="Usage: `/sales today` or `/sales month`",
                  fil="Paggamit: `/benta ngayon` o `/benta buwan`")
    
    qs = _scope_to_user(qs, user)  
    total = qs.aggregate(t=Sum('total_revenue'))['t'] or Decimal('0')
    return _t(lang,
              en=f"Sales {label}: {qs.count()} transaction(s), ₱{total:.2f}",
              fil=f"Benta {label}: {qs.count()} transaksyon, ₱{total:.2f}")
    
def handle_expense(business, user, args, lang):
    from Expense.models import Expense
    period = args.strip().lower()
    today = timezone.localdate()

    if period in TODAY_WORDS:
        qs = Expense.objects.filter(business=business, date=today)
        label = _t(lang, en="today", fil="ngayon")
    elif period in MONTH_WORDS:
        qs = Expense.objects.filter(business=business, date__gte=today.replace(day=1))
        label = _t(lang, en="this month", fil="ngayong buwan")
    else:
        return _t(lang,
                  en="Usage: `/expense today` or `/expense month`",
                  fil="Paggamit: `/gastos ngayon` o `/gastos buwan`")

    qs = _scope_to_user(qs, user)
    total = qs.aggregate(t=Sum('total_amount'))['t'] or Decimal('0')
    return _t(lang,
              en=f"Expenses {label}: {qs.count()} entry(s), ₱{total:.2f}",
              fil=f"Gastos {label}: {qs.count()} entry, ₱{total:.2f}")

    
    
def handle_purchase(business, user, args, lang):
    from Expense.models import Purchase
    period = args.strip().lower()
    today = timezone.localdate()

    if period in TODAY_WORDS:
        qs = Purchase.objects.filter(business=business, purchase_date=today)
        label = _t(lang, en="today", fil="ngayon")
    elif period in MONTH_WORDS:
        qs = Purchase.objects.filter(business=business, purchase_date__gte=today.replace(day=1))
        label = _t(lang, en="this month", fil="ngayong buwan")
    else:
        return _t(lang,
                  en="Usage: `/purchase today` or `/purchase month`",
                  fil="Paggamit: `/pagbili ngayon` o `/pagbili buwan`")

    qs = _scope_to_user(qs, user)
    total = qs.aggregate(t=Sum('total_cost'))['t'] or Decimal('0')
    return _t(lang,
              en=f"Purchases {label}: {qs.count()} order(s), ₱{total:.2f}",
              fil=f"Pagbili {label}: {qs.count()} order, ₱{total:.2f}")


def handle_low(business, args, lang):
    from Inventory.models import Stock
    stocks = (
        Stock.objects.filter(
            business=business,
            quantity__lte=LOW_STOCK_THRESHOLD,
            quantity__gte=1,
        ).exclude(material__status='inactive')
        .order_by('quantity')[:10]
    )
    
    if not stocks:
        return _t(lang,
                  en="✅ No items are low on stock.",
                  fil="✅ Walang kakaunting stock ngayon.")
        
    header = _t(lang, en=f"Low stock items (≤ {LOW_STOCK_THRESHOLD}):",
                      fil=f"Kakaunting stock (≤ {LOW_STOCK_THRESHOLD}):")
    
    lines = [header]
    for s in stocks:
        suffix = _t(lang, en=f"{s.quantity} left", fil=f"{s.quantity} nalang natitira.")
        lines.append(f"• {s.name}: {suffix}")
    return "\n".join(lines)

def handle_out(business, args, lang):
    from Inventory.models import Stock
    stocks = (
        Stock.objects.filter(business=business, quantity=0)
        .exclude(material__status='inactive')
        .order_by('name')[:10]
    )
    if not stocks:
        return _t(lang,
                  en="✅ Nothing is out of stock.",
                  fil="✅ Walang wala nang stock.")

    header = _t(lang, en="Out-of-stock items:", fil="Walang stock:")
    lines = [header]
    for s in stocks:
        lines.append(f"• {s.name}")
    return "\n".join(lines)


COMMANDS = {
    '/help':  handle_help,
    '/stock': handle_stock,
    '/sales': handle_sales,
    '/expense': handle_expense,
    '/low':   handle_low,
    '/out':   handle_out,
}