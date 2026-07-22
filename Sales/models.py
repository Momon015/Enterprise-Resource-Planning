from django.db import models

from Product.models import Product

from user.models import User, BusinessProfile

from django.db.models import Sum, Avg

from Employee.models import Employee

from decimal import Decimal, ROUND_DOWN

from django.utils import timezone

from core.models import TimeStampModel, AbstractDocumentSequence
from django.core.exceptions import ValidationError

from core.utils.owner import get_owner

# Create your models here.

class SaleQuerySet(models.QuerySet):
    def active(self):
        """Only real, countable sales — excludes voids AND non-completed drafts.
        Use for all revenue/count aggregations."""
        return self.filter(is_void=False, status='completed')

    def drafts(self):
        """Pending + canceled — the draft list (never in the sales record)."""
        return self.exclude(status='completed')

    
    def total_revenue(self):
        return self.active().aggregate(total_revenue=Sum('total_revenue'))['total_revenue']

    def average_total_revenue(self):
        return self.active().aggregate(average_total_revenue=Avg('total_revenue'))['average_total_revenue']

class SaleSequence(AbstractDocumentSequence):
    """SI- series — one continuous run per business. BIR-accountable; advances ONLY in
    official mode (is_bir_active=True), so it can begin at 1 the day a business is accredited."""
    pass

class OrderSequence(AbstractDocumentSequence):
    """ORD- series — INTERNAL-mode order numbers (is_bir_active=False).

    NOT a BIR accountable document — an ordinary order/billing reference for the unofficial
    slip. Kept SEPARATE from SaleSequence on purpose: while a business runs in internal mode
    its sales draw ORD- numbers and the SI- accountable run never advances, so SI- can start
    fresh at 1 the day the business is accredited and flips to official mode."""
    pass

class VoidSequence(AbstractDocumentSequence):
    """VD- series — voids are numbered documents, not just a flag.

    RMO 24-2023 Annex D-2 prints "Beg. VOID #" and "End. VOID #" on every Z reading
    alongside the SI and RETURN runs, so a void has to carry its own accountable
    number. p.4(k) says the same thing from the other direction: void, cancellation
    and refund papers are SUPPLEMENTARY INVOICES — which is also why they must print
    "THIS DOCUMENT IS NOT VALID FOR CLAIM OF INPUT TAX".

    Separate from the SI run on purpose. Voiding does not consume a sales invoice
    number; it issues a different kind of document about one.
    """
    pass
 
