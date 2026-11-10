/**
 * Ghost Run — Desktop half.
 *
 * Registers a MAIN-AREA workspace pane (area: 'panes' via host.openWorkspace,
 * falling back to ctx.register({ area: 'panes', ... })) with:
 *   - per-file checkboxes
 *   - struck-through deletions
 *   - modified / added badges
 *   - unified diff view
 *   - Verify (advisory Pass/Fail) / Apply selected / Discard wired to the local ghost UI API
 *     (hermes ghost ui → http://127.0.0.1:<port>/?token=<random>)
 *     Set the API base to that full URL. Host must be 127.0.0.1 or localhost.
 *     These fetches are cross-origin: packaged Desktop is a file:// renderer,
 *     so the browser sends Origin: null. The UI allows only that origin.
 *
 * Imports are limited to `@hermes/plugin-sdk` and `react` (desktop surface lint).
 * If the Desktop app is unavailable, use `hermes ghost ui` for the same pane.
 */
import { Button, Tip } from '@hermes/plugin-sdk'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { jsx as _jsx, jsxs as _jsxs, Fragment as _Fragment } from 'react/jsx-runtime'

const ID = 'ghost-run'
const DEFAULT_API = 'http://127.0.0.1:8765'

function apiUrl(base, path) {
  let origin = DEFAULT_API
  let token = ''
  try {
    const parsed = new URL(base || DEFAULT_API)
    origin = parsed.origin
    token = parsed.searchParams.get('token') || ''
  } catch (err) {
    origin = DEFAULT_API
  }
  const url = new URL(path, origin + '/')
  if (token) url.searchParams.set('token', token)
  return url.toString()
}

const CSS = `
.ghost-pane{display:flex;flex-direction:column;height:100%;min-height:0;color:var(--ui-text-primary, #e6e6e6);background:var(--ui-bg, #0f1115);font-size:12px}
.ghost-bar{display:flex;gap:6px;align-items:center;flex-wrap:wrap;padding:8px 10px;border-bottom:1px solid var(--ui-border, #2a2e37)}
.ghost-bar .meta{flex:1;min-width:120px;color:var(--ui-text-tertiary, #9aa3b2);font-size:11px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.ghost-body{display:grid;grid-template-columns:minmax(200px,1fr) minmax(220px,1.3fr);min-height:0;flex:1}
.ghost-list,.ghost-diff{overflow:auto;min-height:0;padding:8px 10px}
.ghost-list{border-right:1px solid var(--ui-border, #2a2e37)}
.ghost-row{display:grid;grid-template-columns:18px 40px 62px 1fr;gap:6px;align-items:center;padding:6px 4px;border-radius:6px;cursor:pointer}
.ghost-row:hover,.ghost-row[data-active=true]{background:var(--chrome-action-hover, #1c2230)}
.ghost-badge{font-size:9px;font-weight:700;text-transform:uppercase;letter-spacing:.04em;padding:2px 6px;border-radius:999px;text-align:center;width:fit-content}
.ghost-badge[data-kind=added]{background:#143526;color:#3dd68c}
.ghost-badge[data-kind=modified]{background:#3a2a10;color:#f5a524}
.ghost-badge[data-kind=deleted]{background:#3a1520;color:#f31260}
.ghost-risk{font-size:9px;font-weight:800;text-transform:uppercase;padding:2px 5px;border-radius:5px;text-align:center}
.ghost-risk[data-level=high]{background:#5a1020;color:#ff8fa8;box-shadow:0 0 0 1px #f31260 inset}
.ghost-risk[data-level=med]{background:#3a2a10;color:#f5c46a}
.ghost-risk[data-level=low]{background:#143526;color:#7be8b0}
.ghost-reason{grid-column:3 / -1;color:var(--ui-text-tertiary,#9aa3b2);font-size:10.5px;margin-top:-3px}
.ghost-ai{display:block;color:#b9c6ff}
.ghost-blocked{background:#2a1018;border:1px solid #5a2030;border-radius:6px;padding:6px 8px;margin-bottom:6px;color:#ffb4c4;font-size:11px}
.ghost-path{font-family:var(--font-mono, ui-monospace, monospace);font-size:11px;word-break:break-all}
.ghost-path[data-kind=deleted]{color:#f31260;text-decoration:line-through}
.ghost-diff pre{margin:0;white-space:pre-wrap;font-family:var(--font-mono, ui-monospace, monospace);font-size:11px;line-height:1.4;background:#12151c;border:1px solid var(--ui-border,#2a2e37);border-radius:8px;padding:10px}
.ghost-add{color:#3dd68c}.ghost-del{color:#f31260}.ghost-hunk{color:#6c8cff}
.ghost-empty{color:var(--ui-text-tertiary,#9aa3b2);padding:16px 4px}
.ghost-counts{color:var(--ui-text-tertiary,#9aa3b2);margin-bottom:6px}
.ghost-hint{padding:8px 10px;color:var(--ui-text-tertiary,#9aa3b2);font-size:11px;border-top:1px solid var(--ui-border,#2a2e37)}
.ghost-verify{border:1px solid var(--ui-border,#2a2e37);border-radius:6px;padding:6px 8px;margin-bottom:6px;color:var(--ui-text-tertiary,#9aa3b2)}
.ghost-verify[data-status=pass]{background:#143526;color:#7be8b0;border-color:#1f6b45}
.ghost-verify[data-status=fail]{background:#3a1520;color:#ffb4c4;border-color:#5a2030}
.ghost-verify[data-status=error]{background:#3a2a10;color:#f5c46a;border-color:#6a4a16}
.ghost-verify-log{margin:0 0 6px;max-height:140px;overflow:auto;white-space:pre-wrap;font-family:var(--font-mono, ui-monospace, monospace);font-size:10.5px;background:#12151c;border:1px solid var(--ui-border,#2a2e37);border-radius:6px;padding:8px}
`

