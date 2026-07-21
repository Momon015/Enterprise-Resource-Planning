import { StrictMode, useEffect, useState } from 'react'
import { createRoot } from 'react-dom/client'

// ── helpers ───────────────────────────────────────────────
function getCookie(name) {
  const m = document.cookie.match('(^|;)\\s*' + name + '\\s*=\\s*([^;]+)')
  return m ? m.pop() : ''
}
const peso = n =>
  Number(n).toLocaleString('en-PH', { minimumFractionDigits: 2, maximumFractionDigits: 2 })

// First two letters of the name — the app-wide no-image avatar (see project_no_image_initials).
// A product with a real photo keeps it; only the no-photo case falls back here instead of the
// grey no-image placeholder.
function initials(name) {
  const parts = (name || '').replace(/[^A-Za-z0-9\s-]/g, '').split(/[\s-]+/).filter(Boolean)
  if (parts.length === 0) return '?'
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase()
  return (parts[0][0] + parts[1][0]).toUpperCase()
}

// config the Django template handed us via data-* attributes
const el = document.getElementById('sale-cart-root')
const CFG = el ? el.dataset : {}
const BASE = CFG.apiBase || ''
const URLS = {
  state:  BASE,
  qty:    BASE + 'qty/',
  price:  BASE + 'price/',
  remove: BASE + 'remove/',
  clear:  BASE + 'clear/',
}

// ── Statutory discount BANDS (SC / PWD / NAAC / Solo Parent) ───────────────────────
// One entry per legal (type, rate) pair — MUST mirror Sale.STATUTORY_BANDS in
// Sales/models.py. SC and PWD each carry TWO bands: 20% + VAT-exempt on most goods, and
// 5% on DTI/DA basic necessities (groceries) where the VAT is KEPT and the 5% comes off
// the gross. These exist only so the cashier sees the right number BEFORE committing; the
// server recomputes everything on confirm via Sale.price_breakdown(). If the two drift the
// server wins and the customer sees a different total than the screen promised — change
// them together. Order here is the dropdown order (highest-relief band first).
//
// IMPORTANT: VAT exemption follows the BAND, not the rate alone: NAAC is 20% but keeps its
// VAT, and SC/PWD at 5% keep it too. `note` distinguishes the two same-name bands.
// Labels are the FULL name + abbreviation ("Senior Citizen (SC)") — the cart has room and the
// cashier wants clarity. The RECEIPT prints the bare abbreviation instead (Sale.get_discount_
// type_display → "SC"), where 58mm thermal width is tight; the two surfaces diverge on purpose.
const STATUTORY_OPTIONS = [
  { type: 'sc',          rate: 20, vatExempt: true,  label: 'Senior Citizen (SC)',          note: '' },
  { type: 'sc',          rate: 5,  vatExempt: false, label: 'Senior Citizen (SC)',          note: 'basic necessities' },
  { type: 'pwd',         rate: 20, vatExempt: true,  label: 'Person with Disability (PWD)', note: '' },
  { type: 'pwd',         rate: 5,  vatExempt: false, label: 'Person with Disability (PWD)', note: 'basic necessities' },
  { type: 'solo_parent', rate: 10, vatExempt: true,  label: 'Solo Parent (SP)',             note: '' },
  { type: 'naac',        rate: 20, vatExempt: false, label: 'National Athlete (NAAC)',      note: '' },
]
// The default (first-listed, highest-relief) band for a type — the fallback when a stored
// rate doesn't match a legal band.
const defaultRateFor = type => {
  const first = STATUTORY_OPTIONS.find(o => o.type === type)
  return first ? first.rate : 0
}

// POST form-encoded data, get back the fresh cart JSON
async function post(url, body) {
  const res = await fetch(url, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/x-www-form-urlencoded',
      'X-CSRFToken': getCookie('csrftoken'),   // Django CSRF protection
    },
    body: new URLSearchParams(body).toString(),
  })
  return res.json()
}

