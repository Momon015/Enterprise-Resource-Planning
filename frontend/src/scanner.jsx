import { useEffect, useRef, useState } from 'react'

// Retail-relevant symbologies. We intersect this with what the device actually supports
// (getSupportedFormats) so the detector never chokes on an unknown format.
const DESIRED_FORMATS = [
  'ean_13', 'ean_8', 'upc_a', 'upc_e',
  'code_128', 'code_39', 'code_93', 'itf', 'codabar',
]

// The camera path uses the native BarcodeDetector API — zero dependencies, so it respects
// the project's no-CDN / vendored-only rule. It ships on Android Chrome (the common PH
// phone) but NOT iOS Safari, so callers hide the camera button where this returns false and
// fall back to the USB/Bluetooth wedge scanner, which works everywhere.
export function scannerSupported() {
  return typeof window !== 'undefined' && 'BarcodeDetector' in window
}

// A short confirmation beep so the cashier doesn't have to look at the screen to know a scan
// landed. ONE shared AudioContext, reused across scans — a fresh one per beep hits the browser's
// live-context cap (~6) during continuous scanning and the beeps turn erratic then silent. A
// tiny gain envelope avoids the click a bare start/stop makes. Best-effort — audio is a nicety.
let _audioCtx = null
export function beep() {
  try {
    const Ctx = window.AudioContext || window.webkitAudioContext
    if (!Ctx) return
    if (!_audioCtx) _audioCtx = new Ctx()
    const ctx = _audioCtx
    if (ctx.state === 'suspended') ctx.resume()   // autoplay may park it until a gesture
    const now = ctx.currentTime
    const osc = ctx.createOscillator()
    const gain = ctx.createGain()
    osc.type = 'square'
    osc.frequency.value = 880
    // The original snappy square beep, minus the click: hold 0.05, then a short linear
    // release to 0 so the tone stops cleanly instead of popping.
    gain.gain.setValueAtTime(0.05, now)
    gain.gain.setValueAtTime(0.05, now + 0.10)
    gain.gain.linearRampToValueAtTime(0, now + 0.12)
    osc.connect(gain)
    gain.connect(ctx.destination)
    osc.start(now)
    osc.stop(now + 0.13)
  } catch (e) { /* audio is a nicety — stay silent on failure */ }
}

// A distinct "rejected" sound — two short descending low tones — for a scan that couldn't be
// added: an unknown barcode, or a stock/quantity cap. Deliberately unlike the bright success
// blip so the cashier hears the difference without looking at the screen.
export function errorBeep() {
  try {
    const Ctx = window.AudioContext || window.webkitAudioContext
    if (!Ctx) return
    if (!_audioCtx) _audioCtx = new Ctx()
    const ctx = _audioCtx
    if (ctx.state === 'suspended') ctx.resume()
    const now = ctx.currentTime
    const tone = (freq, start, dur) => {
      const osc = ctx.createOscillator()
      const gain = ctx.createGain()
      osc.type = 'square'
      osc.frequency.value = freq
      gain.gain.setValueAtTime(0.06, now + start)
      gain.gain.setValueAtTime(0.06, now + start + dur - 0.02)
      gain.gain.linearRampToValueAtTime(0, now + start + dur)
      osc.connect(gain)
      gain.connect(ctx.destination)
      osc.start(now + start)
      osc.stop(now + start + dur + 0.01)
    }
    tone(320, 0, 0.11)      // buh —
    tone(200, 0.13, 0.16)   // buh (lower) = rejected
  } catch (e) { /* audio is a nicety — stay silent on failure */ }
}

// Full-screen camera overlay that streams the rear camera and decodes barcodes ~4×/sec,
// staying open for continuous scanning until `onClose`. Each item adds ONE unit (bump the
// quantity in the cart for 6-of-a-kind, like a Puregold lane). Two guards stop one physical
// item from becoming duplicate lines:
//   • a 1s cooldown between adds (paces scanning, kills accidental double-fires), and
//   • a leave-frame gate — the SAME code won't re-add until the frame has gone empty, so
//     holding an item in view doesn't keep adding it. A different item still adds right away.
export function CameraScanner({ onDetect, onClose }) {
  const videoRef = useRef(null)
  const [error, setError] = useState('')
  // Kept in a ref so a new `onDetect` identity from the parent doesn't restart the camera.
  const onDetectRef = useRef(onDetect)
  useEffect(() => { onDetectRef.current = onDetect }, [onDetect])

  useEffect(() => {
    let stream = null
    let detector = null
    let timer = null
    let cancelled = false
    let busy = false
    const COOLDOWN_MS = 1000       // min gap between two adds
    let lastCode = ''              // the code of the most recent add
    let lastAddAt = 0              // when it was added
    let armed = true               // true once the frame has cleared → ready for the next read

    async function start() {
      try {
        let formats = DESIRED_FORMATS
        try {
          const supported = await window.BarcodeDetector.getSupportedFormats()
          const usable = DESIRED_FORMATS.filter(f => supported.includes(f))
          if (usable.length) formats = usable
        } catch (e) { /* fall back to the full desired set */ }
        detector = new window.BarcodeDetector({ formats })

        stream = await navigator.mediaDevices.getUserMedia({
          video: { facingMode: 'environment' }, audio: false,
        })
        if (cancelled) { stream.getTracks().forEach(t => t.stop()); return }
        const video = videoRef.current
        if (!video) return
        video.srcObject = stream
        await video.play()

        timer = setInterval(async () => {
          if (busy || cancelled || !videoRef.current) return
          busy = true
          try {
            const codes = await detector.detect(videoRef.current)
            if (!codes || !codes.length) {
              armed = true                 // empty frame → ready for the next item / a re-scan
            } else {
              const code = codes[0].rawValue
              const now = Date.now()
              const cooling = now - lastAddAt < COOLDOWN_MS
              // The same code sitting in frame after its add is NOT a new scan; only a
              // different item, or the same one re-presented after the frame cleared, is.
              const sameStillInFrame = code === lastCode && !armed
              if (code && !cooling && !sameStillInFrame) {
                lastCode = code
                lastAddAt = now
                armed = false
                // No sound here — the caller plays success/error based on the ADD result
                // (stock caps and unknown codes get the distinct error tone).
                onDetectRef.current(code)
              }
            }
          } catch (e) { /* transient decode miss — keep looping */ }
          busy = false
        }, 250)
      } catch (e) {
        setError(e && e.name === 'NotAllowedError'
          ? 'Camera permission denied. Allow camera access to scan.'
          : 'Could not start the camera on this device.')
      }
    }
    start()

    return () => {
      cancelled = true
      if (timer) clearInterval(timer)
      if (stream) stream.getTracks().forEach(t => t.stop())
    }
  }, [])

  return (
    <div className="scanner-overlay" role="dialog" aria-label="Barcode scanner">
      <div className="scanner-stage">
        <video ref={videoRef} className="scanner-video" playsInline muted></video>
        <div className="scanner-reticle"></div>
        <button className="scanner-close" onClick={onClose} aria-label="Close scanner">
          <i className="bi bi-x-lg"></i>
        </button>
        <div className="scanner-hint">
          {error
            ? <span className="scanner-error"><i className="bi bi-exclamation-triangle-fill"></i> {error}</span>
            : <><i className="bi bi-upc-scan"></i> Point the camera at a barcode</>}
        </div>
      </div>
    </div>
  )
}