function colorizeLines(diff) {
  if (!diff) return ['(no unified diff)']
  return diff.split('\n').map((line, i) => {
    let cls = null
    if (line.startsWith('+') && !line.startsWith('+++')) cls = 'ghost-add'
    else if (line.startsWith('-') && !line.startsWith('---')) cls = 'ghost-del'
    else if (line.startsWith('@@')) cls = 'ghost-hunk'
    return _jsx('span', { className: cls || undefined, children: line + '\n' }, i)
  })
}

function VerifyBanner({ verify }) {
  if (!verify) {
    return _jsx('div', {
      className: 'ghost-verify',
      children: 'Verify not run. Advisory only — Apply stays available either way.'
    })
  }
  const cmd = verify.command ? ' · ' + verify.command : ''
  let label = verify.error || 'Verify error'
  let dataStatus = 'error'
  if (verify.status === 'pass') {
    dataStatus = 'pass'
    label = 'Pass — exit ' + verify.exit_code + cmd
  } else if (verify.status === 'fail') {
    dataStatus = 'fail'
    label = 'Fail — ' + (verify.timed_out ? 'timed out' : 'exit ' + verify.exit_code) + cmd
  }
  return _jsxs(_Fragment, {
    children: [
      _jsx('div', { className: 'ghost-verify', 'data-status': dataStatus, children: label }),
      verify.log ? _jsx('pre', { className: 'ghost-verify-log', children: verify.log }) : null
    ]
  })
}

