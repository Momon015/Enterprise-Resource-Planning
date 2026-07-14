/* ═══════════════════════════════════════════════════════════════════════════
   KPI CARDS — the one and only handler for the ▼ breakdown popovers and for
   card-body navigation. Loaded globally from main.html.

   ★★ THIS FILE MUST STAY THE ONLY COPY. ★★
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
     • .kpi-value                        → money is shortened (see below)
   ═══════════════════════════════════════════════════════════════════════════ */
(function () {
  'use strict';

  /* ─────────────────────────────────────────────────────────────────────────
     MONEY ON A KPI CARD — shortened only when it would actually break.

     ★ A KPI card is the money ITSELF, not a ruler. That's why this is NOT the
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

  // The ▼ and the popover itself must NEVER navigate — otherwise opening a
  // breakdown would bounce you to the filtered list instead of showing it.
  const isChrome = (el) => el.closest('.kpi-peek') || el.closest('.kpi-popover');

  document.addEventListener('click', (e) => {
    const btn = e.target.closest('[data-kpi-popover]');
    const open = openCard();

    if (btn) {
      e.stopPropagation();
      const card = btn.closest('.kpi-card');
      if (open && open !== card) open.classList.remove('popover-open');
      card.classList.toggle('popover-open');
      return;
    }

    // A click anywhere outside the open popover closes it.
    if (open && !e.target.closest('.kpi-popover')) open.classList.remove('popover-open');

    const card = e.target.closest('.kpi-card--clickable[data-href]');
    if (card && !isChrome(e.target)) window.location = card.dataset.href;
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
      if (card) window.location = card.dataset.href;
    }
  });
})();