class Sale(TimeStampModel):
    
    VOID_REASON_CHOICES = [
        ('Wrong price',            'Wrong price'),
        ('Wrong quantity',         'Wrong quantity'),
        ('Forgot to apply discount','Forgot to apply discount'),
        ('Wrong item',             'Wrong item'),
        ('Test / accidental entry','Test / accidental entry'),
        ('Other',                  'Other'),
    ]
    
    # ── Draft / payment-confirmation status ───────────────
    STATUS_PENDING   = 'pending'
    STATUS_CANCELED  = 'canceled'
    STATUS_COMPLETED = 'completed'
    STATUS_CHOICES = [
        (STATUS_PENDING,   'Pending'),      # GCash/Bank not yet confirmed received
        (STATUS_CANCELED,  'Canceled'),     # payment never landed — kept, never a real sale
        (STATUS_COMPLETED, 'Completed'),    # confirmed received — the real sale
    ]

    CANCEL_REASON_CHOICES = [
        ('Payment not received', 'Payment not received'),
        ('Customer left',        'Customer left'),
        ('Duplicate / mistake',  'Duplicate / mistake'),
        ('Other',                'Other'),
    ]

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='sales', null=True, blank=True)
    # NULL until the sale is COMPLETED. A parked draft has no books date because it is
    # not yet in the books — it is an intent (items + amount + payment method) and
    # nothing more. Stamped in save() at the moment status becomes completed, so a
    # draft parked Monday and confirmed Wednesday books to WEDNESDAY, the day it
    # actually became a sale. See the reference note in save().
    date = models.DateField(db_index=True, null=True, blank=True)
    total_revenue = models.DecimalField(max_digits=16, decimal_places=6, null=True, blank=True)
    total_salary_cost = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    line_count = models.PositiveIntegerField(default=0)
    reference = models.CharField(max_length=255, null=True, blank=True)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, related_name='created_sales', null=True, blank=True)
    business = models.ForeignKey(BusinessProfile, on_delete=models.SET_NULL, related_name='sales', null=True, blank=True)
    is_locked = models.BooleanField(default=False, db_index=True)
    
    # ── Void (cancellation, not a return) ─────────────────
    is_void     = models.BooleanField(default=False, db_index=True)
    # The void's own accountable number (VD-0000000001), issued at void time from a
    # series separate to SI. NULL on every sale that was never voided. Drives the
    # "Beg./End. VOID #" pair on the Z reading.
    void_reference = models.CharField(max_length=255, null=True, blank=True, db_index=True)
    void_reason = models.CharField(max_length=255, blank=True)
    voided_by   = models.ForeignKey(User, on_delete=models.SET_NULL, related_name='voided_sales', null=True, blank=True)
    voided_at   = models.DateTimeField(null=True, blank=True)
    
    # ── Draft status + Cancel (a draft whose payment never landed — NOT a void) ──
    # "Draft" = any status that isn't 'completed'; drafts stay OUT of the sales record.
    status          = models.CharField(max_length=10, choices=STATUS_CHOICES,
                                        default=STATUS_COMPLETED, db_index=True)
    canceled_reason = models.CharField(max_length=255, blank=True)
    canceled_by     = models.ForeignKey(User, on_delete=models.SET_NULL,
                                        related_name='canceled_sales', null=True, blank=True)
    canceled_at     = models.DateTimeField(null=True, blank=True)
    
    # ── Intended payment for a PENDING draft (consumed by finalize on confirm) ──
    pending_method = models.CharField(max_length=20, blank=True)   # gcash / bank
    pending_status = models.CharField(max_length=10, blank=True)   # full / partial
    pending_amount = models.DecimalField(max_digits=16, decimal_places=6, null=True, blank=True)
    pending_note   = models.CharField(max_length=255, blank=True)


    discount_percent = models.DecimalField(max_digits=5, decimal_places=2, default=0)   # whole-order % (sales are %-only)
    discount_amount  = models.DecimalField(max_digits=16, decimal_places=6, default=0)  # computed peso, stored for the receipt

    # ── Statutory discounts (SC / PWD / NAAC / Solo Parent) ──────────────────
    # NOT an ordinary discount, and the difference drives most of the design:
    #   * the rate is FIXED BY LAW, not typed by the cashier
    #   * the business cannot decline it, so it must work even when the owner has
    #     `enable_sale_discount` switched off
    #   * RMO 24-2023 p.5(n) requires the ID number, the holder's name and a SIGNATURE
    #     on the invoice, and the Z reading breaks the day's discounts out by category
    #
    # `discount_percent` still carries the applied rate — a statutory discount just
    # SETS it — so all the existing peso math, receipt rendering and refund logic keep
    # working untouched. The rate is stored rather than looked up at read time for the
    # same reason `price_at_sale` and `vat_class` are snapshots: if Congress changes a
    # rate, an old sale must still reproduce its own arithmetic.
    #
    # Mutually exclusive with an ordinary discount — a sale is either statutory or it
    # isn't. Stacking them is not something the law contemplates and not something a
    # cashier should be able to do by accident.
    DISCOUNT_REGULAR     = ''
    DISCOUNT_SC          = 'sc'
    DISCOUNT_PWD         = 'pwd'
    DISCOUNT_NAAC        = 'naac'
    DISCOUNT_SOLO_PARENT = 'solo_parent'
    DISCOUNT_TYPE_CHOICES = [
        (DISCOUNT_REGULAR,     'Regular customer'),
        # Abbreviated ON PURPOSE — this display drives the RECEIPT (58mm thermal), where a
        # full name like "National Athlete / Coach (20%)" wraps. The cart dropdown keeps the
        # friendly full names (its own labels in sale-cart.jsx), and these match how real PH
        # receipts print them (Savemore prints "SC", "PWD").
        (DISCOUNT_SC,          'SC'),
        (DISCOUNT_PWD,         'PWD'),
        (DISCOUNT_NAAC,        'NAAC'),
        (DISCOUNT_SOLO_PARENT, 'SP'),
    ]
    # STATUTORY BANDS — the legal (rate, VAT-exempt) pairs each type may carry. A type can
    # have MORE THAN ONE band. SC and PWD are 20% AND VAT-exempt on most goods, but on the
    # DTI/DA "basic necessities and prime commodities" list (groceries) the law grants only
    # 5%, and that 5% KEEPS the VAT — it comes off the GROSS shelf price with no VAT
    # machinery at all. The first band is the DEFAULT (highest-relief), used when a caller
    # names no rate. RA 9994 (SC) / RA 10754 (PWD) → 20% or 5%; RA 10699 (NAAC) → 20%,
    # discount ONLY, no exemption; RA 11861 (Solo Parent) → 10% on specified child goods.
    #
    # Eligibility per item (WHICH goods qualify for the 5%) is NOT enforced here: the cashier
    # picks the band, the same way they judge eligibility at every PH counter. Whole-order for
    # now — a mixed basket applies the chosen band to everything, which only ever OVER-relieves
    # (costs the owner, never the customer). Per-line attribution is Phase 2.
    #
    # Why NAAC is the odd one out on VAT: RMO 24-2023 Annex D-2's VAT ADJUSTMENT block lists
    # SC TRANS and PWD TRANS but NOT NAAC, while its DISCOUNT SUMMARY lists all four.
    # ⚠️ Two things are accountant-pending: Solo Parent's exemption, and — for the 5% band on
    # a VAT-REGISTERED seller — whether VAT reports on the gross or on the discounted amount.
    # We compute VAT on the discounted amount (it reconciles). Only VAT-registered sellers ever
    # feel any of this; a non-VAT seller has no VAT to exempt, and most of our clients are non-VAT.
    STATUTORY_BANDS = {
        DISCOUNT_SC:          [(Decimal('20'), True),  (Decimal('5'), False)],
        DISCOUNT_PWD:         [(Decimal('20'), True),  (Decimal('5'), False)],
        DISCOUNT_NAAC:        [(Decimal('20'), False)],
        DISCOUNT_SOLO_PARENT: [(Decimal('10'), True)],
    }
    # Backward-compatible views of the bands (first = default, highest-relief band):
    #   STATUTORY_RATES      — default rate per type (what statutory_rate() returns).
    #   STATUTORY_VAT_EXEMPT — types whose DEFAULT band is VAT-exempt; answers only "does this
    #     TYPE ever carry an exemption". The rate-aware exception (SC/PWD at 5% keep the VAT)
    #     lives in statutory_vat_exempt(type, rate) — always prefer that for real arithmetic.
    STATUTORY_RATES = {t: bands[0][0] for t, bands in STATUTORY_BANDS.items()}
    STATUTORY_VAT_EXEMPT = {t for t, bands in STATUTORY_BANDS.items() if bands[0][1]}

    discount_type = models.CharField(
        max_length=12, choices=DISCOUNT_TYPE_CHOICES, blank=True,
        default=DISCOUNT_REGULAR, db_index=True,
    )
    # OSCA no. / PWD ID / PNSTM ID / Solo Parent ID — whichever applies to the type.
    discount_id_no = models.CharField(max_length=60, blank=True)
    discount_name  = models.CharField(max_length=255, blank=True)   # holder, not the payer
    # "TIN, if any" (p.5(n)) — genuinely optional; most SC/PWD holders won't have one.
    discount_tin   = models.CharField(max_length=20, blank=True)

    # The VAT removed because the BUYER was exempt — NOT because the goods were. Kept as
    # its own figure because Annex D-2 deducts it on a line separate from the discount:
    #
    #     Gross Amount          50.00
    #     Less Discount         -8.93
    #     Less VAT Adjustment   -5.36
    #     Net Amount            35.71
    #
    # and the Z reading reports a DISCOUNT SUMMARY and a VAT ADJUSTMENT block that must
    # each tie to their own total. Merging the two into one "discount" would make both
    # blocks unreportable. Always 0 for a non-VAT seller, which is most of our clients.
    vat_adjustment = models.DecimalField(max_digits=16, decimal_places=6, default=0)

    # BIR "Invoice Reset Counter" (RMO 24-2023 p.4(b)) — printed on the invoice beside the
    # number ("INVOICE RESET COUNT 000"). Captured from the SI sequence at COMPLETION, the
    # same instant the number is stamped, so a later counter reset can never rewrite an old
    # invoice's printed value (pen-not-pencil). Null on drafts and on sales rung before this
    # field existed — the invoice template falls back to 000 for those.
    books_reset_counter = models.PositiveIntegerField(null=True, blank=True)

    objects = SaleQuerySet.as_manager()
    
    def __str__(self):
        return f"Date: {self.date} - {self.total_revenue}"
    
    def quantity_item(self):
        return sum(item.quantity for item in self.sale_items.all())
    
    @property
    def is_draft(self):
        """Not yet a real sale (pending or canceled) — kept OUT of the sales record."""
        return self.status != self.STATUS_COMPLETED
    
    @property
    def is_pending(self):
        return self.status == self.STATUS_PENDING
    
    @property
    def is_canceled(self):
        return self.status == self.STATUS_CANCELED
    
    def save(self, *args, **kwargs):
        if not self._state.adding and self.is_locked:
            # void_reference belongs here for the same reason the rest do: a posted sale
            # is immutable, but VOIDING it is an append, and the void carries its own
            # document number. Omit it and stamping the number raises on every void.
            allowed = {'is_void', 'void_reason', 'voided_by', 'voided_at', 'is_locked',
                       'void_reference'}
            uf = kwargs.get('update_fields')
            if uf is None or not set(uf) <= allowed:
                raise ValueError("Posted sale is immutable — append a void/return/adjust instead.")

        # ── Books date + accountable invoice number: COMPLETED sales only ──────
        # Both are stamped here, together, at the instant the sale becomes real.
        #
        # A draft used to claim `SI-…` the moment it was parked, which produced two
        # problems at once. (1) A cancelled draft left a number sitting in the
        # accountable series that never became an invoice. (2) Worse, the series
        # stopped being chronological — park draft A, sell B and C, then confirm A,
        # and the customer receives SI-1 after SI-3 has already gone out.
        #
        # RMO 24-2023 p.4 note: "If the system generates transaction number, SI/OR
        # number should be a different series." A number assigned before the sale
        # exists IS a transaction number, so drawing it from the SI run collided
        # with exactly that. Assigning at completion keeps the SI series
        # chronological AND free of numbers that were never issued — correct under
        # both the strict and lenient readings of "sequential series of accountable
        # documents", which matters because the RMO never defines the term.
        #
        # A cancelled draft therefore keeps date=None and reference=None forever.
        # It is a real record of an abandoned intent, not a gap: it never held a
        # number, so none went missing.
        stamped = []
        if self.status == self.STATUS_COMPLETED:
            if not self.date:
                self.date = timezone.localdate()
                stamped.append('date')

            if not self.reference and self.business:
                # The reference SERIES depends on the BIR mode (is_bir_active):
                #   Official mode -> SI- accountable invoice series, and the reset counter is
                #     captured alongside the number in the same breath (both stamped once at
                #     completion, then frozen).
                #   Internal mode -> a plain ORD- order number; NOT a BIR document, so no reset
                #     counter, and crucially the SI- run is left untouched so it can start at 1
                #     the day this business is accredited.
                if self.business.is_bir_active:
                    self.reference, _, self.books_reset_counter = SaleSequence.issue(self.business, 'SI')
                    stamped += ['reference', 'books_reset_counter']
                else:
                    self.reference, _, _ = OrderSequence.issue(self.business, 'ORD')
                    stamped.append('reference')

        # A caller passing update_fields cannot know we just stamped these — and
        # confirm_sale_draft does exactly that. Without this, date/reference would be
        # set on the instance and silently never written. Widen the list to match
        # what actually changed rather than making every call site remember.
        update_fields = kwargs.get('update_fields')
        if stamped and update_fields is not None:
            kwargs['update_fields'] = list(set(update_fields) | set(stamped))

        super().save(*args, **kwargs)
        
    @property
    def subtotal(self):
        """The TRUE gross — what the goods rang up at before any relief came off.

        Adds the VAT adjustment back as well as the discount. Without it, a senior's
        ₱50 sale at a VAT-registered seller would report ₱44.64 as its gross, and since
        this is the figure the BIR odometer posts, the accumulated grand total would
        quietly under-report by exactly the VAT it exempted.

        Non-VAT sellers have vat_adjustment=0, so this is unchanged for them.
        """
        return ((self.total_revenue or Decimal('0'))
                + (self.discount_amount or Decimal('0'))
                + (self.vat_adjustment or Decimal('0')))

    @classmethod
    def price_breakdown(cls, gross, discount_type, *, seller_charges_vat,
                        rate=None, vatable_gross=None):
        """Split a gross amount into the three lines Annex D-2 prints.

        Order matters for PRESENTATION, not arithmetic: VAT comes off first, then the
        discount applies to the exempt base. (Both are multiplications, so the total is
        the same either way — but the receipt must show them separately, and the
        discount figure differs depending on which base it was taken from.)

        `rate` overrides the statutory rate, for ordinary owner-set discounts.

        `vatable_gross` is how much of the gross actually carries VAT. It matters ONLY for
        a statutory VAT exemption on a MIXED cart: exempt/zero-rated lines never had VAT,
        so dividing the whole gross by 1.12 strips phantom VAT off them — over-stating the
        exemption and understating the sale (the bug that made the Z reading's VAT
        breakdown disagree with its Net line by the VAT of the exempt lines). It defaults
        to the whole gross, so an all-VATable cart — and every non-exempt path — is
        unchanged. This mirrors vat_summary(), which already buckets VAT removal per line.

        Returns Decimals quantized to centavos, with
        total + discount_amount + vat_adjustment == gross always holding.
        """
        from decimal import ROUND_HALF_UP
        cents = Decimal('0.01')

        gross = Decimal(gross or 0).quantize(cents, ROUND_HALF_UP)
        vg = gross if vatable_gross is None else Decimal(vatable_gross or 0).quantize(cents, ROUND_HALF_UP)
        if rate is None:
            rate = cls.statutory_rate(discount_type)
        rate = Decimal(rate or 0)

        # VAT only comes off when the seller actually charges it AND the buyer's
        # (type, rate) BAND carries an exemption. NAAC has a rate but no exemption, and
        # SC/PWD at the 5% basic-necessities band keep their VAT — hence rate-aware.
        if seller_charges_vat and cls.statutory_vat_exempt(discount_type, rate):
            # Remove VAT only from the VATable portion; exempt/zero lines pass through.
            vatable_base = (vg / Decimal('1.12')).quantize(cents, ROUND_HALF_UP)
            base = vatable_base + (gross - vg)
        else:
            base = gross
        vat_adjustment = gross - base

        discount_amount = (base * rate / Decimal('100')).quantize(cents, ROUND_HALF_UP)
        total = base - discount_amount

        return {
            'gross':           gross,
            'vat_adjustment':  vat_adjustment,
            'discount_amount': discount_amount,
            'total':           max(total, Decimal('0.00')),
        }

    @property
    def is_statutory_discount(self):
        """True when this sale carries an SC/PWD/NAAC/Solo Parent discount.

        Distinguishes the two kinds of discount everywhere it matters: the receipt
        prints the ID and signature block only for these, and the Z reading counts
        them under their own category rather than 'Other'.
        """
        return bool(self.discount_type)

    @classmethod
    def statutory_rate(cls, discount_type):
        """The DEFAULT (highest-relief) rate for a discount type, or 0 for a regular
        customer. A type with more than one band (SC/PWD: 20% or 5%) returns its 20%.

        Used at checkout to SET discount_percent when no band is named. Never used to
        re-derive the rate of an existing sale — that one reads its own stored
        discount_percent, so a sale rung before a rate change still prints the
        arithmetic it was rung with.
        """
        return cls.STATUTORY_RATES.get(discount_type or '', Decimal('0'))

    @classmethod
    def resolve_statutory_rate(cls, discount_type, rate):
        """The rate to apply for a (type, requested-rate) pick, validated against the
        type's legal bands. Falls back to the type's DEFAULT (first, highest) band when
        the requested rate isn't a legal band — so a hand-typed query string can't invent
        a rate like SC 3%. Returns 0 for a regular customer.
        """
        bands = cls.STATUTORY_BANDS.get(discount_type or '')
        if not bands:
            return Decimal('0')
        allowed = [r for (r, _exempt) in bands]
        try:
            r = Decimal(str(rate))
        except (ArithmeticError, TypeError, ValueError):
            return allowed[0]
        return r if r in allowed else allowed[0]

    @classmethod
    def statutory_vat_exempt(cls, discount_type, rate):
        """Whether the (type, rate) BAND carries a VAT exemption.

        Exemption follows the BAND, not just the type: SC/PWD at 20% are exempt, but their
        5% basic-necessities band keeps the VAT (5% off the gross, VAT untouched). A rate
        that matches no band falls back to the type's default band, so an older sale that
        stored only its type (rate left at 0) still reads its default treatment.
        """
        bands = cls.STATUTORY_BANDS.get(discount_type or '')
        if not bands:
            return False
        try:
            r = Decimal(str(rate))
        except (ArithmeticError, TypeError, ValueError):
            r = None
        for (band_rate, exempt) in bands:
            if band_rate == r:
                return exempt
        return bands[0][1]   # default (highest-relief) band
    
    def vat_summary(self):
        """PH 12% VAT breakdown, VAT-inclusive, discount-aware.
        Buckets each line by its snapshot vat_class, applies the whole-order
        discount proportionally, then extracts 12% from the VATable bucket.

        ┌─ HOW THE FOUR RECEIPT LINES WORK (worked example, VAT-registered seller) ──────┐
        │ A cart of 8 items:                                                             │
        │   VATable (V):  Item01 500 + Item02 12 + Item03 25          =   537.00         │
        │   Exempt  (E):  Item35 50 + Item36 500 + Item37 500 + I38 250 = 1,300.00       │
        │   Zero    (Z):  Item39 150                                  =   150.00         │
        │   Subtotal (gross, VAT-INCLUSIVE)                           = 1,987.00         │
        │                                                                                │
        │ ── REGULAR customer (no statutory exemption) — each class stays put ──         │
        │   VATable Sale (V) = 537 / 1.12        =   479.46   (VAT-EXCLUSIVE base)       │
        │   VAT (12%)        = 537 − 479.46      =    57.54                              │
        │   VAT-Exempt (E)   =                       1,300.00                            │
        │   Zero-Rated (Z)   =                         150.00                            │
        │   check: (V+VAT) + E + Z = 479.46+57.54+1300+150 = 1,987.00 = net              │
        │                                                                                │
        │ ── SENIOR / PWD 20% (statutory VAT EXEMPTION) ──                               │
        │ The exemption attaches to the BUYER, so VATABLE goods are RECLASSIFIED to      │
        │ Exempt (their VAT stripped out); zero-rated STAYS zero-rated; then the 20%     │
        │ comes off every bucket (keep = 80%). VAT actually DUE becomes 0.               │
        │   VATable 537 → strip VAT → 479.46 → moved into the Exempt bucket              │
        │   Exempt before discount = 479.46 + 1,300 = 1,779.46 ;  Zero = 150             │
        │   VATable Sale (V) = 0.00           ← nothing was sold AS a VATable sale       │
        │   VAT (12%)        = 0.00           ← an exempt sale owes no output VAT        │
        │   VAT-Exempt (E)   = 1,779.46 × 0.80 = 1,423.57                                │
        │   Zero-Rated (Z)   =   150.00 × 0.80 =   120.00                                │
        │   check: E + Z = 1,543.57 = Total (net)                                        │
        │                                                                                │
        │   IMPORTANT So V=0 / VAT=0 on a senior/PWD sale is CORRECT, not a bug —        │ 
        │   the 537 of vatable goods is INSIDE the Exempt line                           │
        │   (479.46, less the 20%). The receipt's separate                               │
        │   `Less VAT (exempt)` line is that 57.54, and it is computed by                │
        │   price_breakdown() from the VATABLE portion ONLY (mixed carts: 537, not the   │
        │   whole 1,987). The 5% "basic-necessities" band does NOT exempt VAT — there V  │
        │   and VAT(12%) stay populated and only the plain 5% comes off.                 │
        └────────────────────────────────────────────────────────────────────────────────┘
        """
        from decimal import Decimal, ROUND_HALF_UP
        cents = Decimal('0.01')
        keep = (Decimal('100') - (self.discount_percent or 0)) / Decimal('100')

        # IMPORTANT: A statutory VAT exemption OVERRIDES every line's own vat_class. An SC or PWD
        # sale is exempt in full — it does not matter that the product is ordinarily
        # VATable, because the exemption attaches to the BUYER, not the goods.
        # NAAC is deliberately absent from STATUTORY_VAT_EXEMPT — 20% off, VAT still due.
        #
        # CRITICAL: The VAT must be REMOVED, not merely relabelled. Our prices are VAT-INCLUSIVE,
        # so a ₱50 VATable sticker is ₱44.64 + ₱5.36 VAT. Moving ₱50 into the exempt
        # bucket would hand the senior only the 20% and quietly keep the VAT — under-
        # relieving the customer AND overstating exempt sales to BIR. Divide it out.
        #
        # Only VATable lines carry VAT to remove: an already-exempt or zero-rated line
        # has none. And a NON-VAT business never charged VAT in the first place, so its
        # prices are stripped of nothing — which is why this is gated on the business.
        # Rate-aware: SC/PWD at 20% exempt the whole order, but their 5% basic-necessities
        # band keeps the VAT (it's a plain 5% off, applied below via `keep`).
        exempt_everything = self.statutory_vat_exempt(self.discount_type, self.discount_percent)
        seller_charges_vat = bool(getattr(self.business, 'is_vat_registered', False))

        buckets = {'vatable': Decimal('0'), 'exempt': Decimal('0'), 'zero': Decimal('0')}
        for item in self.sale_items.all():
            line = (item.price_at_sale or Decimal('0')) * item.quantity
            cls = item.vat_class if item.vat_class in buckets else 'vatable'

            if exempt_everything and cls == 'vatable':
                # A statutory exemption converts VATABLE sales to VAT-exempt (removing the VAT
                # the sticker carried). Already-exempt lines are exempt regardless; ZERO-RATED
                # lines STAY zero-rated — a 0% sale doesn't become "exempt" because the buyer
                # is a senior/PWD, and BIR reports VAT-exempt and zero-rated sales separately.
                # (Previously every class was forced to 'exempt', so a zero-rated line showed
                # a 'Z' per-line flag yet landed in the Exempt total with Zero-Rated at 0.00.)
                if seller_charges_vat:
                    line = (line / Decimal('1.12')).quantize(cents, ROUND_HALF_UP)
                cls = 'exempt'

            buckets[cls] += line

        # whole-order discount hits every bucket proportionally
        for k in buckets:
            buckets[k] = (buckets[k] * keep).quantize(cents, ROUND_HALF_UP)

        vatable_incl = buckets['vatable']
        vatable_base = (vatable_incl / Decimal('1.12')).quantize(cents, ROUND_HALF_UP)
        return {
            'vatable':      vatable_base,                       # VAT-exclusive VATable sales
            'vat':          vatable_incl - vatable_base,        # the 12%
            'exempt':       buckets['exempt'],
            'zero':         buckets['zero'],
            'vatable_incl': vatable_incl,                       # VAT-inclusive VATable
            'total':        vatable_incl + buckets['exempt'] + buckets['zero'],
        }

    @property
    def amount_paid(self):
        return self.payments.aggregate(t=models.Sum('amount'))['t'] or Decimal('0')
    
    @property
    def settlement_status(self):
        """'unpaid' (utang), 'partial', or 'paid' — drives the per-receipt Method chip."""
        paid = self.amount_paid
        total = self.total_revenue or Decimal('0')
        if paid <= 0:
            return 'unpaid'
        return 'partial' if paid < total else 'paid'

    @property
    def cash_tendered(self):
        """Cash the customer handed over, or None when none was recorded.

        None and zero mean different things here: None is "the cashier didn't record
        a tender", zero would be "they handed over nothing". Only the first happens,
        and the receipt must stay silent for it rather than print CASH 0.00.
        """
        tendered = [p.tendered for p in self.payments.all() if p.tendered is not None]
        return sum(tendered) if tendered else None

    @property
    def cash_change(self):
        """Change handed back, or None when no tender was recorded.

        Summed across payments rather than taken from one: an installment sale can
        have several cash payments, each with its own tender and its own change.
        """
        changes = [p.change_due for p in self.payments.all() if p.tendered is not None]
        return sum(changes) if changes else None

    @property
    def settlement_display(self):
        """Label: 'Debt', a method name (Cash/GCash…), 'Mixed', or 'Partial · X'."""
        status = self.settlement_status
        if status == 'unpaid':
            return 'Debt'
        methods = {p.get_method_display() for p in self.payments.all()}
        label = next(iter(methods)) if len(methods) == 1 else 'Mixed'
        return f'Partial · {label}' if status == 'partial' else label

    @property
    def payment_method_code(self):
        """Which method settled this sale — a single method code (cash/gcash/
        bank/credit), 'mixed' when more than one, or None when nothing's paid
        yet. Drives the payment-method icon in the sales list/detail."""
        methods = {p.method for p in self.payments.all()}
        if not methods:
            return None
        if len(methods) == 1:
            return next(iter(methods))
        return 'mixed'

    @property
    def settlement_badge(self):
        """Paid-status chip data (label / icon / level / amount) shared by the
        Status column and the detail page. Void is handled separately by the
        caller. 'Paid' = settled at the counter (one payment, dated the sale
        day); 'Fully Paid' = installments, mixed methods, or credit cleared on
        a later date."""
        if self.is_fully_paid:
            payments = list(self.payments.all())
            settled_at_counter = len(payments) == 1 and payments[0].date == self.date
            label = 'Paid' if settled_at_counter else 'Fully Paid'
            return {'label': label, 'icon': 'bi-check-circle-fill',
                    'level': 'success', 'amount': None}
        if self.amount_paid > 0:
            return {'label': 'Partial', 'icon': '',
                    'level': 'warning', 'amount': self.amount_paid}
        return {'label': 'Debt', 'icon': 'bi-clock-history', 'level': 'danger', 'amount': None}


    @property
    def amount_refunded(self):
        """Total of all refunds — for net_revenue calc."""
        return self.returns.aggregate(t=models.Sum('refund_total'))['t'] or Decimal('0')

    @property
    def amount_refunded_cash(self):
        """Cash handed back — money that left the drawer. Doesn't reduce outstanding
        (it was already settled; that's the only way cash can be refunded at all).

        Sums the refund_cash COLUMN, not rows whose method == 'cash'. A single return can
        be part credit and part cash, and filtering by the method string would silently
        count the whole of a mixed refund as one or the other.
        """
        return self.returns.aggregate(t=models.Sum('refund_cash'))['t'] or Decimal('0')

    @property
    def amount_refunded_credit(self):
        """Knocked off what the customer owes — no money moved."""
        return self.returns.aggregate(t=models.Sum('refund_credit'))['t'] or Decimal('0')

    @property
    def net_revenue(self):
        """Revenue minus ALL refunds — for accounting / reports. Voided or
        non-completed draft (pending/canceled) counts as 0."""
        if self.is_void or self.status != 'completed':
            return Decimal('0')
        return (self.total_revenue or Decimal('0')) - self.amount_refunded

    @property
    def has_returnable_items(self):
        """False once every unit has already been returned.

        Without this the Return form still opened on a fully-returned sale, showing a
        table where every row said "Fully returned" and no input existed — and submitting
        it just bounced with "Pick at least one item to return." Offer the action only
        when there is something left to act on.
        """
        if self.is_void or self.status != 'completed':
            return False
        return any(i.returnable_quantity > 0 for i in self.sale_items.all())

    @property
    def return_summary(self):
        """Return activity on this sale — None when nothing came back.

        A return is NOT a void. The sale really happened, and its revenue stays in the
        period it was rung up; the refund lands on the RETURN's own date instead (a
        June 29 sale refunded on July 4 reduces JULY). So this never changes what the
        sale was worth — it only says the goods came back later.

        The chip this feeds sits BESIDE the settlement badge, never replacing it.
        "Paid" and "Returned" are INDEPENDENT facts: the customer really did hand over
        the money, and the goods really did come back. Collapsing them into one chip
        throws half the story away — which is exactly why a fully-refunded sale used to
        read as a clean "Paid" row with nothing to show for it.
        """
        if self.is_void or self.status != 'completed':
            return None

        returns = list(self.returns.all())
        if not returns:
            return None

        refunded = sum((r.refund_total or Decimal('0')) for r in returns)
        total    = self.total_revenue or Decimal('0')
        full     = total > 0 and refunded >= total

        return {
            'full':    full,
            # "Partly returned", not "Partial" — the settlement badge already says
            # "Partial" for a part-paid sale, and two chips reading "Partial" side by
            # side would be unreadable.
            'label':   'Returned' if full else 'Partly returned',
            'detail':  'Fully returned' if full else 'Partly returned',
            'amount':  refunded,
            'count':   len(returns),
            # Exactly one return -> the chip can link straight at it. Several -> there's
            # no single row to point to, so the caller lists them on the detail page.
            'only':    returns[0] if len(returns) == 1 else None,
            'returns': returns,
        }

    @property
    def outstanding(self):
        """What customer still owes — voided or draft (pending/canceled) owes
        nothing (a draft was never a real, posted sale)."""
        if self.is_void or self.status != 'completed':
            return Decimal('0')
        return (self.total_revenue or Decimal('0')) - self.amount_paid - self.amount_refunded_credit


    @property
    def is_fully_paid(self):
        return self.outstanding <= Decimal('0')