function GhostPane() {
  const [apiBase, setApiBase] = useState(DEFAULT_API)
  const [payload, setPayload] = useState(null)
  const [error, setError] = useState(null)
  const [selected, setSelected] = useState(() => new Set())
  const [active, setActive] = useState(null)
  const [busy, setBusy] = useState(false)
  const [verifying, setVerifying] = useState(false)
  const verifyingRef = useRef(false)
  const [status, setStatus] = useState('')

  const changes = payload && payload.ok ? (payload.changes || []) : []
  const riskOf = (c) => c.risk || { level: 'low', reason: '' }
  const selectLowRisk = () => {
    // Heuristic level only; the model never selects or applies.
    setSelected(new Set(changes.filter((c) => riskOf(c).level === 'low').map((c) => c.path)))
  }

  const refresh = useCallback(async () => {
    try {
      const res = await fetch(apiUrl(apiBase, '/api/diff'))
      if (!res.ok) throw new Error('HTTP ' + res.status)
      const data = await res.json()
      setPayload((prev) => {
        // Keep the on-screen verify result while a run is in flight.
        if (verifyingRef.current && prev && Object.prototype.hasOwnProperty.call(prev, 'verify')) {
          data.verify = prev.verify
        }
        return data
      })
      setError(null)
      if (data.ok && Array.isArray(data.changes)) {
        setSelected((prev) => {
          if (prev.size === 0) return new Set(data.changes.map((c) => c.path))
          const alive = new Set(data.changes.map((c) => c.path))
          return new Set([...prev].filter((p) => alive.has(p)))
        })
        setActive((cur) => cur || (data.changes[0] && data.changes[0].path) || null)
      }
    } catch (err) {
      setError(
        'Cannot reach Ghost UI at ' +
          apiBase +
          '. Start it with: hermes ghost ui and paste the printed http://127.0.0.1:<port>/?token= URL. (' +
          String(err && err.message ? err.message : err) +
          ')'
      )
    }
  }, [apiBase])

  useEffect(() => {
    refresh()
    const t = setInterval(refresh, 4000)
    return () => clearInterval(t)
  }, [refresh])

  const activeChange = useMemo(
    () => changes.find((c) => c.path === active) || changes[0] || null,
    [changes, active]
  )

  const toggle = (path, on) => {
    setSelected((prev) => {
      const next = new Set(prev)
      if (on) next.add(path)
      else next.delete(path)
      return next
    })
  }

  const runVerify = async () => {
    // Advisory only. Do not set `busy` — that would disable Apply selected.
    verifyingRef.current = true
    setVerifying(true)
    setStatus('Running verify…')
    try {
      const res = await fetch(apiUrl(apiBase, '/api/verify'), { method: 'POST' })
      const data = await res.json()
      if (data.verify) {
        setPayload((prev) => ({ ...(prev || { ok: true }), verify: data.verify }))
        if (data.verify.status === 'pass') setStatus('Verify: Pass')
        else if (data.verify.status === 'fail') setStatus('Verify: Fail (Apply stays available)')
        else setStatus(data.verify.error || 'Verify error')
      } else {
        setStatus(data.error || 'verify failed')
      }
    } catch (err) {
      setStatus(String(err && err.message ? err.message : err))
    } finally {
      verifyingRef.current = false
      setVerifying(false)
    }
  }

  const applySelected = async () => {
    // Verify Pass/Fail never gates this. Apply stays a human choice.
    const paths = [...selected]
    if (!paths.length) {
      setStatus('Select at least one path')
      return
    }
    setBusy(true)
    try {
      const res = await fetch(apiUrl(apiBase, '/api/apply'), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ paths })
      })
      const data = await res.json()
      setStatus(data.ok ? 'Applied ' + (data.actions || []).length + ' path(s)' : data.error || 'apply failed')
      setSelected(new Set())
      await refresh()
    } catch (err) {
      setStatus(String(err && err.message ? err.message : err))
    } finally {
      setBusy(false)
    }
  }

  const discardAll = async () => {
    setBusy(true)
    try {
      const res = await fetch(apiUrl(apiBase, '/api/discard'), { method: 'POST' })
      const data = await res.json()
      setStatus(data.message || (data.ok ? 'Discarded' : 'discard failed'))
      setSelected(new Set())
      setActive(null)
      await refresh()
    } catch (err) {
      setStatus(String(err && err.message ? err.message : err))
    } finally {
      setBusy(false)
    }
  }

  return _jsxs('div', {
    className: 'ghost-pane',
    children: [
      _jsx('style', { children: CSS }),
      _jsxs('div', {
        className: 'ghost-bar',
        children: [
          _jsx('div', {
            className: 'meta',
            title: payload && payload.ok ? payload.real_root : '',
            children: error
              ? 'UI offline'
              : payload && payload.ok
                ? 'real: ' + payload.real_root
                : (payload && payload.error) || 'No sandbox'
          }),
          _jsx(Button, {
            size: 'sm',
            variant: 'ghost',
            disabled: busy,
            onClick: refresh,
            children: 'Refresh'
          }),
          _jsx(Button, {
            size: 'sm',
            variant: 'ghost',
            disabled: busy || !changes.length,
            onClick: selectLowRisk,
            children: 'Select only low-risk'
          }),
          _jsx(Button, {
            size: 'sm',
            variant: 'ghost',
            disabled: verifying,
            onClick: runVerify,
            children: verifying ? 'Verifying…' : 'Verify'
          }),
          _jsx(Button, {
            size: 'sm',
            disabled: busy || !changes.length,
            onClick: applySelected,
            children: 'Apply selected'
          }),
          _jsx(Button, {
            size: 'sm',
            variant: 'destructive',
            disabled: busy,
            onClick: discardAll,
            children: 'Discard'
          })
        ]
      }),
      error
        ? _jsxs('div', {
            className: 'ghost-empty',
            style: { padding: '12px 10px' },
            children: [
              error,
              _jsx('div', {
                style: { marginTop: 8 },
                children: _jsx(Tip, {
                  children: 'Desktop cannot run the agent half; keep `hermes ghost ui` up so this pane can Apply/Discard.'
                })
              }),
              _jsxs('label', {
                style: { display: 'block', marginTop: 10, fontSize: 11 },
                children: [
                  'API base ',
                  _jsx('input', {
                    value: apiBase,
                    onChange: (e) => setApiBase(e.target.value),
                    style: {
                      width: '100%',
                      marginTop: 4,
                      background: '#12151c',
                      color: 'inherit',
                      border: '1px solid #2a2e37',
                      borderRadius: 6,
                      padding: '4px 6px'
                    }
                  })
                ]
              })
            ]
          })
        : _jsxs('div', {
            className: 'ghost-body',
            children: [
              _jsxs('div', {
                className: 'ghost-list',
                children: [
                  _jsx('div', {
                    className: 'ghost-counts',
                    children:
                      payload && payload.counts
                        ? '+' +
                          payload.counts.added +
                          ' added · ~' +
                          payload.counts.modified +
                          ' modified · −' +
                          payload.counts.deleted +
                          ' deleted'
                        : ''
                  }),
                  _jsx(VerifyBanner, { verify: payload && payload.verify }),
                  payload && payload.risk_counts
                    ? _jsx('div', {
                        className: 'ghost-counts',
                        children:
                          payload.risk_counts.high + ' high · ' + payload.risk_counts.med +
                          ' med · ' + payload.risk_counts.low + ' low (sorted by risk)'
                      })
                    : null,
                  payload && payload.blocked && payload.blocked.length
                    ? _jsx('div', {
                        className: 'ghost-blocked',
                        children: payload.blocked.length + ' blocked attempt(s): ' +
                          payload.blocked.slice(-3).map((b) => b.reason).join(' | ')
                      })
                    : null,
                  payload && payload.ai && payload.ai.status === 'ok' && payload.ai.summary
                    ? _jsx('div', { className: 'ghost-counts', children: 'AI: ' + payload.ai.summary })
                    : null,
                  changes.length === 0
                    ? _jsx('div', {
                        className: 'ghost-empty',
                        children: 'No pending changes.'
                      })
                    : changes.map((ch) =>
                        _jsxs(
                          'div',
                          {
                            className: 'ghost-row',
                            'data-active': active === ch.path,
                            onClick: () => setActive(ch.path),
                            children: [
                              _jsx('input', {
                                type: 'checkbox',
                                checked: selected.has(ch.path),
                                onChange: (e) => {
                                  e.stopPropagation()
                                  toggle(ch.path, e.target.checked)
                                },
                                onClick: (e) => e.stopPropagation(),
                                'aria-label': 'select ' + ch.path
                              }),
                              _jsx('span', {
                                className: 'ghost-risk',
                                'data-level': riskOf(ch).level,
                                children: riskOf(ch).level
                              }),
                              _jsx('span', {
                                className: 'ghost-badge',
                                'data-kind': ch.kind,
                                children: ch.kind
                              }),
                              _jsx('span', {
                                className: 'ghost-path',
                                'data-kind': ch.kind,
                                children: ch.path
                              }),
                              _jsxs('span', {
                                className: 'ghost-reason',
                                children: [
                                  riskOf(ch).reason,
                                  riskOf(ch).ai_note
                                    ? _jsx('span', { className: 'ghost-ai', children: 'AI: ' + riskOf(ch).ai_note })
                                    : null
                                ]
                              })
                            ]
                          },
                          ch.path
                        )
                      )
                ]
              }),
              _jsxs('div', {
                className: 'ghost-diff',
                children: [
                  _jsx('div', {
                    className: 'ghost-counts',
                    children: activeChange
                      ? activeChange.kind + ' — ' + activeChange.path
                      : 'Unified diff'
                  }),
                  _jsx('pre', {
                    children: activeChange
                      ? colorizeLines(activeChange.unified_diff)
                      : 'Select a file.'
                  })
                ]
              })
            ]
          }),
      status
        ? _jsx('div', { className: 'ghost-hint', children: status })
        : _jsx('div', {
            className: 'ghost-hint',
            children:
              'Verify runs one configured test in the ghost copy and does not block Apply. Apply copies selected ghost changes onto the real folder. Discard deletes the sandbox.'
          })
    ]
  })
}

export default {
  name: ID,
  version: '0.1.0',
  async register(ctx) {
    const render = () => _jsx(GhostPane, {})

    // Prefer openWorkspace (main-area tab) when the Desktop host exposes it.
    if (ctx && typeof ctx.openWorkspace === 'function') {
      try {
        ctx.openWorkspace(ID + ':diff', {
          title: 'Ghost Run',
          render,
          minWidth: '22rem'
        })
        console.info('[ghost-run] opened Desktop workspace pane via openWorkspace')
        return
      } catch (err) {
        console.warn('[ghost-run] openWorkspace failed, falling back to panes register', err)
      }
    }

    if (ctx && typeof ctx.register === 'function') {
      try {
        ctx.register({
          area: 'panes',
          id: ID + '-diff',
          title: 'Ghost Run',
          data: {
            placement: 'main',
            minWidth: '22rem',
            dock: { pane: 'workspace', pos: 'right' }
          },
          render
        })
        console.info('[ghost-run] registered Desktop pane ghost-run-diff')
        return
      } catch (err) {
        console.warn('[ghost-run] panes register failed:', err)
      }
    }

    console.info(
      '[ghost-run] Desktop pane host unavailable; use hermes ghost ui on localhost'
    )
  }
}
