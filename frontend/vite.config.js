import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { resolve } from 'path'

// Each entry below becomes one self-contained bundle in ../static/react/.
// A Django template mounts an island by dropping in:
//   <div id="sale-cart-root" data-business="{{ current_business.slug }}"></div>
//   <script type="module" src="{% static 'react/sale-cart.js' %}"></script>
export default defineConfig({
  plugins: [react()],
  build: {
    // Vite writes the compiled bundles straight into Django's static dir.
    outDir: resolve(__dirname, '../static/react'),
    emptyOutDir: true,
    rollupOptions: {
      input: {
        'sale-cart': resolve(__dirname, 'src/sale-cart.jsx'),
        'purchase-cart': resolve(__dirname, 'src/purchase-cart.jsx'),
        'product-list': resolve(__dirname, 'src/product-list.jsx'),
      },
      output: {
        // Stable, hash-free filenames so Django templates can reference them directly.
        entryFileNames: '[name].js',
        chunkFileNames: '[name].js',
        assetFileNames: '[name][extname]',
      },
    },
  },
})
