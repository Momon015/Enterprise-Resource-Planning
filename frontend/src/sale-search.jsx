import { StrictMode, useEffect, useRef, useState } from 'react'
import { createRoot } from 'react-dom/client'

// ── helpers ───────────────────────────────────────────────
function getCookie(name) {
  const m = document.cookie.match('(^|;)\\s*' + name + '\\s*=\\s*([^;]+)')
  return m ? m.pop() : ''
}

// First two letters of the name — the app-wide no-image avatar (see project_no_image_initials).
// Unlike materials, a product CAN carry its own photo; this is only the fallback when it doesn't.
function initials(name) {
  const parts = (name || '').replace(/[^A-Za-z0-9\s-]/g, '').split(/[\s-]+/).filter(Boolean)
  if (parts.length === 0) return '?'
  // First TWO CHARACTERS ("Item 05" → "IT"), matching the Django avatar — NOT word-initials
  // (which turned "Item 05" into "I0").
  return (parts.join('').slice(0, 2) || '?').toUpperCase()
}

// Real product photo if we have one, else the initials avatar — the whole reason the sale
// side keeps a Thumb the materials side dropped: products have images, materials don't.
function Thumb({ image, name }) {
  if (image) return <img className="ps-thumb ps-thumb--img" src={image} alt="" loading="lazy" />
  return <div className="ps-thumb">{initials(name)}</div>
}

// Debounce the raw input so we hit the server only after typing settles.
function useDebounced(value, delay = 200) {
  const [v, setV] = useState(value)
  useEffect(() => {
    const t = setTimeout(() => setV(value), delay)
    return () => clearTimeout(t)
  }, [value, delay])
  return v
}

// Bold the matched slice of a label — cheap, and it makes long lists scannable.
function Highlight({ text, query }) {
  if (!query) return text
  const idx = text.toLowerCase().indexOf(query.toLowerCase())
  if (idx === -1) return text
  return (
    <>
      {text.slice(0, idx)}
      <mark className="ps-mark">{text.slice(idx, idx + query.length)}</mark>
      {text.slice(idx + query.length)}
    </>
  )
}

const el = document.getElementById('sale-search-root')
const CFG = el ? el.dataset : {}

async function postAdd(url, productId) {
  const res = await fetch(url, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/x-www-form-urlencoded',
      'X-CSRFToken': getCookie('csrftoken'),
    },
    body: new URLSearchParams({ product_id: productId }).toString(),
  })
  return res.json()
}

