HIGH_STOCK_THRESHOLD = 50 # matches your inventory view's "high" filter
LOW_STOCK_THRESHOLD = 25 # matches your inventory view's "low" filter
NO_STOCK_THRESHOLD = 0 # matches your inventory view's "no stock" filter

# "Almost gone" — the urgent SUBSET of low (a critical item is ALSO low, and ?stock=low
# still lists it). Products compute this per-item from their own low_stock_threshold
# (see Product.critical_stock_threshold / CRITICAL_THRESHOLD_EXPR); material Stock has no
# per-item threshold yet, so it falls back to this global — same rule, 20% of low, floor 1.
CRITICAL_STOCK_THRESHOLD = max(1, round(LOW_STOCK_THRESHOLD * 0.2))  # = 5

MARGIN_DEFAULT_TARGET = 30  # global default target margin % (product & category both unset)
MARGIN_DANGER_FLOOR   = 10  # margin % below this = critical (red badge + margin_low event)

# ── KPI / dashboard cache freshness ──────────────────────────────────────
# Bust-on-write does the real freshness; TTL is just the missed-signal backstop.
KPI_CACHE_TTL    = 60 * 30   # 30 min
KPI_BUST_DEBOUNCE = 60       # seconds — a burst of writes within this window = 1 bust
