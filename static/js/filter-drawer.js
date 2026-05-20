/**
 * Shared filter-drawer + toast behavior for list pages.
 * Loaded once in main.html and runs on every page.
 *
 * Behaviors:
 *  1. Auto-dismiss .toast-message after 3.2s.
 *  2. Open the <details class="filter-card"> drawer if search/category filter is active.
 *  3. Stop click events inside the filter form from bubbling up and closing the drawer.
 *  4. Stop the reset button (a.btn-light inside the form) from doing the same.
 */
document.addEventListener("DOMContentLoaded", function () {
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
    "select_month", "stock", "period",
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
