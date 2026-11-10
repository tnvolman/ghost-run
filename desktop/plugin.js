/**
 * Ghost Run — Desktop half.
 *
 * Thin pane. Load and Apply stay on the loopback review page, which is
 * same-origin in a normal browser and needs no CORS header. This file does
 * not fetch 127.0.0.1. It opens the URL printed by `hermes ghost ui` with
 * ctx.os.openExternal.
 *
 * The workspace tile is host.openWorkspace when the SDK exports it
 * (apps/desktop/src/sdk/index.ts). Older hosts fall back to
 * ctx.register({ area: 'panes' }).
 *
 * The SDK sandboxed iframe is not used. Its default sandbox is scripts only
 * (opaque origin), so the review page's own fetches would send Origin: null.
 *
 * Imports are limited to `@hermes/plugin-sdk` and `react` (desktop surface lint).
 */
import { Button, Tip, host } from '@hermes/plugin-sdk'
import { useState } from 'react'
import { jsx as _jsx, jsxs as _jsxs } from 'react/jsx-runtime'

const ID = 'ghost-run'

function reviewUrl(raw) {
  let parsed
  try {
    parsed = new URL(String(raw || '').trim())
  } catch (err) {
    return null
  }
  const loopback = parsed.protocol === 'http:' && (parsed.hostname === '127.0.0.1' || parsed.hostname === 'localhost')
  const fragment = parsed.hash.startsWith('#') ? parsed.hash.slice(1) : ''
  const token = new URLSearchParams(fragment).get('token')
  // A query token would be sent to the server and can land in logs. Refuse it.
  if (!loopback || !token || parsed.searchParams.has('token')) return null
  return parsed.toString()
}

function GhostPane({ openExternal }) {
  const [url, setUrl] = useState('')
  const [status, setStatus] = useState('')
  const [busy, setBusy] = useState(false)

  const openReview = async () => {
    const target = reviewUrl(url)
    if (!target) {
      setStatus('Paste the full URL from hermes ghost ui. The token belongs in the fragment (#token=), not the query.')
      return
    }
    if (typeof openExternal !== 'function') {
      setStatus('This Desktop build has no ctx.os.openExternal. Open the printed URL in a browser.')
      return
    }
    setBusy(true)
    try {
      const ok = await openExternal(target)
      setStatus(ok === false
        ? 'The browser did not open. Open the printed URL yourself.'
        : 'Opened the review page in your browser. Load and Apply run there.')
    } catch (_err) {
      // Never echo the thrown text. openExternal can include the fragment URL.
      setStatus('Could not open the review page.')
    } finally {
      setBusy(false)
    }
  }

  return _jsxs('div', {
    style: {
      display: 'flex',
      flexDirection: 'column',
      gap: 10,
      height: '100%',
      padding: 12,
      color: 'var(--ui-text-primary, #e6e6e6)',
      background: 'var(--ui-bg, #0f1115)',
      fontSize: 12
    },
    children: [
      _jsx('div', { style: { fontWeight: 650 }, children: 'Ghost Run' }),
      _jsx(Tip, {
        children: 'Run hermes ghost ui, paste the printed URL, then open it in your browser. The page checks the loopback Host and the token. This pane does not fetch that port.'
      }),
      _jsx('input', {
        value: url,
        placeholder: 'http://127.0.0.1:<port>/#token=…',
        'aria-label': 'Ghost Run review URL',
        onChange: (e) => setUrl(e.target.value),
        style: {
          width: '100%',
          background: '#12151c',
          color: 'inherit',
          border: '1px solid #2a2e37',
          borderRadius: 6,
          padding: '6px 8px'
        }
      }),
      _jsx(Button, {
        size: 'sm',
        disabled: busy,
        onClick: openReview,
        children: busy ? 'Opening…' : 'Open review page'
      }),
      status ? _jsx('div', { style: { color: 'var(--ui-text-tertiary, #9aa3b2)' }, children: status }) : null
    ]
  })
}

export default {
  id: ID,
  name: 'Ghost Run',
  version: '0.1.0',
  async register(ctx) {
    const openExternal = ctx && ctx.os && typeof ctx.os.openExternal === 'function'
      ? (target) => ctx.os.openExternal(target)
      : null
    const render = () => _jsx(GhostPane, { openExternal })

    // host.openWorkspace is the SDK door (export const host in sdk/index.ts).
    if (host && typeof host.openWorkspace === 'function') {
      try {
        host.openWorkspace(ID + ':review', {
          title: 'Ghost Run',
          render,
          minWidth: '22rem'
        })
        console.info('[ghost-run] opened Desktop workspace via host.openWorkspace')
        return
      } catch (err) {
        console.warn('[ghost-run] host.openWorkspace failed, falling back to panes register', err)
      }
    }

    if (ctx && typeof ctx.register === 'function') {
      try {
        ctx.register({
          area: 'panes',
          id: ID + '-review',
          title: 'Ghost Run',
          data: {
            placement: 'main',
            minWidth: '22rem',
            dock: { pane: 'workspace', pos: 'right' }
          },
          render
        })
        console.info('[ghost-run] registered Desktop pane via ctx.register')
        return
      } catch (err) {
        console.warn('[ghost-run] panes register failed:', err)
      }
    }

    console.info('[ghost-run] Desktop pane host unavailable; open the hermes ghost ui URL in a browser')
  }
}
