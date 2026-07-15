import { StrictMode, useEffect, useState } from 'react'
import { createRoot } from 'react-dom/client'

// ── helpers ───────────────────────────────────────────────
function getCookie(name) {
  const m = document.cookie.match('(^|;)\\s*' + name + '\\s*=\\s*([^;]+)')
  return m ? m.pop() : ''
}
const peso = n =>
  Number(n).toLocaleString('en-PH', { minimumFractionDigits: 2, maximumFractionDigits: 2 })

const el = document.getElementById('purchase-cart-root')
const CFG = el ? el.dataset : {}
const BASE = CFG.apiBase || ''
const URLS = {
  state:  BASE,
  qty:    BASE + 'qty/',
  line:   BASE + 'line/',
  remove: BASE + 'remove/',
  clear:  BASE + 'clear/',
}

async function post(url, body) {
  const res = await fetch(url, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/x-www-form-urlencoded',
      'X-CSRFToken': getCookie('csrftoken'),
    },
    body: new URLSearchParams(body).toString(),
  })
  return res.json()
}

// ── confirm modal (matches your .quickview / .cm-* styles) ──
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
                <button type="button" className="btn btn-outline-secondary" onClick={onCancel}>Keep items</button>
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

// ── one order line (material) ─────────────────────────────
function CartRow({ item, mode, onQty, onLine, onRemove, onToast }) {
  const [editingPrice, setEditingPrice] = useState(false)
  const [priceDraft, setPriceDraft] = useState(item.item_total)
  const [discDraft, setDiscDraft] = useState(item.discount)

  useEffect(() => { setDiscDraft(item.discount) }, [item.discount])

  const setQty = (v) => {
    if (v > item.stock) { onToast(`${item.material} — only ${item.stock} available.`, 'warning', 'stock'); v = item.stock }
    if (v < 1) v = 1
    if (v === item.quantity) return
    onQty(item.id, v)
  }
  const saveDiscount = (val) => {
    let d = parseFloat(val); if (isNaN(d) || d < 0) d = 0
    onLine(item.id, { discount: d.toFixed(2) })
  }
  const stepDiscount = (delta) => {
    let d = (parseFloat(discDraft) || 0) + delta
    if (d < 0) d = 0
    setDiscDraft(d.toFixed(2)); saveDiscount(d)
  }

  return (
    <div className="sale-row">
      <img className="sale-row-thumb" src={item.image || CFG.noImage} alt="" />
      <div className="sale-row-body">
        <div className="sale-row-head">
          <div className="sale-row-info">
            <div className="sale-row-name">{item.material}</div>
            <div className="sale-row-sub">{item.supplier}</div>
            <div className="sale-row-cost">Unit cost ₱{item.price}</div>
          </div>
          <button className="sale-row-remove" title="Remove" onClick={() => onRemove(item.id)}>
            <i className="bi bi-x-lg"></i>
          </button>
        </div>

        <div className="sale-row-ctrls-purchase">
          {/* QTY */}
          <div className="row-ctrl">
            <span className="row-ctrl-label">Qty</span>
            <div className="qty-stepper">
              <button className="qty-btn" disabled={item.quantity <= 1}
                      onClick={() => setQty(item.quantity - 1)}>−</button>
              <input className="qty-input" type="number" min="1" value={item.quantity}
                     onChange={e => setQty(parseInt(e.target.value || '1', 10))} />
              <button className="qty-btn" onClick={() => setQty(item.quantity + 1)}>+</button>
            </div>
          </div>

          {/* DISCOUNT — flat mode only */}
          {mode === 'flat' && (
            <div className="row-ctrl">
              <span className="row-ctrl-label">Discount</span>
              <div className="qty-stepper">
                <button className="qty-btn" onClick={() => stepDiscount(-1)}>−</button>
                <input className="qty-input" type="number" min="0" step="0.01" value={discDraft}
                       onChange={e => setDiscDraft(e.target.value)}
                       onBlur={e => saveDiscount(e.target.value)} />
                <button className="qty-btn" onClick={() => stepDiscount(1)}>+</button>
              </div>
            </div>
          )}

          {/* TOTAL PRICE — flip to edit */}
          <div className="row-ctrl row-ctrl--total">
            <span className="row-ctrl-label">Total</span>
            {editingPrice ? (
              <div style={{ display:'flex', alignItems:'center', gap:'0.3rem' }}>
                <div className="row-price-chip">
                  <span style={{ color:'var(--muted)', fontSize:'0.85rem' }}>₱</span>
                  <input className="num" type="number" step="0.01" min="0" autoFocus
                         value={priceDraft} onChange={e => setPriceDraft(e.target.value)} />
                </div>
                <button className="apply-check-btn" title="Update total price"
                        onClick={() => { onLine(item.id, { total_price: priceDraft }); setEditingPrice(false) }}>
                  <i className="bi bi-check2"></i>
                </button>
              </div>
            ) : (
              <div className="cost-display">
                <span className="sale-row-total">₱{item.item_total}</span>
                <button className="edit-btn" title="Edit total price"
                        onClick={() => { setPriceDraft(item.item_total); setEditingPrice(true) }}>
                  <i className="bi bi-pencil-square"></i>
                </button>
              </div>
            )}
            {mode === 'flat' && parseFloat(item.discount) > 0 && (
              <span className="row-ctrl-hint">− ₱{item.discount} → ₱{item.item_discount}</span>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}

// ── the whole purchase cart ───────────────────────────────
function PurchaseCart() {
  const [cart, setCart] = useState(null)
  const [toasts, setToasts] = useState([])
  const [clearing, setClearing] = useState(false)
  const [page, setPage] = useState(1)
  const [discount, setDiscount] = useState(0)   // whole-order % (percent mode)

  // Load on mount — AND again whenever the cart is mutated from outside this island.
  // The topbar search's "+" adds a material via plain htmx, entirely outside React, so
  // without this the island keeps showing the state it fetched at mount (most visibly the
  // empty state, still sitting there after you'd added something) until a manual refresh.
  // main.html fires `cart:changed` on any htmx swap of the cart badge. Mirror of sale-cart.
  useEffect(() => {
    const load = () =>
      fetch(URLS.state).then(r => r.json()).then(data => {
        setCart(data)
        setDiscount(parseFloat(data.purchase_discount_percent) || 0)
      })

    load()
    document.addEventListener('cart:changed', load)
    return () => document.removeEventListener('cart:changed', load)
  }, [])

  if (!cart) return <div style={{ padding:'2rem', color:'var(--muted)' }}>Loading cart…</div>

  const showToast = (message, type = 'warning', channel = null) => {
    const id = Date.now() + Math.random()
    setToasts(t => {
      const base = channel ? t.filter(x => x.channel !== channel) : t
      if (!channel && base.some(x => x.message === message)) return base
      return [...base, { id, message, type, channel }]
    })
    setTimeout(() => setToasts(t => t.filter(x => x.id !== id)), 3200)
  }

  const apply    = data => { setCart(data); if (data.warning) showToast(data.warning, 'warning', 'stock') }
  const onQty    = (id, q)    => post(URLS.qty,  { material_id: id, quantity: Math.max(1, q) }).then(apply)
  const onLine   = (id, body) => post(URLS.line, { material_id: id, ...body }).then(apply)
  const onRemove = id         => post(URLS.remove, { material_id: id }).then(apply)
  const onClear  = () => setClearing(true)
  const doClear  = () => { post(URLS.clear, {}).then(apply); setClearing(false) }

  if (cart.item_count === 0) {
    return (
      <div className="empty-state">
        <div className="empty-icon"><i className="bi bi-receipt"></i></div>
        <div className="empty-title">No items yet</div>
        <p className="empty-sub">Browse the supplier list and add items to start recording this purchase.</p>
        <a href={CFG.materialListUrl} className="btn-add-product">
          <i className="bi bi-plus-circle"></i> Add item
        </a>
      </div>
    )
  }

  const mode     = cart.discount_enabled ? 'percent' : 'flat'
  const subtotal = parseFloat(cart.subtotal) || 0
  const totalDiscount = parseFloat(cart.total_discount) || 0
  const pct      = Math.max(0, Math.min(discount || 0, 100))
  const discAmt  = subtotal * pct / 100
  const grand    = mode === 'percent' ? (subtotal - discAmt) : parseFloat(cart.total_after_discount)

  const goConfirm = () => {
    const q = mode === 'percent' ? ('?discount_percent=' + pct) : ''
    window.location = CFG.confirmUrl + q
  }

  const PER_PAGE  = 4
  const pageCount = Math.max(1, Math.ceil(cart.items.length / PER_PAGE))
  const current   = Math.min(page, pageCount)
  const pageItems = cart.items.slice((current - 1) * PER_PAGE, current * PER_PAGE)

  return (
    <div className="row g-3 g-lg-4">

      <div className="toast-container" aria-live="polite" aria-atomic="true">
        {toasts.map(t => (
          <div key={t.id} className={`toast-message toast-${t.type}`}>
            {t.type === 'warning' && <i className="bi bi-exclamation-triangle-fill me-2"></i>}
            {t.type === 'success' && <i className="bi bi-check-circle-fill me-2"></i>}
            {t.type === 'info'    && <i className="bi bi-info-circle-fill me-2"></i>}
            {t.message}
          </div>
        ))}
      </div>

      {clearing && (
        <ConfirmModal
          title="Clear Items"
          note="This empties the cart so you can start over. Nothing has been saved yet."
          tone="danger" icon="bi-trash" btnIcon="bi-trash-fill" label="Yes, clear"
          onConfirm={doClear} onCancel={() => setClearing(false)} />
      )}

      {/* LEFT: order lines */}
      <div className="col-lg-8">
        <div className="pos-card">
          <div className="pos-card-header">
            <span className="pos-card-title"><i className="bi bi-list-ul"></i> Order lines</span>
            <a href={CFG.materialListUrl} className="btn-browse"><i className="bi bi-search"></i> Browse supplier</a>
          </div>
          <div>
            {pageItems.map(item => (
              <CartRow key={item.id} item={item} mode={mode}
                       onQty={onQty} onLine={onLine} onRemove={onRemove} onToast={showToast} />
            ))}
          </div>

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

      {/* RIGHT: summary + presets */}
      <div className="col-lg-4">
        <div className="order-summary">
          <div className="pos-card">
            <div className="pos-card-header">
              <span className="pos-card-title"><i className="bi bi-receipt"></i> Order Summary</span>
            </div>
            <div style={{ padding:'1.25rem' }}>

              {mode === 'percent' && (
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
                <span className="total-label"><i className="bi bi-box-seam"></i> Items</span>
                <span className="total-value">{cart.item_count}</span>
              </div>
              <div className="total-row">
                <span className="total-label"><i className="bi bi-cash-stack"></i> Subtotal</span>
                <span className="total-value">₱{peso(subtotal)}</span>
              </div>

              {mode === 'percent' && pct > 0 && (
                <div className="total-row">
                  <span className="total-label"><i className="bi bi-tag"></i> Discount</span>
                  <span className="total-value text-success-soft">−₱{peso(discAmt)}</span>
                </div>
              )}
              {mode === 'flat' && totalDiscount > 0 && (
                <div className="total-row">
                  <span className="total-label"><i className="bi bi-tag"></i> Discount</span>
                  <span className="total-value text-success-soft">₱{peso(totalDiscount)}</span>
                </div>
              )}

              <hr className="divider" />

              <div className="total-row">
                <span className="total-label"><i className="bi bi-calculator"></i> Total Cost</span>
                <span className="total-value total-grand" style={{ color:'var(--danger)' }}>₱{peso(grand)}</span>
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

          {/* Supplier Presets — posts to Django */}
          <div className="pos-card" style={{ marginBottom:0, marginTop:'1rem' }}>
            <div className="pos-card-header">
              <span className="pos-card-title"><i className="bi bi-bookmark-fill"></i> Supplier Presets</span>
              <a href={CFG.presetListUrl} style={{ fontSize:'.8rem', color:'var(--muted)', textDecoration:'none' }}>
                View all <i className="bi bi-arrow-right"></i>
              </a>
            </div>
            <div style={{ padding:'1.25rem' }}>
              <form method="POST" action={CFG.presetUrl} autoComplete="off">
                <input type="hidden" name="csrfmiddlewaretoken" value={getCookie('csrftoken')} />
                <input type="text" name="name" className="preset-input" placeholder="e.g. Supplier" />
                <label style={{ display:'flex', alignItems:'center', gap:'.5rem', fontSize:'.83rem', marginBottom:'.75rem', cursor:'pointer' }}>
                  <input type="checkbox" value="checkbox" name="checkbox" style={{ accentColor:'var(--accent)' }} />
                  Save for future use
                </label>
                <button type="submit" className="btn-preset">
                  <i className="bi bi-bookmark-check"></i> Save as preset
                </button>
              </form>
            </div>
          </div>

        </div>
      </div>
    </div>
  )
}

if (el) {
  createRoot(el).render(<StrictMode><PurchaseCart /></StrictMode>)
}
