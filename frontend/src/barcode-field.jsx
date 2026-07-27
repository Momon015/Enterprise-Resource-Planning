import { StrictMode, useEffect, useState } from 'react'
import { createRoot } from 'react-dom/client'

import { CameraScanner, scannerSupported, beep } from './scanner.jsx'

// Scan-to-FILL, not scan-to-add: this island sits beside the material form's barcode input
// and writes a scanned code INTO it, rather than adding anything to a cart. Two jobs:
//   1. Swallow the trailing Enter a USB/wedge scanner sends — otherwise scanning the code
//      into the field would submit the half-filled material form. Runs on every browser.
//   2. Offer a camera "Scan" button where BarcodeDetector exists (Android Chrome), which
//      fills the field on detect. Hidden where unsupported; the wedge path still works.
const el = document.getElementById('barcode-scan-root')
const CFG = el ? el.dataset : {}

function fillInput(input, value) {
  input.value = value
  // Let the form's required-guard / any listeners react to the new value.
  input.dispatchEvent(new Event('input', { bubbles: true }))
  input.dispatchEvent(new Event('change', { bubbles: true }))
}

function BarcodeFieldScanner() {
  const [scanning, setScanning] = useState(false)
  const target = CFG.target ? document.getElementById(CFG.target) : null

  // Swallow Enter on the barcode field so a wedge scanner's terminator can't submit the
  // form mid-edit. Registered regardless of camera support — wedge works everywhere.
  useEffect(() => {
    if (!target) return
    const onKey = (e) => { if (e.key === 'Enter') e.preventDefault() }
    target.addEventListener('keydown', onKey)
    return () => target.removeEventListener('keydown', onKey)
  }, [target])

  if (!scannerSupported() || !target) return null   // camera button only where usable

  return (
    <>
      <button type="button" className="bc-scan-btn" onClick={() => setScanning(true)}>
        <i className="bi bi-upc-scan"></i> Scan barcode
      </button>
      {scanning && (
        <CameraScanner
          onDetect={(code) => { fillInput(target, code); beep(); setScanning(false) }}
          onClose={() => setScanning(false)}
        />
      )}
    </>
  )
}

if (el) {
  createRoot(el).render(<StrictMode><BarcodeFieldScanner /></StrictMode>)
}
