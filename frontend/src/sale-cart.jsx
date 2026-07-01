import { StrictMode, useEffect, useState } from 'react'
import { createRoot } from 'react-dom/client'

// ── helpers ───────────────────────────────────────────────
function getCookie(name) {
  const m = document.cookie.match('(^|;)\\s*' + name + '\\s*=\\s*([^;]+)')
  return m ? m.pop() : ''
}
const peso = n =>
  Number(n).toLocaleString('en-PH', { minimumFractionDigits: 2, maximumFractionDigits: 2 })

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
      <img className="sale-row-thumb" src={item.image || CFG.noImage} alt="" />
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
                  <input className="mono" type="number" step="0.01" min="0" autoFocus
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

  // load cart once, when the component first appears on screen
  useEffect(() => {
    fetch(URLS.state).then(r => r.json()).then(data => {
      setCart(data)
      setDiscount(parseFloat(data.discount_percent) || 0)
    })
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

  const apply = data => { setCart(data); if (data.warning) showToast(data.warning, 'warning', 'stock') }
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


  const subtotal   = parseFloat(cart.subtotal) || 0
  const pct        = Math.max(0, Math.min(discount || 0, 100))
  const discAmt    = subtotal * pct / 100
  const grand      = subtotal - discAmt
  const discountOn = CFG.discountEnabled === '1'

  const goConfirm = () => { window.location = CFG.confirmUrl + '?discount_percent=' + pct }

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
              {discountOn && (
                <div style={{ display:'flex', alignItems:'center', justifyContent:'space-between',
                              background:'var(--accent-light)', border:'1px solid var(--accent)',
                              borderRadius:'10px', padding:'.6rem .85rem', marginBottom:'.85rem' }}>
                  <span style={{ fontWeight:600, color:'var(--accent)', fontSize:'.85rem' }}>
                    <i className="bi bi-tags"></i> Discount
                  </span>
                  <span style={{ display:'flex', alignItems:'center', gap:'.3rem' }}>
                    <input type="number" step="0.01" min="0" max="100" value={discount}
                           onChange={e => setDiscount(parseFloat(e.target.value) || 0)}
                           style={{ width:'72px', textAlign:'right', padding:'.35rem .5rem',
                                    border:'1px solid var(--accent)', borderRadius:'8px',
                                    background:'#fff', fontWeight:600 }} />
                    <span style={{ color:'var(--accent)', fontWeight:700 }}>%</span>
                  </span>
                </div>
              )}

              <div className="total-row">
                <span className="total-label"><i className="bi bi-basket3"></i> Items</span>
                <span className="total-value">{cart.item_count}</span>
              </div>
              <div className="total-row">
                <span className="total-label"><i className="bi bi-cash-stack"></i> Subtotal</span>
                <span className="total-value">₱{peso(subtotal)}</span>
              </div>
              {discountOn && pct > 0 && (
                <div className="total-row">
                  <span className="total-label"><i className="bi bi-tags"></i> Discount</span>
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

          {/* Product Presets — posts to Django (not part of the live cart) */}
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

      </div>
    </div>
  )
}

if (el) {
  createRoot(el).render(<StrictMode><SaleCart /></StrictMode>)
}
