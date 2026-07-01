import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'

// Stub island for the purchase cart session. Build out the real UI here.
function PurchaseCart({ businessSlug }) {
  return (
    <div style={{ padding: '1rem', border: '2px dashed #0ea5e9', borderRadius: '12px' }}>
      <h3 style={{ margin: 0 }}>📦 React Purchase Cart island (stub)</h3>
      <p style={{ margin: '.25rem 0 0', color: '#555' }}>
        Business: <code>{businessSlug || '(none passed)'}</code>
      </p>
    </div>
  )
}

const el = document.getElementById('purchase-cart-root')
if (el) {
  createRoot(el).render(
    <StrictMode>
      <PurchaseCart businessSlug={el.dataset.business} />
    </StrictMode>
  )
}