// ── one product line ──────────────────────────────────────
function CartRow({ item, onQty, onPrice, onRemove, onToast }) {
  const [editing, setEditing] = useState(false)
  const [priceDraft, setPriceDraft] = useState(item.line_total)

  // one place that decides the new qty + warns if it would exceed stock
  const setQty = (v) => {
    if (!item.is_service && v > item.stock) {
      onToast(`${item.name} — only ${item.stock} in stock.`, 'warning', 'stock')
      v = item.stock                    // cap it
    }
    if (v < 1) v = 1
    if (v === item.quantity) return     // nothing actually changed → skip server
    onQty(item.id, v)
  }

  

  return (
    <div className="sale-row">
      {item.image
        ? <img className="sale-row-thumb" src={item.image} alt="" />
        : <div className="sale-row-thumb sale-row-thumb--initials">{initials(item.name)}</div>}
      <div className="sale-row-body">
        <div className="sale-row-head">
          <div className="sale-row-info">
            <div className="sale-row-name">{item.name}</div>
            {item.supplier && <div className="sale-row-sub">{item.supplier}</div>}
            <div className="sale-row-cost">₱{item.selling_price} each</div>
          </div>
          <button className="sale-row-remove" title="Remove" onClick={() => onRemove(item.id)}>
            <i className="bi bi-x-lg"></i>
          </button>
        </div>

        <div className="sale-row-controls">
          <div className="qty-stepper">
            <button className="qty-btn" disabled={item.quantity <= 1}
                    onClick={() => setQty(item.quantity - 1)}>−</button>
            <input className="qty-input" type="number" min="1" value={item.quantity}
                   onChange={e => setQty(parseInt(e.target.value || '1', 10))} />
            <button className="qty-btn"
                    onClick={() => setQty(item.quantity + 1)}>+</button>
          </div>

          <div className="sale-row-price">
            {editing ? (
              <span style={{ display:'flex', alignItems:'center', gap:'.35rem' }}>
                <div className="row-price-chip">
                  <span style={{ color:'var(--muted)', fontSize:'.85rem' }}>₱</span>
                  <input className="num" type="number" step="0.01" min="0" autoFocus
                         value={priceDraft} onChange={e => setPriceDraft(e.target.value)} />
                </div>
                <button className="apply-check-btn" title="Save price"
                        onClick={() => { onPrice(item.id, priceDraft); setEditing(false) }}>
                  <i className="bi bi-check2"></i>
                </button>
              </span>
            ) : (
              <div className="cost-display">
                <span className="sale-row-total">₱{item.line_total}</span>
                <button className="edit-btn" title="Edit price"
                        onClick={() => { setPriceDraft(item.line_total); setEditing(true) }}>
                  <i className="bi bi-pencil-square"></i>
                </button>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}

// ── reusable confirm modal — matches your .quickview / .cm-* styles ──
function ConfirmModal({ title, note, tone = 'danger', icon = 'bi-trash',
                        label = 'Confirm', btnIcon = 'bi-trash-fill', onConfirm, onCancel }) {
  return (
    <div className="quickview is-open" style={{ alignItems:'center', padding:'2rem 1rem' }}>
      <div className="quickview__backdrop" onClick={onCancel}></div>
      <div style={{ position:'relative', zIndex:1, width:'100%', maxWidth:'460px' }}>
        <div className="quickview__panel cm-panel">
          <button type="button" className="quickview__close" onClick={onCancel} aria-label="Close">
            <i className="bi bi-x-lg"></i>
          </button>
          <div className="card cm-card">
            <div className="card-body">
              <div className="cm-head">
                <div className={`cm-thumb cm-thumb--${tone}`}><i className={`bi ${icon}`}></i></div>
                <div><div className="cm-title">{title}</div></div>
              </div>
              {note && <p className={`cm-note cm-note--${tone}`}>{note}</p>}
              <div className="cm-actions">
                <button type="button" className="btn btn-outline-secondary" onClick={onCancel}>Keep Items</button>
                <button type="button" className={`btn cm-btn cm-btn--${tone}`} onClick={onConfirm}>
                  <i className={`bi ${btnIcon}`}></i> {label}
                </button>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}

// ── the whole cart ────────────────────────────────────────
function SaleCart() {
  const [clearing, setClearing] = useState(false)
  const [page, setPage] = useState(1)
  const [toasts, setToasts] = useState([])
  const [cart, setCart] = useState(null)      // null = still loading
  const [discount, setDiscount] = useState(0)
  const [discountType, setDiscountType] = useState('')     // '' = regular customer
  const [discountRate, setDiscountRate] = useState(0)      // which statutory BAND (20 vs 5)
  const [discountIdNo, setDiscountIdNo] = useState('')     // OSCA / PWD / PNSTM / SP no.
  const [discountName, setDiscountName] = useState('')     // the ID holder, not the payer

  // Load on mount — AND again whenever the cart is mutated from outside this island.
  //
  // The topbar search's "+" button is plain htmx: it POSTs to add-to-sale and swaps the
  // cart badge. That happens entirely outside React, so the island used to sit on the
  // state it fetched at mount and show a stale cart (most visibly: the "No products yet"
  // empty state, still there after you'd just added something). You had to refresh.
  //
  // main.html fires `cart:changed` on any htmx swap of the cart badge — i.e. on any cart
  // mutation, whatever triggered it — so this re-reads the truth instead of trying to
  // guess what changed.
  useEffect(() => {
    const load = () =>
      fetch(URLS.state).then(r => r.json()).then(data => {
        setCart(data)
        setDiscount(parseFloat(data.discount_percent) || 0)
        // The customer is server state too — clicking Edit on the summary re-mounts this
        // component, and without restoring these the cart came back claiming "Regular
        // customer" while the session still held the senior. The screen and the pending
        // sale then disagreed about who was being served.
        const t = data.discount_type || ''
        setDiscountType(t)
        // Restore the exact BAND too, not just the type. An older/absent rate that doesn't
        // match a legal band falls back to the type's default so the dropdown still selects.
        let r = parseFloat(data.discount_rate) || 0
        if (t && !STATUTORY_OPTIONS.some(o => o.type === t && o.rate === r)) r = defaultRateFor(t)
        setDiscountRate(t ? r : 0)
        setDiscountIdNo(data.discount_id_no || '')
        setDiscountName(data.discount_name || '')
      })

    // React only to EXTERNAL cart changes (topbar "+", the sale-search island's add). Our
    // own mutations mark the event `internal` — apply() has already set the fresh state, so
    // re-fetching here would be a redundant round trip and could race an in-flight POST.
    const onChanged = e => { if (!e.detail?.internal) load() }

    load()
    document.addEventListener('cart:changed', onChanged)
    return () => document.removeEventListener('cart:changed', onChanged)
  }, [])

  if (!cart) return <div style={{ padding:'2rem', color:'var(--muted)' }}>Loading cart…</div>

  // every mutation returns the fresh cart → drop it straight into state
  const showToast = (message, type = 'warning', channel = null) => {
    const id = Date.now() + Math.random()
    setToasts(t => {
      // channel toasts: only ONE at a time — kick out the old one in this channel
      const base = channel ? t.filter(x => x.channel !== channel) : t
      // non-channel toasts: ignore an exact duplicate message
      if (!channel && base.some(x => x.message === message)) return base
      return [...base, { id, message, type, channel }]
    })
    setTimeout(() => setToasts(t => t.filter(x => x.id !== id)), 3200)
  }

  const apply = data => {
    setCart(data)
    if (data.warning) showToast(data.warning, 'warning', 'stock')
    // Broadcast so the sale-search island refreshes its "in cart" badges and stock counts
    // after a remove, qty change, price edit or clear. Marked internal so our own listener
    // above skips the redundant re-fetch — we already hold this fresh state.
    document.dispatchEvent(new CustomEvent('cart:changed', { detail: { internal: true } }))
  }
  const onQty    = (id, q)     => post(URLS.qty,    { product_id: id, quantity: Math.max(1, q) }).then(apply)
  const onPrice  = (id, total) => post(URLS.price,  { product_id: id, total_price: total }).then(apply)
  const onRemove = id          => post(URLS.remove, { product_id: id }).then(apply)

  const onClear = () => setClearing(true)
  const doClear = () => { post(URLS.clear, {}).then(apply); setClearing(false) }

  if (cart.item_count === 0) {
    return (
      <div className="empty-state">
        <div className="empty-icon"><i className="bi bi-basket"></i></div>
        <div className="empty-title">No products yet</div>
        <p className="empty-sub">Browse the product list and add items to start recording today's sales.</p>
        <a href={CFG.productListUrl} className="btn-add-product">
          <i className="bi bi-plus-circle"></i> Add product
        </a>
      </div>
    )
  }

  const PER_PAGE  = 5
  const pageCount = Math.max(1, Math.ceil(cart.items.length / PER_PAGE))
  const current   = Math.min(page, pageCount)                 // clamp if items shrank
  const pageItems = cart.items.slice((current - 1) * PER_PAGE, current * PER_PAGE)


  // ── Statutory discounts — the active BAND (see STATUTORY_OPTIONS at module scope) ──
  const subtotal    = parseFloat(cart.subtotal) || 0
  const discountOn  = CFG.discountEnabled === '1'
  const sellerVat   = CFG.vatRegistered === '1'
  // The picked (type, rate) band, or null for a regular customer. Everything below reads
  // `statutory` exactly as it did when it was keyed on type alone.
  const statutory   = STATUTORY_OPTIONS.find(
    o => o.type === discountType && o.rate === discountRate) || null

  // Statutory wins over the owner's manual discount — they never stack.
  const pct = statutory
    ? statutory.rate
    : (discountOn ? Math.max(0, Math.min(discount || 0, 100)) : 0)

  // Mirrors price_breakdown(): strip VAT first, then discount the exempt base. Both are
  // multiplications so the total is the same either way, but the DISCOUNT figure differs
  // by which base it came off — and that is the number printed on the receipt.
  const vatAdj  = (statutory && statutory.vatExempt && sellerVat)
    ? subtotal - (subtotal / 1.12)
    : 0
  const base    = subtotal - vatAdj
  const discAmt = base * pct / 100
  const grand   = base - discAmt

  const goConfirm = () => {
    // IMPORTANT: discount_type is ALWAYS sent, empty for a regular customer. Omitting it when
    // regular looks equivalent but isn't: the server reads the type from the session, and
    // an ABSENT key means "unchanged" while an EMPTY one means "cleared". Sending nothing
    // left a previously-picked PWD sitting in the session, so switching back to Regular
    // silently kept the 20%. Reported 2026-07-20; regression test in
    // core/tests/test_statutory_discounts.py.
    const params = new URLSearchParams({
      discount_percent: pct,
      discount_type: discountType,
    })
    if (statutory) {
      params.set('discount_id_no', discountIdNo)
      params.set('discount_name', discountName)
    }
    window.location = CFG.confirmUrl + '?' + params.toString()
  }

  return (
    <div className="row g-3 g-lg-4">

      {/* live toast stack — floats over the page, position doesn't matter */}
      <div className="toast-container" aria-live="polite" aria-atomic="true">
        {toasts.map(t => (
          <div key={t.id} className={`toast-message toast-${t.type}`}>
            {t.type === 'warning' && <i className="bi bi-exclamation-triangle-fill me-2"></i>}
            {t.type === 'error'   && <i className="bi bi-exclamation-circle-fill me-2"></i>}
            {t.type === 'success' && <i className="bi bi-check-circle-fill me-2"></i>}
            {t.type === 'info'    && <i className="bi bi-info-circle-fill me-2"></i>}
            {t.message}
          </div>
        ))}
      </div>

      {/* Clear-cart confirm modal */}
      {clearing && (
        <ConfirmModal
          title="Clear Items"
          note="This empties the cart so you can start over. Nothing has been saved yet."
          tone="danger" icon="bi-trash" btnIcon="bi-trash-fill" label="Yes, clear"
          onConfirm={doClear}
          onCancel={() => setClearing(false)} />
      )}

      {/* LEFT: cart rows */}
      <div className="col-lg-8">
        <div className="pos-card">
          <div className="pos-card-header">
            <span className="pos-card-title"><i className="bi bi-list-ul"></i> Product Lines</span>
          </div>
          <div>
            {pageItems.map(item => (
              <CartRow key={item.id} item={item}
                       onQty={onQty} onPrice={onPrice} onRemove={onRemove}
                       onToast={showToast} />
            ))}
          </div>

          {/* PAGINATION — reuses your .pagination CSS */}
          {pageCount > 1 && (
            <div className="pagination-wrapper">
              <nav>
                <ul className="pagination">
                  <li className={`page-item ${current <= 1 ? 'disabled' : ''}`}>
                    <button className="page-link" onClick={() => setPage(current - 1)} disabled={current <= 1}>
                      <i className="bi bi-chevron-left"></i>
                    </button>
                  </li>
                  {Array.from({ length: pageCount }, (_, i) => i + 1).map(p => (
                    <li key={p} className={`page-item ${p === current ? 'active' : ''}`}>
                      <button className="page-link" onClick={() => setPage(p)}>{p}</button>
                    </li>
                  ))}
                  <li className={`page-item ${current >= pageCount ? 'disabled' : ''}`}>
                    <button className="page-link" onClick={() => setPage(current + 1)} disabled={current >= pageCount}>
                      <i className="bi bi-chevron-right"></i>
                    </button>
                  </li>
                </ul>
              </nav>
            </div>
          )}

        </div>
      </div>

      {/* RIGHT: order summary */}
      <div className="col-lg-4">
        <div className="order-summary">
          <div className="pos-card">
            <div className="pos-card-header">
              <span className="pos-card-title"><i className="bi bi-receipt"></i> Order Summary</span>
            </div>
            <div style={{ padding:'1.25rem' }}>
              {/* Customer type — ALWAYS shown, never gated on discountOn. SC and PWD are
                  statutory: the owner cannot switch them off, so this must appear even for
                  businesses that offer no ordinary discounts at all. */}
              <div style={{ marginBottom:'.85rem' }}>
                <label style={{ display:'block', fontSize:'.72rem', fontWeight:600,
                                textTransform:'uppercase', letterSpacing:'.05em',
                                color:'var(--muted)', marginBottom:'.35rem' }}>
                  <i className="bi bi-person-vcard"></i> Customer
                </label>
                {/* Value encodes the BAND — "type:rate" — because SC and PWD each appear
                    twice (20% vs 5%), so the type alone can't say which row is selected. */}
                <select value={discountType ? `${discountType}:${discountRate}` : ''}
                        onChange={e => {
                          const v = e.target.value
                          // Back to Regular means a DIFFERENT customer, so everything the
                          // previous one carried goes with them — ID, name, band AND the
                          // manual %.
                          //
                          // Resetting the manual % looks aggressive (the owner may have
                          // typed it themselves before picking a statutory type) but the
                          // counter case decides it: serve a senior at 20%, switch to
                          // Regular for the next person, and a stale 20 sitting in the box
                          // silently discounts someone not entitled to it. Losing a typed
                          // rate is an annoyance; granting an unearned discount is money.
                          if (!v) {
                            setDiscountType('')
                            setDiscountRate(0)
                            setDiscountIdNo('')
                            setDiscountName('')
                            setDiscount(0)
                            return
                          }
                          const [t, r] = v.split(':')
                          setDiscountType(t)
                          setDiscountRate(parseFloat(r))
                        }}
                        style={{ width:'100%', padding:'.5rem .65rem', fontWeight:600,
                                 border:'1px solid var(--border)', borderRadius:'8px',
                                 background:'var(--surface)', color:'var(--text)' }}>
                  <option value="">Regular customer</option>
                  {STATUTORY_OPTIONS.map(o => (
                    <option key={`${o.type}:${o.rate}`} value={`${o.type}:${o.rate}`}>
                      {o.label} — {o.rate}%{o.note ? ` (${o.note})` : ''}
                    </option>
                  ))}
                </select>
              </div>

              {/* ID capture — RMO 24-2023 p.5(n) requires the ID number and holder's name
                  on the invoice, plus a signature (printed as a line on the receipt). Only
                  rendered for a statutory type; a regular sale needs none of it. */}
              {statutory && (
                <div style={{ background:'var(--accent-light)', border:'1px solid var(--accent)',
                              borderRadius:'10px', padding:'.7rem .85rem', marginBottom:'.85rem' }}>
                  {/* maxLength MUST match Sale.discount_id_no (60) and the [:60] the view
                      applies. Without it the browser accepts more, the server silently
                      truncates, and a WRONG ID number ends up printed on a BIR invoice with
                      nothing on screen to say it was cut. Free text on purpose: OSCA numbers
                      have no national format (each LGU issues its own, often with letters or
                      dashes), so a digits-only rule would reject valid IDs. */}
                  <input type="text" value={discountIdNo} autoComplete="off" maxLength={60}
                         onChange={e => setDiscountIdNo(e.target.value)}
                         placeholder="OSCA / ID number"
                         style={{ width:'100%', padding:'.4rem .55rem', marginBottom:'.45rem',
                                  border:'1px solid var(--accent)', borderRadius:'8px',
                                  background:'var(--surface)' }} />
                  <input type="text" value={discountName} autoComplete="off" maxLength={255}
                         onChange={e => setDiscountName(e.target.value)}
                         placeholder="Name on the ID"
                         style={{ width:'100%', padding:'.4rem .55rem',
                                  border:'1px solid var(--accent)', borderRadius:'8px',
                                  background:'var(--surface)' }} />
                </div>
              )}

              {/* Manual discount — hidden entirely under a statutory type rather than shown
                  disabled. The rate is the law's, not the cashier's, and an editable-looking
                  box that silently does nothing is worse than no box. */}
              {discountOn && !statutory && (
                <div style={{ display:'flex', alignItems:'center', justifyContent:'space-between',
                              background:'var(--accent-light)', border:'1px solid var(--accent)',
                              borderRadius:'10px', padding:'.6rem .85rem', marginBottom:'.85rem' }}>
                  <span style={{ fontWeight:600, color:'var(--accent)', fontSize:'.85rem' }}>
                    <i className="bi bi-tag"></i> Discount
                  </span>
                  <span style={{ display:'flex', alignItems:'center', gap:'.3rem' }}>
                    <input type="number" step="0.01" min="0" max="100" value={discount}
                           onChange={e => setDiscount(parseFloat(e.target.value) || 0)}
                           style={{ width:'72px', textAlign:'right', padding:'.35rem .5rem',
                                    border:'1px solid var(--accent)', borderRadius:'8px',
                                    background:'var(--surface)', fontWeight:600 }} />
                    <span style={{ color:'var(--accent)', fontWeight:700 }}>%</span>
                  </span>
                </div>
              )}

              <div className="total-row">
                <span className="total-label"><i className="bi bi-cart3"></i> Items</span>
                <span className="total-value">{cart.item_count}</span>
              </div>
              <div className="total-row">
                <span className="total-label"><i className="bi bi-cash-stack"></i> Subtotal</span>
                <span className="total-value">₱{peso(subtotal)}</span>
              </div>
              {/* Deductions in the order Annex D-2 prints them: VAT adjustment first, then
                  the discount off the exempt base. Only VAT-registered sellers ever show the
                  first line — for a non-VAT business vatAdj is always 0. */}
              {vatAdj > 0 && (
                <div className="total-row">
                  <span className="total-label"><i className="bi bi-percent"></i> VAT exempt</span>
                  <span className="total-value" style={{ color:'var(--success)' }}>−₱{peso(vatAdj)}</span>
                </div>
              )}
              {pct > 0 && (
                <div className="total-row">
                  <span className="total-label">
                    <i className="bi bi-tag"></i> {statutory ? `${statutory.label} ${pct}%` : 'Discount'}
                  </span>
                  <span className="total-value" style={{ color:'var(--success)' }}>−₱{peso(discAmt)}</span>
                </div>
              )}

              <hr className="divider" />

              <div className="total-row">
                <span className="total-label"><i className="bi bi-calculator"></i> Total Revenue</span>
                <span className="total-value total-grand">₱{peso(grand)}</span>
              </div>

              <div style={{ display:'flex', gap:'0.5rem', marginTop:'0.75rem' }}>
                <button className="btn-confirm" onClick={goConfirm}
                        style={{ flex:1, padding:'0.6rem', fontSize:'0.875rem', margin:0 }}>
                  <i className="bi bi-check2-circle"></i> Confirm
                </button>
                  <button className="btn-clear" onClick={onClear}
                          style={{ flex:'0 0 auto', width:'auto', padding:'0.6rem 0.85rem', fontSize:'0.8rem', margin:0 }}>
                    Clear
                  </button>

              </div>
            </div>
          </div>
        </div>

          {/* ── Product Presets — HIDDEN 2026-07-20, not deleted ──────────────────────
              Owner's call: presets stay on the MATERIALS side only. On the sales cart this
              panel occupied the space directly under Order Summary, which is where the
              statutory-discount controls (Senior Citizen / PWD / NAAC / Solo Parent, plus
              the ID and name capture RMO 24-2023 p.5(n) requires) now need to live.

              Kept commented rather than removed because the SERVICES case may bring it
              back: xerox, GCash cash-in and bills payment are identical transactions rung
              many times a day, which is exactly what a preset is for. If it returns, it
              probably belongs somewhere other than under the checkout button.

              The Django side is untouched — CFG.presetUrl / CFG.presetListUrl, the view and
              the preset list page all still work. This hides ONE entry point, nothing more.

          <div className="pos-card" style={{ marginBottom:0, marginTop:'1rem' }}>
            <div className="pos-card-header">
              <span className="pos-card-title"><i className="bi bi-bookmark-fill"></i> Product Presets</span>
              <a href={CFG.presetListUrl} style={{ fontSize:'.82rem', color:'var(--muted)', textDecoration:'none' }}>
                View all <i className="bi bi-arrow-right"></i>
              </a>
            </div>
            <div style={{ padding:'1.25rem' }}>
              <form method="POST" action={CFG.presetUrl}>
                <input type="hidden" name="csrfmiddlewaretoken" value={getCookie('csrftoken')} />
                <input type="text" name="product_name" autoComplete="off"
                       className="preset-input" placeholder="e.g. Service Fees" />
                <label style={{ display:'flex', alignItems:'center', gap:'.55rem', fontSize:'.85rem', marginBottom:'.85rem', cursor:'pointer' }}>
                  <input type="checkbox" value="checkbox" name="product_checkbox" style={{ accentColor:'var(--accent)' }} />
                  Save for future use
                </label>
                <button type="submit" className="btn-preset">
                  <i className="bi bi-bookmark-check"></i> Save as preset
                </button>
              </form>
            </div>
          </div>
          ─────────────────────────────────────────────────────────────────────────── */}

      </div>
    </div>
  )
}

if (el) {
  createRoot(el).render(<StrictMode><SaleCart /></StrictMode>)
}