class SaleItem(models.Model):
    name = models.CharField(max_length=255, null=True, blank=True)
    sale = models.ForeignKey(Sale, on_delete=models.CASCADE, related_name='sale_items', null=True, blank=True)
    product = models.ForeignKey(Product, on_delete=models.SET_NULL, related_name='sale_items', null=True, blank=True)
    price_at_sale = models.DecimalField(max_digits=16, decimal_places=6, null=True, blank=True)
    cost_price = models.DecimalField(max_digits=16, decimal_places=6, default=1.00)
    quantity = models.PositiveIntegerField(default=1)
    unsold_quantity = models.PositiveIntegerField(default=0) # will not be used, I didnt remove it due to migrations
    supplier_name = models.CharField(max_length=150, null=True, blank=True) # snapshot
    
    session = models.ForeignKey(
        'Product.ServiceSession', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='sale_items',
    )
    
    vat_class = models.CharField(max_length=8, null=True, blank=True)   # snapshot of product.vat_class at sale time

    def __str__(self):
        if self.name:
            return f"{self.name} x {self.quantity}"
        return '-'
    def save(self, *args, **kwargs):
        if not self.price_at_sale:
            self.price_at_sale = self.product.selling_price

        if self.product:
            self.name = self.product.name
            if self.session_id:
                self.name = f"{self.product.name} ({self.session.label})"
            if not self.vat_class:                          # NEW — freeze VAT treatment at sale time
                self.vat_class = self.product.vat_class

        super().save(*args, **kwargs)


        
    # def clean(self):
    #     if self.product.prepared_quantity > self.quantity:
    #         raise ValidationError('Quantity should not exceed to prepared quantity.')
    
    @property
    def total_cost_per_item(self):
        return self.cost_price * self.quantity

    @property
    def unsold_product_cost(self):
        return self.cost_price * self.unsold_quantity
    
    @property
    def total_sold_per_item(self):
        return self.price_at_sale * self.quantity

    @property
    def effective_unit_price(self):
        """What the customer ACTUALLY paid for ONE of these.

        `price_at_sale` is the STICKER price. The whole-order discount is stored only on
        the Sale (`discount_percent`) and is never written down onto the line, so a 20%
        order discount means every item is 20% off — spread proportionally, exactly the
        rule Sale.vat_summary() already applies to its VAT buckets.

        Anything that pays money BACK must price the line through here, never through
        `price_at_sale`, or it refunds more than was ever collected. That was a real
        bug: the return form prefilled the sticker price, so a partial return of a
        discounted sale silently over-refunded, and a FULL return totalled more than
        the sale and got rejected by the refund ceiling.

        Rounds DOWN to centavos on purpose: it keeps the sum of the lines at or under
        `total_revenue`, so a full return can always be processed and can never trip the
        `max_refund` guard on a rounding remainder.
        """
        price = self.price_at_sale or Decimal('0')
        pct = (self.sale.discount_percent or Decimal('0')) if self.sale_id else Decimal('0')
        if pct <= 0:
            return price
        keep = (Decimal('100') - pct) / Decimal('100')
        return (price * keep).quantize(Decimal('0.01'), rounding=ROUND_DOWN)

    @property
    def net_sale_value(self):
        return (self.total_sold_per_item) - self.unsold_product_cost
    
    
    @property
    def total_returned_quantity(self):
        return self.return_items.aggregate(
            t=models.Sum('quantity'))['t'] or 0

    @property
    def returnable_quantity(self):
        return self.quantity - self.total_returned_quantity

    
        
