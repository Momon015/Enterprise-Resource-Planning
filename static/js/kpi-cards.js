/* ═══════════════════════════════════════════════════════════════════════════
   KPI CARDS — the one and only handler for the ▼ breakdown popovers and for
   card-body navigation. Loaded globally from main.html.

   THIS FILE MUST STAY THE ONLY COPY.
   Everything below is a DELEGATED listener on `document`, so a second copy on
   a page does NOT simply "win" — both copies hear the same click, both call
   classList.toggle('popover-open'), and they cancel each other out. The
   popover opens and shuts in the same millisecond, every dropdown in the app
   goes dead, and NOTHING is logged, because each copy is correct on its own.
   That is exactly the bug this file was created to retire (it used to be
   pasted into 6 templates). If you ever page-scope this again, delete this
   one in the same commit.

   Both behaviours are driven purely by markup, so a page opts in by rendering
   the attributes and opts out by not rendering them — no per-page wiring:
     • [data-kpi-popover]                → the ▼ button; toggles its card
     • .kpi-card--clickable[data-href]   → the card body navigates
     • ...plus hx-get                    → htmx swaps a live list region INSTEAD
                                           of navigating, and this file stands
                                           down (product_list's stock cards)
     • .kpi-value                        → money is shortened (see below)
   ═══════════════════════════════════════════════════════════════════════════ */