function SaleSearch() {
  const [query, setQuery] = useState('')
  const [focused, setFocused] = useState(false)
  const [loading, setLoading] = useState(false)
  const [active, setActive] = useState(0)
  // services MUST default to [] — the first render (before the mount fetch resolves) does
  // `[...products, ...services]`, and spreading undefined throws, blanking the whole island.
  const [data, setData] = useState({ products: [], services: [], suggested: false })
  const [toasts, setToasts] = useState([])
  // Bumped on every cart mutation to force a re-fetch, so each row's in-cart quantity
  // stays live while the dropdown is open — otherwise adding an item would leave its
  // qty badge one behind until the next keystroke.
  const [cartTick, setCartTick] = useState(0)
  const inputRef = useRef(null)
  const wrapRef = useRef(null)
  const reqId = useRef(0)
  const debounced = useDebounced(query, 200)

  // Fetch results whenever the debounced query changes OR the cart mutates. reqId guards
  // against a slow early response landing after a later one (out-of-order races).
  useEffect(() => {
    const id = ++reqId.current
    setLoading(true)
    fetch(`${CFG.searchUrl}?q=${encodeURIComponent(debounced.trim())}`)
      .then(r => r.json())
      .then(res => {
        if (id !== reqId.current) return
        setData({ products: res.products || [], services: res.services || [],
                  suggested: !!res.suggested })
        setLoading(false)
      })
      .catch(() => { if (id === reqId.current) setLoading(false) })
  }, [debounced, cartTick])

  // Any cart change — this island's own "+", the sibling cart's qty steppers, a preset —
  // fires `cart:changed`. Re-fetch so the in-cart badges match reality.
  useEffect(() => {
    const bump = () => setCartTick(t => t + 1)
    document.addEventListener('cart:changed', bump)
    return () => document.removeEventListener('cart:changed', bump)
  }, [])

  // A new query starts the highlight at the top; a cart-tick re-fetch must NOT, or the
  // selection would jump every time you add an item.
  useEffect(() => { setActive(0) }, [debounced])

  // Close the dropdown on an outside click.
  useEffect(() => {
    function onDown(e) {
      if (!wrapRef.current?.contains(e.target)) setFocused(false)
    }
    document.addEventListener('mousedown', onDown)
    return () => document.removeEventListener('mousedown', onDown)
  }, [])

  const { products, services, suggested } = data
  // One flat order for keyboard nav — products first, then services — even though they
  // render as two labelled sections. `active` indexes into this.
  const flat = [...products, ...services]
  const total = flat.length

  // Warnings only — the success case is confirmed by the "in cart" badge, not a toast.
  // ONE toast per message: hammering "+" on a stock-capped item fired the same "only N
  // in stock" warning over and over and stacked them. Drop any existing copy first so the
  // repeat just refreshes the single toast instead of piling up. (Mirrors the cart island.)
  const showToast = (message) => {
    const id = Date.now() + Math.random()
    setToasts(t => [...t.filter(x => x.message !== message), { id, message }])
    setTimeout(() => setToasts(t => t.filter(x => x.id !== id)), 3200)
  }

  function addProduct(p) {
    postAdd(CFG.addUrl, p.id).then(res => {
      if (res.warning) { showToast(res.warning); return }
      // Tell the sibling sale-cart island to re-read — same signal the topbar "+" fires.
      // No success toast: the row's "in cart" badge ticks up on the same cart:changed, so it
      // already confirms the add. Only warnings (e.g. stock caps) surface as a toast now.
      document.dispatchEvent(new CustomEvent('cart:changed'))
    })
  }

  function onKeyDown(e) {
    if (!focused) setFocused(true)
    if (e.key === 'ArrowDown') {
      e.preventDefault()
      setActive(i => Math.min(i + 1, Math.max(total - 1, 0)))
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      setActive(i => Math.max(i - 1, 0))
    } else if (e.key === 'Enter') {
      e.preventDefault()
      const item = flat[active]
      if (item) addProduct(item)
    } else if (e.key === 'Escape') {
      setFocused(false)
      inputRef.current?.blur()
    }
  }

  const open = focused
  const hasResults = total > 0

  // One row, shared by both sections. `idx` is the FLAT index so keyboard highlight lines
  // up across products + services.
  const renderRow = (p, idx) => (
    <div key={`p${p.id}`}
         className={`ps-row ${active === idx ? 'is-active' : ''}`}
         onMouseEnter={() => setActive(idx)}
         onClick={() => addProduct(p)}>
      <Thumb image={p.image} name={p.name} />
      <div className="ps-body">
        <div className="ps-name"><Highlight text={p.name} query={debounced} /></div>
        <div className="ps-sub">
          {p.supplier && <>
            <Highlight text={p.supplier} query={debounced} />
            <span className="ps-dot">·</span>
          </>}
          <span className="ps-cost">₱{p.price}</span>
          {/* Stock matters on the SELL side — a cashier needs to know what's left before
              ringing it. Services have no stock. */}
          {!p.is_service && <>
            <span className="ps-dot">·</span>
            <span className={`ps-stock${p.stock <= 0 ? ' ps-stock--out' : ''}`}>
              {p.stock > 0 ? `${p.stock} in stock` : 'Out of stock'}
            </span>
          </>}
        </div>
      </div>
      {p.in_cart > 0 && (
        <span className="ps-incart" title={`${p.in_cart} in cart`}>
          <i className="bi bi-cart-check-fill"></i> {p.in_cart} in cart
        </span>
      )}
      <button className="ps-add" aria-label={`Add ${p.name}`}
              onClick={e => { e.stopPropagation(); addProduct(p) }}>
        <i className="bi bi-plus-lg"></i>
      </button>
    </div>
  )

  return (
    <div ref={wrapRef} className="ps-wrap">

      {/* toasts */}
      <div className="toast-container" aria-live="polite" aria-atomic="true">
        {toasts.map(t => (
          <div key={t.id} className="toast-message toast-warning">
            <i className="bi bi-exclamation-triangle-fill me-2"></i>
            {t.message}
          </div>
        ))}
      </div>

      {/* input */}
      <div className={`ps-input-shell ${open ? 'is-open' : ''}`}>
        <i className="bi bi-search ps-input-icon"></i>
        <input
          ref={inputRef}
          value={query}
          onChange={e => setQuery(e.target.value)}
          onFocus={() => setFocused(true)}
          onKeyDown={onKeyDown}
          placeholder={CFG.services === '1' ? 'Search products and services…' : 'Search products…'}
          className="ps-input"
        />
        {query && (
          <button className="ps-clear" aria-label="Clear"
                  onClick={() => { setQuery(''); inputRef.current?.focus() }}>
            <i className="bi bi-x-lg"></i>
          </button>
        )}
        {/* <kbd className="ps-kbd">⌘K</kbd> */}
      </div>

      {/* dropdown */}
      <div className={`ps-panel ${open ? 'is-open' : ''}`}>
        <div className="ps-panel-head">
          <span>{loading ? 'Searching…' : `${total} result${total === 1 ? '' : 's'}`}</span>
          <span className="ps-hints">
            <span><kbd>↑↓</kbd> navigate</span>
            <span><kbd>↵</kbd> add</span>
            <span><kbd>esc</kbd> close</span>
          </span>
        </div>

        <div className="ps-list">
          {loading && !hasResults ? (
            <div className="ps-loading"><i className="bi bi-arrow-repeat ps-spin"></i> Searching…</div>
          ) : !hasResults ? (
            <div className="ps-empty">
              <div className="ps-empty-icon"><i className="bi bi-search"></i></div>
              <div className="ps-empty-title">No results{query && <> for “{query}”</>}</div>
              <div className="ps-empty-sub">Try a different product name.</div>
            </div>
          ) : (
            <>
              {products.length > 0 && (
                <div className="ps-section">
                  <div className="ps-section-head">
                    {suggested
                      ? <><i className="bi bi-star-fill"></i> Best sellers</>
                      : <><i className="bi bi-box-seam"></i> Products</>}
                    <span className="ps-section-count">· {products.length}</span>
                  </div>
                  {products.map((p, i) => renderRow(p, i))}
                </div>
              )}

              {services.length > 0 && (
                <div className="ps-section">
                  <div className="ps-section-head">
                    <i className="bi bi-ticket-perforated"></i> Top services
                    <span className="ps-section-count">· {services.length}</span>
                  </div>
                  {services.map((p, i) => renderRow(p, products.length + i))}
                </div>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  )
}

if (el) {
  createRoot(el).render(<StrictMode><SaleSearch /></StrictMode>)
}