# ──────────────────────────────────────────────────────────────
# SALES PAYMENTS — for utang / customer credit tracking (FUTURE)
# Defined now for symmetry; not actively wired in v1.
# ──────────────────────────────────────────────────────────────

class SalesPayment(TimeStampModel):
    PAYMENT_METHOD_CHOICES = [
        ('cash', 'Cash'),
        ('gcash', 'GCash'),
        ('bank', 'Bank Transfer'),
        ('credit', 'Store Credit'),
    
    ]

    sale = models.ForeignKey(Sale, on_delete=models.CASCADE, related_name='payments')
    amount = models.DecimalField(max_digits=16, decimal_places=6)
    date = models.DateField(db_index=True)
    method = models.CharField(max_length=20, choices=PAYMENT_METHOD_CHOICES, default='cash')
    note = models.CharField(max_length=255, blank=True)

    # What the customer physically handed over, cash only. NULL everywhere else — an
    # e-payment is always exact, and NULL (not 0) is what lets the receipt tell "no
    # change line" apart from "handed over exactly the right money".
    #
    # IMPORTANT: Persisted rather than computed at checkout because receipts REPRINT. A reprint
    # that quietly drops the CHANGE line is a different document from the one the
    # customer was handed, which is exactly what an immutable receipt may not be.
    #
    # CRITICAL: This must never reach the drawer. `Shift.expected_cash` sums `amount`, and
    # `amount` stays the sale value — the change went back out of the till, so the net
    # drawer effect of a ₱1000 tender on a ₱474 sale is +₱474, not +₱1000. Keeping the
    # two in separate columns makes that true by construction, not by remembering.
    tendered = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True)
    business = models.ForeignKey(
        BusinessProfile, on_delete=models.SET_NULL,
        related_name='sales_payments', null=True, blank=True)

    created_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='created_sales_payments')
    
    class Meta:
        ordering = ['date', 'created_at']
        
    def __str__(self):
        return f"₱{self.amount} payment for sale {self.sale.reference}"

    @property
    def change_due(self):
        """Cash handed back to the customer, or None when there's no tender to report.

        Derived, never stored — a stored change that disagreed with
        `tendered - amount` would be unexplainable, and one of the two would be lying.

        Works for partial payments too: a ₱500 note against a ₱200 down payment is
        ₱300 change and a ₱300 balance, which are different numbers that happen to
        match here. Clamped at zero because tendering LESS than the payment isn't
        change, it's a shortfall the partial/utang path already models.
        """
        if self.tendered is None:
            return None
        return max(self.tendered - (self.amount or Decimal('0')), Decimal('0'))

    def save(self, *args, **kwargs):
        stamped = []

        if not self.date:
            self.date = timezone.localdate()
            stamped.append('date')

        # Tender only means something for cash. Clearing it on every other method
        # stops a method switch from stranding a stale figure that would then print
        # a CHANGE line on a GCash receipt.
        if self.method != 'cash' and self.tendered is not None:
            self.tendered = None
            stamped.append('tendered')

        # IMPORTANT: A field this method sets itself must be added to update_fields, or the
        # caller's narrow list silently drops it: the value changes in memory and
        # never reaches the database. Same trap as Sale.save() stamping date+reference.
        update_fields = kwargs.get('update_fields')
        if stamped and update_fields is not None:
            kwargs['update_fields'] = list(set(update_fields) | set(stamped))

        super().save(*args, **kwargs)

