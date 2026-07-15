import { StrictMode, useEffect, useMemo, useRef, useState } from 'react'
import { createRoot } from 'react-dom/client'

// ── helpers ───────────────────────────────────────────────
function getCookie(name) {
  const m = document.cookie.match('(^|;)\\s*' + name + '\\s*=\\s*([^;]+)')
  return m ? m.pop() : ''
}
const peso = n =>
  Number(n).toLocaleString('en-PH', { minimumFractionDigits: 2, maximumFractionDigits: 2 })

function initials(name) {
  const parts = (name || '').replace(/[^A-Za-z0-9\s-]/g, '').split(/[\s-]+/).filter(Boolean)
  if (parts.length === 0) return '?'
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase()
  return (parts[0][0] + parts[1][0]).toUpperCase()
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

const el = document.getElementById('purchase-search-root')
const CFG = el ? el.dataset : {}

async function postAdd(url, materialId) {
  const res = await fetch(url, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/x-www-form-urlencoded',
      'X-CSRFToken': getCookie('csrftoken'),
    },
    body: new URLSearchParams({ material_id: materialId }).toString(),
  })
  return res.json()
}

// A material row's avatar: real product thumbnail if we have one, else plain initials.
// (No rainbow tints — the image is the identity, initials are just the fallback.)
function Thumb({ image, name }) {
  if (image) return <img className="ps-thumb ps-thumb--img" src={image} alt="" loading="lazy" />
  return <div className="ps-thumb">{initials(name)}</div>
}

function PurchaseSearch() {
  const [query, setQuery] = useState('')
  const [focused, setFocused] = useState(false)
  const [loading, setLoading] = useState(false)
  const [active, setActive] = useState(0)
  const [data, setData] = useState({ materials: [], products: [] })
  const [toasts, setToasts] = useState([])
  const inputRef = useRef(null)
  const wrapRef = useRef(null)
  const reqId = useRef(0)
  const debounced = useDebounced(query, 200)

  // Fetch results whenever the debounced query changes. reqId guards against a slow
  // early response landing after a later one (out-of-order races).
  useEffect(() => {
    const id = ++reqId.current
    setLoading(true)
    fetch(`${CFG.searchUrl}?q=${encodeURIComponent(debounced.trim())}`)
      .then(r => r.json())
      .then(res => {
        if (id !== reqId.current) return
        setData({ materials: res.materials || [], products: res.products || [] })
        setActive(0)
        setLoading(false)
      })
      .catch(() => { if (id === reqId.current) setLoading(false) })
  }, [debounced])

  // Close the dropdown on an outside click.
  useEffect(() => {
    function onDown(e) {
      if (!wrapRef.current?.contains(e.target)) setFocused(false)
    }
    document.addEventListener('mousedown', onDown)
    return () => document.removeEventListener('mousedown', onDown)
  }, [])

  const { materials, products } = data
  // Flat list = navigable order. Only materials are addable; products ride along for reference.
  const flat = useMemo(
    () => [...materials.map(m => ({ ...m, kind: 'material' })),
           ...products.map(p => ({ ...p, kind: 'product' }))],
    [materials, products],
  )
  const total = flat.length

  const showToast = (message, type = 'success') => {
    const id = Date.now() + Math.random()
    setToasts(t => [...t, { id, message, type }])
    setTimeout(() => setToasts(t => t.filter(x => x.id !== id)), 3200)
  }

  function addMaterial(m) {
    postAdd(CFG.addUrl, m.id).then(res => {
      if (res.warning) { showToast(res.warning, 'warning'); return }
      // Tell the sibling purchase-cart island to re-read — same signal the topbar "+" fires.
      document.dispatchEvent(new CustomEvent('cart:changed'))
      showToast(`${res.added || m.name} added to purchase`, 'success')
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
      if (item && item.kind === 'material') addMaterial(item)
    } else if (e.key === 'Escape') {
      setFocused(false)
      inputRef.current?.blur()
    }
  }

  const open = focused
  const hasResults = total > 0

  return (
    <div ref={wrapRef} className="ps-wrap">

      {/* toasts */}
      <div className="toast-container" aria-live="polite" aria-atomic="true">
        {toasts.map(t => (
          <div key={t.id} className={`toast-message toast-${t.type}`}>
            {t.type === 'success'
              ? <i className="bi bi-check-circle-fill me-2"></i>
              : <i className="bi bi-exclamation-triangle-fill me-2"></i>}
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
          placeholder="Search materials, suppliers, products…"
          className="ps-input"
        />
        {query && (
          <button className="ps-clear" aria-label="Clear"
                  onClick={() => { setQuery(''); inputRef.current?.focus() }}>
            <i className="bi bi-x-lg"></i>
          </button>
        )}
        <kbd className="ps-kbd">⌘K</kbd>
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
              <div className="ps-empty-sub">Try a different name, or check the supplier spelling.</div>
            </div>
          ) : (
            <>
              {materials.length > 0 && (
                <div className="ps-section">
                  <div className="ps-section-head">
                    <i className="bi bi-box-seam"></i> Materials
                    <span className="ps-section-count">· {materials.length}</span>
                  </div>
                  {materials.map((m, i) => {
                    const isActive = active === i
                    return (
                      <div key={`m${m.id}`}
                           className={`ps-row ${isActive ? 'is-active' : ''}`}
                           onMouseEnter={() => setActive(i)}
                           onClick={() => addMaterial(m)}>
                        <Thumb image={m.image} name={m.name} />
                        <div className="ps-body">
                          <div className="ps-name"><Highlight text={m.name} query={debounced} /></div>
                          <div className="ps-sub">
                            Supplier: <Highlight text={m.supplier} query={debounced} />
                            <span className="ps-dot">·</span>
                            <span className="ps-cost">₱{m.price}</span>
                          </div>
                        </div>
                        <span className="ps-pill ps-pill--material">Material</span>
                        <button className="ps-add" aria-label={`Add ${m.name}`}
                                onClick={e => { e.stopPropagation(); addMaterial(m) }}>
                          <i className="bi bi-plus-lg"></i>
                        </button>
                      </div>
                    )
                  })}
                </div>
              )}

              {products.length > 0 && (
                <div className="ps-section">
                  <div className="ps-section-head">
                    <i className="bi bi-tag"></i> Products
                    <span className="ps-section-count">· {products.length}</span>
                  </div>
                  {products.map((p, i) => {
                    const idx = materials.length + i
                    const isActive = active === idx
                    return (
                      <div key={`p${p.id}`}
                           className={`ps-row ps-row--ref ${isActive ? 'is-active' : ''}`}
                           onMouseEnter={() => setActive(idx)}>
                        <Thumb image={p.image} name={p.name} />
                        <div className="ps-body">
                          <div className="ps-name"><Highlight text={p.name} query={debounced} /></div>
                          <div className="ps-sub"><span className="ps-cost">₱{p.price}</span></div>
                        </div>
                        <span className="ps-pill ps-pill--product">Product</span>
                      </div>
                    )
                  })}
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
  createRoot(el).render(<StrictMode><PurchaseSearch /></StrictMode>)
}
