import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'

// Stub island for the product list. (Note: a filtered list is also a strong
// htmx candidate — see README — but this is here if you want it in React.)
function ProductList({ businessSlug }) {
  return (
    <div style={{ padding: '1rem', border: '2px dashed #16a34a', borderRadius: '12px' }}>
      <h3 style={{ margin: 0 }}>🧾 React Product List island (stub)</h3>
      <p style={{ margin: '.25rem 0 0', color: '#555' }}>
        Business: <code>{businessSlug || '(none passed)'}</code>
      </p>
    </div>
  )
}

const el = document.getElementById('product-list-root')
if (el) {
  createRoot(el).render(
    <StrictMode>
      <ProductList businessSlug={el.dataset.business} />
    </StrictMode>
  )
}