# ──────────────────────────────────────────────────────────────
# SALES RETURNS — customer returns (defective, changed mind, etc.)
# Per-item triage (resellable → Stock; damaged → Waste).
# ──────────────────────────────────────────────────────────────

class SalesReturnSequence(AbstractDocumentSequence):
    """SRR- series — one continuous run per business."""
    pass

class SalesReturn(TimeStampModel):
    # refund_method is now DERIVED, not chosen (2026-07-12). The refund is split by
    # core.utils.returns.split_refund — debt first, cash second — so a refund that is
    # impossible (cash back on a sale nobody paid for) can't be represented at all.
    # This field is the display summary; refund_cash / refund_credit carry the money.
    REFUND_METHOD_CHOICES = [
        ('cash',   'Cash refund'),
        ('credit', 'Deducted from balance'),
        ('mixed',  'Balance + cash'),
    ]

    REASON_CHOICES = [
        # Real returns
        ('customer_changed_mind', 'Customer changed mind'),
        ('defective',             'Defective'),
        ('wrong_item',            'Wrong item'),
        ('expired',               'Expired'),
        
        # Corrections
        ('amount_correction',     'Amount correction'),
        ('staff_error',           'Staff error'),
        ('other',                 'Other'),
    ]

    original_sale = models.ForeignKey(
        Sale, on_delete=models.PROTECT, related_name='returns')
    
    date = models.DateField(db_index=True)
    reason = models.CharField(max_length=30, choices=REASON_CHOICES, default='customer_changed_mind')
    reason_note = models.CharField(max_length=255, blank=True)
    refund_total = models.DecimalField(max_digits=16, decimal_places=6, default=0)

    # The actual split — refund_total = refund_cash + refund_credit, always.
    #   refund_credit = knocked off what the customer still owes (no money moves)
    #   refund_cash   = money physically handed back
    # A cash figure can only be non-zero once the balance is settled. See split_refund().
    refund_cash   = models.DecimalField(max_digits=16, decimal_places=6, default=0)
    refund_credit = models.DecimalField(max_digits=16, decimal_places=6, default=0)

    # Derived from the split above — for badges only. Never trust it for money.
    refund_method = models.CharField(max_length=20, choices=REFUND_METHOD_CHOICES, default='cash')
    reference = models.CharField(max_length=255, blank=True)  # # auto-generated SRR-0000000001

    business = models.ForeignKey(BusinessProfile, on_delete=models.SET_NULL,
        related_name='sales_returns', null=True, blank=True)
    
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, 
        related_name='created_sales_returns', null=True, blank=True)
        
    class Meta:
        ordering = ['-date', '-created_at']

    def __str__(self):
        return f"{self.reference or '(unsaved)'} — ₱{self.refund_total}"

    def save(self, *args, **kwargs):
        if not self.date:
            self.date = timezone.localdate()

        if not self.reference and self.business:
            self.reference, _, _ = SalesReturnSequence.issue(self.business, 'SRR')
            
        super().save(*args, **kwargs)
        
