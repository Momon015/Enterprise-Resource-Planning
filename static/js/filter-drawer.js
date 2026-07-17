/**
 * Shared filter-drawer + toast behavior for list pages.
 * Loaded once in main.html and runs on every page.
 *
 * Behaviors:
 *  1. Auto-dismiss .toast-message after 3.2s.
 *  2. Open the <details class="filter-card"> drawer if search/category filter is active.
 *  3. Stop click events inside the filter form from bubbling up and closing the drawer.
 *  4. Stop the reset button (a.btn-light inside the form) from doing the same.
 *  5. Flag empty <input type="month"> fields so CSS can show "Select a month".
 *  6. Open the native month dropdown on click, not just on the calendar icon.
 *  7. Hand the native segments back the moment someone types into a month field.
 */

// ── 5. MONTH PLACEHOLDER ─────────────────────────────────────────────────────
// A month input has no placeholder attribute; empty, Chrome paints "--------- ----".
// style.css replaces that mask with a real label, but it needs to know when the field
// is empty and CSS cannot work that out on its own (see the note beside the rule).
// So: one class, kept in sync with the input's LIVE value.
function syncMonthPlaceholders() {
  document.querySelectorAll('input[type="month"]').forEach(input => {
    input.classList.toggle("is-empty", !input.value);
    // A completed value ends the typing session (see behavior 7). Without this, someone who
    // typed a month and then cleared it from the picker would get a bare mask and no label
    // until they clicked away.
    if (input.value) input.classList.remove("is-typing");
  });
}

// Scan on every input/change rather than only on month fields: the list pages clear
// select_month PROGRAMMATICALLY when a date range is typed (the mutually-exclusive
// date rule), and assigning .value fires no event of its own. Watching only the month
// input would leave the label off a field that just became empty.
// queueMicrotask so this lands after the handler that did the clearing, whichever
// order the two listeners happen to be registered in.
["input", "change"].forEach(evt =>
  document.addEventListener(evt, () => queueMicrotask(syncMonthPlaceholders))
);

// Live filter regions are replaced wholesale on every filter/paginate, so the fresh
// inputs arrive unclassed. Re-scan after a swap.
document.addEventListener("htmx:afterSwap", syncMonthPlaceholders);

// ── 6. DATE + MONTH FIELDS OPEN THEIR DROPDOWN ───────────────────────────────
// By default only the little calendar icon opens the picker; clicking the field itself
// just drops a caret into a text segment and waits for you to TYPE. That is the wrong
// offer for a filter — nobody wants to type "07" — and it's what put the segment mask on
// screen in the first place. Click anywhere in the field and you get the calendar, which
// is what these fields look like they promise. Both types: the month filter and the
// From/To range read as one control to the user and must behave alike.
//
// Typing still works: this only adds a way in, it takes none away. Keyboard users tab to
// the field and type as before (the "Select a month" label steps aside on focus so the
// segments are visible — see the :not(:focus) note in style.css).
//
// Delegated, like everything else here: the live filter regions are replaced wholesale on
// every filter, so a listener bound to the inputs themselves would be discarded on the
// first swap and the field would quietly go back to its old behavior.
//
// ★★ CAPTURE PHASE (the `true`) IS LOAD-BEARING — this silently did NOTHING without it. ★★
// Behavior 3 below does filterForm.addEventListener("click", e => e.stopPropagation()), and
// every month field lives INSIDE #filterForm. In the bubble phase the click therefore dies
// at the form and never reaches document, so this handler was never called: the field still
// focused and still showed the native mask (both are the browser, not us), which looks
// exactly like a picker that "didn't open" rather than like a listener that never ran.
// Capture runs on the way DOWN to the target, before any bubble-phase stopPropagation.
document.addEventListener("click", function (e) {
  const input = e.target.closest && e.target.closest('input[type="month"], input[type="date"]');
  if (!input || input.disabled || input.readOnly) return;
  // Firefox has no month picker at all and renders that field as plain text; guard rather
  // than assume. (It does have a date picker — this just lets it say so for itself.)
  if (typeof input.showPicker !== "function") return;
  try {
    input.showPicker();
  } catch (err) {
    // Throws without user activation, or if the browser refuses. A click IS activation,
    // so this is the belt-and-braces path: never let it break the rest of the handler.
  }
}, true);

// ── 7. TYPING BEATS THE LABEL ────────────────────────────────────────────────
// "Select a month" sits ON TOP of the field's native segments, which are hidden while it
// shows (style.css). That's right for the mouse — click opens the dropdown, the segments
// are never used. But someone can still TAB in and type, and they must not type blind:
// a month input's .value stays "" until BOTH segments are filled, so the label would hang
// there through the whole entry and only vanish on the last keystroke.
// So the first real keypress hands the segments straight back.
// Capture again: these fields live inside #filterForm, which stops click — and keeping
// both listeners on the same phase means one rule to remember, not two.
document.addEventListener("keydown", function (e) {
  const input = e.target.closest && e.target.closest('input[type="month"]');
  if (!input) return;
  // Navigation and dismissal aren't entry — don't tear the label down for them.
  if (["Tab", "Escape", "Enter", "Shift"].includes(e.key)) return;
  input.classList.add("is-typing");
}, true);

// Leaving the field ends the typing session: if it's still empty it goes back to showing
// the label, rather than staying a bare mask forever because of one stray keystroke.
// blur doesn't bubble, so capture is the only way to delegate it.
document.addEventListener("blur", function (e) {
  const input = e.target.closest && e.target.closest('input[type="month"]');
  if (input) input.classList.remove("is-typing");
}, true);

document.addEventListener("DOMContentLoaded", function () {
  syncMonthPlaceholders();

  // Auto-dismiss toasts
  document.querySelectorAll(".toast-message").forEach(t =>
    setTimeout(() => t.remove(), 3200)
  );

  // Filter drawer logic — only if the page has one
  const filterDetails = document.querySelector("details.filter-card");
  if (!filterDetails) return;

  const filterForm = filterDetails.querySelector("#filterForm");
  if (!filterForm) return;

  // Open the drawer if ANY common filter param is in the URL.
  // (Pages without a given filter just have the param absent — no harm.)
  const FILTER_PARAMS = [
    "search", "category", "start_date", "end_date",
    "select_month", "reason", "status",  // stock
  ];
  const params = new URLSearchParams(window.location.search);
  if (FILTER_PARAMS.some(p => params.get(p))) {
    filterDetails.setAttribute("open", "");
  }

  filterForm.addEventListener("click", function (e) {
    e.stopPropagation();
  });

  const resetBtn = filterForm.querySelector("a.btn-light");
  if (resetBtn) {
    resetBtn.addEventListener("click", e => e.stopPropagation());
  }
});
