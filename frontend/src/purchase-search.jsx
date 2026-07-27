import { StrictMode, useEffect, useRef, useState } from 'react'
import { createRoot } from 'react-dom/client'

import { CameraScanner, scannerSupported, beep, errorBeep } from './scanner.jsx'

// ── helpers ───────────────────────────────────────────────
function getCookie(name) {
  const m = document.cookie.match('(^|;)\\s*' + name + '\\s*=\\s*([^;]+)')
  return m ? m.pop() : ''
}
const peso = n =>
  Number(n).toLocaleString('en-PH', { minimumFractionDigits: 2, maximumFractionDigits: 2 })

// First two letters of the name — the app-wide no-image avatar (see project_no_image_initials).
// Materials never carry their own photo (only products do), so the search always uses initials
// here rather than borrowing a linked product's image, which read as if the material had one.
function initials(name) {
  const parts = (name || '').replace(/[^A-Za-z0-9\s-]/g, '').split(/[\s-]+/).filter(Boolean)
  if (parts.length === 0) return '?'
  // First TWO CHARACTERS ("Item 05" → "IT"), matching the Django avatar — NOT word-initials
  // (which turned "Item 05" into "I0").
  return (parts.join('').slice(0, 2) || '?').toUpperCase()
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

// A wedge/USB barcode scanner types the whole code in a fast burst and sends Enter, which
// fires BEFORE the 200ms debounce — so on Enter we resolve a scanned code (digits, EAN/UPC)
// as an EXACT barcode via the same endpoint (`exact_match` flags the hit) and add it
// straight away. Anything else falls back to the highlighted row for a human.
function looksLikeScan(s) {
  return /^[0-9]{6,}$/.test(s)
}
async function scanLookup(url, code, key) {
  try {
    const res = await fetch(`${url}?q=${encodeURIComponent(code)}`).then(r => r.json())
    if (res.exact_match && res[key] && res[key][0]) return res[key][0]
  } catch (e) { /* ignore — caller falls back to the highlighted row */ }
  return null
}

function PurchaseSearch() {
  const [query, setQuery] = useState('')
  const [focused, setFocused] = useState(false)
  const [loading, setLoading] = useState(false)
  const [active, setActive] = useState(0)
  const [data, setData] = useState({ materials: [], suggested: false })
  const [toasts, setToasts] = useState([])
  // Bumped on every cart mutation to force a re-fetch, so each row's in-cart quantity
  // stays live while the dropdown is open — otherwise adding an item would leave its
  // qty badge one behind until the next keystroke.
  const [cartTick, setCartTick] = useState(0)
  const [scanning, setScanning] = useState(false)
  const inputRef = useRef(null)
  const wrapRef = useRef(null)
  const reqId = useRef(0)
  const debounced = useDebounced(query, 200)
  const canScan = scannerSupported()

  // Fetch results whenever the debounced query changes OR the cart mutates. reqId guards
  // against a slow early response landing after a later one (out-of-order races).
  useEffect(() => {
    const id = ++reqId.current
    setLoading(true)
    fetch(`${CFG.searchUrl}?q=${encodeURIComponent(debounced.trim())}`)
      .then(r => r.json())
      .then(res => {
        if (id !== reqId.current) return
        setData({ materials: res.materials || [], suggested: !!res.suggested })
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

  const { materials, suggested } = data
  const total = materials.length

  // Warnings only — the success case is confirmed by the "in cart" badge, not a toast.
  // ONE toast per message: hammering "+" on a stock-capped item fired the same "only N
  // available" warning over and over and stacked them. Drop any existing copy first so the
  // repeat just refreshes the single toast instead of piling up. (Mirrors the cart island.)
  const showToast = (message) => {
    const id = Date.now() + Math.random()
    setToasts(t => [...t.filter(x => x.message !== message), { id, message }])
    setTimeout(() => setToasts(t => t.filter(x => x.id !== id)), 3200)
  }

  function addMaterial(m) {
    return postAdd(CFG.addUrl, m.id).then(res => {
      if (res.warning) { showToast(res.warning) }
      else {
        // Tell the sibling purchase-cart island to re-read — same signal the topbar "+" fires.
        // No success toast: the row's "in cart" badge ticks up on the same cart:changed.
        document.dispatchEvent(new CustomEvent('cart:changed'))
      }
      return res
    })
  }

  // A camera-detected barcode resolves the same way as a wedge scan on intake — exact-match
  // add. Sound is OUTCOME-based: bright blip on a real add, error tone when it can't (unknown
  // code, or a stock/quantity cap surfaced as `warning`).
  function handleScanned(code) {
    scanLookup(CFG.searchUrl, code, 'materials').then(m => {
      if (!m) { errorBeep(); showToast(`No material found for barcode ${code}`); return }
      addMaterial(m).then(res => {
        if (res && res.warning) errorBeep()
        else beep()
      })
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
      // Read the live DOM value, not `query` — React state can lag the scanner's final
      // keystroke, and Enter arrives immediately after it.
      const code = (inputRef.current?.value || '').trim()
      if (looksLikeScan(code)) {
        scanLookup(CFG.searchUrl, code, 'materials').then(m => {
          if (m) { addMaterial(m); setQuery('') }      // clear for the next scan
          else showToast(`No material found for barcode ${code}`)
        })
        return
      }
      const item = materials[active]
      if (item) addMaterial(item)
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
          placeholder="Search materials..."
          className="ps-input"
        />
        {/* Shows whenever the dropdown is open, not only when text is typed: the panel can
            be open on an empty query, and touch users have no Esc key to dismiss it. Clears
            the text AND closes the panel. Mirror of the sale-search island. */}
        {(open || query) && (
          <button className="ps-clear" aria-label="Close search"
                  onMouseDown={e => e.preventDefault()}
                  onClick={() => { setQuery(''); setFocused(false); inputRef.current?.blur() }}>
            <i className="bi bi-x-lg"></i>
          </button>
        )}
        {/* Camera scan — only where the browser supports it; a USB/BT wedge scanner needs
            no button (it just types into the field). */}
        {canScan && (
          <button className="ps-scan" aria-label="Scan barcode with camera" type="button"
                  onMouseDown={e => e.preventDefault()}
                  onClick={() => setScanning(true)}>
            <i className="bi bi-upc-scan"></i>
          </button>
        )}
        {/* <kbd className="ps-kbd">⌘K</kbd> */}
      </div>

      {scanning && (
        <CameraScanner onDetect={handleScanned} onClose={() => setScanning(false)} />
      )}

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
                    {suggested
                      ? <><i className="bi bi-star-fill"></i> Most purchased</>
                      : <><i className="bi bi-box-seam"></i> Materials</>}
                    <span className="ps-section-count">· {materials.length}</span>
                  </div>
                  {materials.map((m, i) => {
                    const isActive = active === i
                    return (
                      <div key={`m${m.id}`}
                           className={`ps-row ${isActive ? 'is-active' : ''}`}
                           onMouseEnter={() => setActive(i)}
                           onClick={() => addMaterial(m)}>
                        <div className="ps-thumb"><i className="bi bi-box-seam"></i></div>
                        <div className="ps-body">
                          <div className="ps-name"><Highlight text={m.name} query={debounced} /></div>
                          <div className="ps-sub">
                            Supplier: <Highlight text={m.supplier} query={debounced} />
                            <span className="ps-dot">·</span>
                            <span className="ps-cost">₱{m.price}</span>
                            {/* Reference quantity (Material.quantity + unit) — how the item is
                                defined, NOT stock. A hint for how many to order. */}
                            {m.qty ? <>
                              <span className="ps-dot">·</span>
                              <span className="ps-refqty">Ref: {m.qty} {m.unit}</span>
                            </> : null}
                          </div>
                        </div>
                        {m.in_cart > 0 && (
                          <span className="ps-incart" title={`${m.in_cart} in cart`}>
                            <i className="bi bi-cart-check-fill"></i> {m.in_cart} in cart
                          </span>
                        )}
                        <button className="ps-add" aria-label={`Add ${m.name}`}
                                onClick={e => { e.stopPropagation(); addMaterial(m) }}>
                          <i className="bi bi-plus-lg"></i>
                        </button>
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