class SalesReturnItem(models.Model):
    sales_return = models.ForeignKey(SalesReturn,
        on_delete=models.CASCADE, related_name='items')
    
    original_sale_item = models.ForeignKey(SaleItem, on_delete=models.SET_NULL,
        related_name='return_items', null=True, blank=True,)  
    
    name = models.CharField(max_length=255)  # snapshot
    quantity = models.PositiveIntegerField(default=1)
    unit_refund = models.DecimalField(max_digits=16, decimal_places=6, default=0)
    resellable = models.BooleanField(default=True)  # True → Stock; False → Waste

    def __str__(self):
        return f"{self.name} × {self.quantity}"

    @property
    def line_total(self):
        return self.unit_refund * self.quantity
    
class SaleEmployee(TimeStampModel):
    """
    Tracks which employees worked during a sale session.
    Currently used for labor / salary cost tracking in summary/dashboard.
    NOTE: Shift assignment will move to a shared cart flow in Phase 2.
    For now, owner logs shift manually after confirming sale.
    """
    
    name = models.CharField(max_length=255, null=True, blank=True)
    sale = models.ForeignKey(Sale, on_delete=models.CASCADE, related_name='sale_employees', null=True, blank=True)
    employee = models.ForeignKey(Employee, on_delete=models.SET_NULL, related_name='sale_employees', null=True, blank=True)
    daily_rate = models.DecimalField(max_digits=10, decimal_places=2)
    
    def __str__(self):
        if self.name:
            return f"Sale Record ID: #{self.sale.id} - {self.name}"
        return 'No employee info'

    def save(self, *args, **kwargs):
        if self.employee:
            self.name = self.employee.name

        super().save(*args, **kwargs)