(function () {
  'use strict';

  /* ─────────────────────────────────────────────────────────────────────────
     MONEY ON A KPI CARD — shortened only when it would actually break.

       A KPI card is the money ITSELF, not a ruler. That's why this is NOT the
       same rule as the chart axes (pesoAxis, which abbreviates from ₱1k up):
       "₱9.9k" on a Net Cash card hides whether you took ₱9,947.80 or ₱9,999 —
       an ₱800 swing behind a rounded headline, on the one screen an owner
       checks daily. Charts can round. The money cannot.

       So this stays EXACT until the number would genuinely overflow the card:

         under ₱100k   →  ₱9,947.80        untouched (server already exact)
         ₱100k–999k    →  ₱100,203         exact, centavos dropped (noise here)
         ₱1M and up    →  ₱1.23M           abbreviated — the real overflow point

     The exact figure is never lost: it goes into `data-exact` and the `title`,
     so hovering the value shows it, and the ▼ dropdown (rendered server-side by
     Django) was always exact and is untouched by any of this.

     Skips anything that isn't pesos — counts ("65"), percentages ("82.2%") and
     em-dash placeholders are left alone by the leading-₱ test.
     ───────────────────────────────────────────────────────────────────────── */
  const PESO = '₱';
  const EXACT_BELOW = 1e5;   // under this, don't touch it at all
  const DROP_CENTAVOS_BELOW = 1e6;

  function trimNum(x) {
    // 1.23 → "1.23" · 1.20 → "1.2" · 1.00 → "1" · 12.45 → "12.5"
    const s = x >= 10 ? x.toFixed(1) : x.toFixed(2);
    return s.replace(/\.?0+$/, '');
  }

  function shorten(n) {
    const abs = Math.abs(n);
    const sign = n < 0 ? '−' : '';
    if (abs >= 1e9) return sign + PESO + trimNum(abs / 1e9) + 'B';
    if (abs >= 1e6) return sign + PESO + trimNum(abs / 1e6) + 'M';
    // ₱100k–999k: still exact, just without the centavos.
    return sign + PESO + Math.round(abs).toLocaleString('en-PH');
  }

  function formatMoney(root) {
    (root || document).querySelectorAll('.kpi-value').forEach(function (el) {
      if (el.dataset.moneyDone) return;          // idempotent — htmx may re-run this
      el.dataset.moneyDone = '1';

      const raw = el.textContent.trim();
      if (raw.indexOf(PESO) !== 0) return;       // not money → leave it alone

      const n = Number(raw.replace(/[₱,\s−]/g, ''));
      if (!isFinite(n)) return;
      if (Math.abs(n) < EXACT_BELOW) return;     // small enough to show in full

      el.dataset.exact = raw;
      el.title = raw;                            // hover still gives the true figure
      el.textContent = shorten(n);
    });
  }

  document.addEventListener('DOMContentLoaded', function () { formatMoney(); });
  // A KPI strip can arrive in an htmx swap; format whatever just landed.
  document.body && document.body.addEventListener('htmx:afterSwap', function (e) {
    formatMoney(e.target);
  });

  const openCard = () => document.querySelector('.kpi-card.popover-open');
  const closeOpen = () => {
    const open = openCard();
    if (open) open.classList.remove('popover-open');
  };
  const togglePopover = (card) => {
    const open = openCard();
    if (open && open !== card) open.classList.remove('popover-open');
    card.classList.toggle('popover-open');
  };

  // The ▼ and the popover itself must NEVER navigate — otherwise opening a
  // breakdown would bounce you to the filtered list instead of showing it.
  const isChrome = (el) => el.closest('.kpi-peek') || el.closest('.kpi-popover');

  // A card that carries hx-get has handed navigation to htmx (it swaps a live list
  // region in place instead of loading a page — see product_list.html's KPI strip).
  // Without this, BOTH would fire on the same click: htmx would fetch and swap while
  // window.location tore the page down underneath it, and the full reload would win.
  // The visible result is "the live filter doesn't work", with the swap that did happen
  // discarded too fast to see. Markup-driven like everything else here: a card opts out
  // by rendering hx-get, so the dashboard's cards (no hx-get, they navigate to other
  // pages) are untouched.
  const htmxOwns = (card) => card.hasAttribute('hx-get');

  // ── The ▼ on a card that ALSO drives htmx (Stock Levels' KPI strip) ──────────
  // When a card carries hx-get, htmx binds its click trigger to the CARD element. The ▼
  // button is a CHILD of that card, so a click on it bubbles up to the card and fires the
  // card's hx-get — filtering the list instead of opening the breakdown. The bubble handler
  // below cannot stop that: htmx's listener is on the card (deeper in the tree) and runs
  // during bubbling BEFORE this document-level one, so by the time we could stopPropagation
  // the request is already away.
  //
  // So intercept in the CAPTURE phase, which runs before any element-level listener. Only for
  // cards that own BOTH hx-get and a ▼ — i.e. only Stock Levels today. Everywhere else this
  // is inert: dashboard cards have a ▼ but no hx-get (htmxOwns false), product_list cards have
  // hx-get but no ▼ (no .kpi-peek match), so both fall straight through to the bubble handler
  // untouched. We do the toggle here and stop the click before it reaches htmx; a click inside
  // an already-open popover is shielded the same way (no navigation) but does not toggle.
  document.addEventListener('click', (e) => {
    if (!e.target.closest) return;
    const chrome = e.target.closest('.kpi-peek, .kpi-popover');
    if (!chrome) return;
    const card = chrome.closest('.kpi-card');
    if (!card || !htmxOwns(card)) return;   // non-htmx cards: bubble handler is correct
    e.stopPropagation();                    // shield htmx — the ▼ must never filter
    if (e.target.closest('[data-kpi-popover]')) togglePopover(card);
  }, true);

  document.addEventListener('click', (e) => {
    const btn = e.target.closest('[data-kpi-popover]');
    const open = openCard();

    if (btn) {
      e.stopPropagation();
      togglePopover(btn.closest('.kpi-card'));
      return;
    }

    // A click anywhere outside the open popover closes it.
    if (open && !e.target.closest('.kpi-popover')) open.classList.remove('popover-open');

    const card = e.target.closest('.kpi-card--clickable[data-href]');
    if (card && !isChrome(e.target) && !htmxOwns(card)) window.location = card.dataset.href;
  });

  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
      closeOpen();
      return;
    }
    if (e.key === 'Enter') {
      // Guard the ▼ here too: it is a real <button>, so Enter already fires a
      // click on it (which toggles the popover). Without this guard the card
      // would ALSO navigate, and the breakdown you just asked for would vanish
      // as the page changed under you.
      if (!e.target.closest) return;
      if (isChrome(e.target)) return;
      const card = e.target.closest('.kpi-card--clickable[data-href]');
      if (!card) return;
      // htmx's default trigger for a plain element is `click`, which a keypress does not
      // produce — so an hx-get card would fall through to window.location here and quietly
      // give keyboard users the full reload the mouse path just stopped doing. Synthesise
      // the click instead: htmx hears it, and the guard above stops us handling it twice.
      if (htmxOwns(card)) { card.click(); return; }
      window.location = card.dataset.href;
    }
  });
})();