# ──────────────────────────────────────────────────────────────
# Z READING — the sealed End-of-Day (BIR) record.
# The computed reading (core.utils.reading.compute_reading) is the X — read-only,
# recomputable, seals nothing. Sealing FREEZES one past business day into an
# append-only ZReading with a burned Z counter, so it can never restate.
# ──────────────────────────────────────────────────────────────

class ZReadingSequence(AbstractDocumentSequence):
    """Z- series — the Z counter. One continuous, per-business run of sealed Z readings.

    Annex D-2 prints a "Z Counter" on every reading; this is it. Separate from the SI /
    VOID / RETURN runs — a Z consumes none of those, it is its own kind of accountable
    document (the end-of-day seal)."""
    pass


def _jsonable(value):
    """Recursively convert a compute_reading() dict into JSON-storable primitives.

    Decimals become strings (never floats — money must survive the round-trip exactly),
    dicts/lists recurse, everything else passes through. The frozen snapshot is rendered
    straight back through the same template, where `floatformat`/`intcomma` accept the
    numeric strings unchanged — so a sealed Z and a live X render identically."""
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return value


class ZReading(TimeStampModel):
    """An append-only sealed Z reading for ONE business day. Pen, not pencil.

    Freezes the ENTIRE compute_reading() output as a JSON snapshot, so a sealed day
    renders exactly as it was sealed even if a product's cost, a discount, or anything
    else is edited afterwards — same guarantee, same reasoning, as DailyClose. The
    scalar columns are denormalized copies of the snapshot's headline figures so the
    list can show them without deserializing JSON per row.

    ONE Z per business day (unique business+date). A Z is sealed only AFTER the day is
    over (`date < today`): sealing freezes the accumulated-total odometer at `Present`,
    and a sale rung into an already-sealed day would push the next day's `Previous` past
    it, tearing the Z-to-Z continuity an examiner checks. Today stays a live X preview.
    """
    business = models.ForeignKey(BusinessProfile, on_delete=models.CASCADE,
                                 related_name='z_readings')
    date     = models.DateField(db_index=True)

    z_counter = models.PositiveBigIntegerField()             # the Annex "Z Counter"
    reference = models.CharField(max_length=100)             # 'Z-0000000001' snapshot

    # Denormalized headline figures (authoritative copy lives in `snapshot`).
    gross                = models.DecimalField(max_digits=16, decimal_places=2, default=0)
    net_amount           = models.DecimalField(max_digits=16, decimal_places=2, default=0)
    present_accumulated  = models.DecimalField(max_digits=16, decimal_places=2, default=0)
    previous_accumulated = models.DecimalField(max_digits=16, decimal_places=2, default=0)
    sales_for_day        = models.DecimalField(max_digits=16, decimal_places=2, default=0)
    transaction_count    = models.PositiveIntegerField(default=0)
    void_count           = models.PositiveIntegerField(default=0)
    return_count         = models.PositiveIntegerField(default=0)
    is_vat_registered    = models.BooleanField(default=False)

    # The frozen full reading (Decimals stored as strings — see _jsonable).
    snapshot = models.JSONField(default=dict)

    sealed_at = models.DateTimeField(auto_now_add=True)
    sealed_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True,
                                  related_name='z_readings_sealed')

    class Meta:
        ordering = ['-date']
        constraints = [
            models.UniqueConstraint(fields=['business', 'date'],
                                    name='uniq_zreading_business_date'),
        ]
        indexes = [models.Index(fields=['business', '-date'])]

    def __str__(self):
        return f"Z-{self.z_counter} {self.business} {self.date} — net {self.net_amount}"

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValueError("ZReading is append-only — a sealed Z cannot be modified.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError("ZReading is append-only — a sealed Z cannot be un-sealed.")

    def reading_context(self):
        """The frozen snapshot as the template's `reading` dict (strings render fine)."""
        return self.snapshot

    @classmethod
    def for_day(cls, business, day):
        """The sealed Z for this business-day, or None if the day is still a live X."""
        if day is None:
            return None
        return cls.objects.filter(business=business, date=day).first()

    @classmethod
    def earliest_unsealed_day(cls, business):
        """The oldest FINISHED trading day (has a real sale, before today) not yet sealed,
        or None if every finished trading day is sealed.

        Trading days are defined exactly as the Z-reading LIST defines them — a day with at
        least one completed, non-void sale — so the guard never points at a day the owner
        can't actually reach and seal (e.g. a day whose only sales were all voided)."""
        today = timezone.localdate()
        trading_days = set(
            Sale.objects.filter(business=business, status='completed', is_void=False,
                                 date__lt=today)
            .values_list('date', flat=True).distinct()
        )
        sealed = set(cls.objects.filter(business=business).values_list('date', flat=True))
        unsealed = trading_days - sealed
        return min(unsealed) if unsealed else None

    @classmethod
    def seal(cls, business, day, *, user=None):
        """Seal `day` into an append-only Z. Returns (zreading, created); idempotent.

        Raises ValueError if `day` is today/future (only a finished day can be sealed) or if
        an OLDER trading day is still unsealed — Z readings must be sealed in date order so
        the Z counter runs chronologically, the way an examiner expects. Race-safe: the
        unique (business, date) constraint is the real guard, and a lost race rolls back
        inside the atomic block so no Z counter is burned."""
        from django.db import transaction, IntegrityError
        from core.utils.reading import compute_reading

        today = timezone.localdate()
        if day >= today:
            raise ValueError(
                "A Z reading can only be sealed after the business day is over — "
                "today is still trading, so its reading is an X, not a Z."
            )

        existing = cls.for_day(business, day)
        if existing:
            return existing, False

        earliest = cls.earliest_unsealed_day(business)
        if earliest is not None and day != earliest:
            raise ValueError(
                f"Z readings must be sealed in date order — seal "
                f"{earliest:%b %d, %Y} first."
            )

        reading = compute_reading(business, day)
        snapshot = _jsonable({k: v for k, v in reading.items()
                              if k not in ('business', 'day')})
        try:
            with transaction.atomic():
                reference, z_counter, _ = ZReadingSequence.issue(business, 'Z')
                zr = cls.objects.create(
                    business=business, date=day,
                    z_counter=z_counter, reference=reference,
                    gross=reading['gross'], net_amount=reading['net_amount'],
                    present_accumulated=reading['present_accumulated'],
                    previous_accumulated=reading['previous_accumulated'],
                    sales_for_day=reading['sales_for_day'],
                    transaction_count=reading['transaction_count'],
                    void_count=reading['void_count'],
                    return_count=reading['return_count'],
                    is_vat_registered=reading['is_vat_registered'],
                    snapshot=snapshot, sealed_by=user,
                )
            return zr, True
        except IntegrityError:
            # Lost the race — the other seal's numbers are authoritative; our counter
            # burn was rolled back with the failed create, so no gap in the Z run.
            return cls.for_day(business, day), False