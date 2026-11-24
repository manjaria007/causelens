// Browser regression for the embedded console's in-page links, in real headless Chrome driven over the DevTools protocol.
//
// The parent page is served over HTTP from 127.0.0.1 and contains the iframe markup the BUILT page renders (via render.mjs and the test
// stubs; this is not the Crew dashboard). A source link inside the sandboxed srcDoc frame is clicked with a real mouse event. The test
// requires that the frame scrolls to the link's target, still holds the console, and that no request reaches the network or the server.
// A control renders the same console with bare `#call-N` links and shows the failure the rewrite fixes.
//
// Skipped when no Chrome/Chromium is found (set CHROME to its executable to choose one).
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { spawn } from 'node:child_process'
import { existsSync, mkdtempSync, readFileSync, rmSync } from 'node:fs'
import { createServer } from 'node:http'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { setTimeout as sleep } from 'node:timers/promises'
import { html as pageHtml, packagedConsole } from './render.mjs'

const CANDIDATES = [
  process.env.CHROME,
  '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
  '/Applications/Chromium.app/Contents/MacOS/Chromium',
  '/usr/bin/google-chrome', '/usr/bin/google-chrome-stable', '/usr/bin/chromium', '/usr/bin/chromium-browser',
].filter(Boolean)
const CHROME = CANDIDATES.find((p) => existsSync(p))

/** Fail with a message instead of hanging when the browser does not answer. */
function within(ms, what, promise) {
  let timer
  return Promise.race([promise, new Promise((_, reject) => { timer = setTimeout(() => reject(new Error(`timed out after ${ms} ms: ${what}`)), ms) })])
    .finally(() => clearTimeout(timer))
}

const attrEscape = (s) => s.replaceAll('&', '&amp;').replaceAll('"', '&quot;').replaceAll('<', '&lt;').replaceAll('>', '&gt;')
const iframeMarkup = pageHtml.match(/<iframe[^>]*><\/iframe>/)[0]
const controlMarkup = `<iframe title="control" srcDoc="${attrEscape(packagedConsole)}" sandbox="" style="width:100%;height:70vh"></iframe>`

function parent(markup) {
  return `<!doctype html><html><head><meta charset="utf-8"><title>parent</title></head><body><h1>parent page</h1>${markup}</body></html>`
}

async function startServer() {
  const hits = []
  const server = createServer((req, res) => {
    hits.push(req.url)
    if (req.url.startsWith('/control')) return res.end(parent(controlMarkup))
    if (req.url.startsWith('/page')) return res.end(parent(iframeMarkup))
    res.statusCode = 404
    res.end('not found')
  })
  await new Promise((r) => server.listen(0, '127.0.0.1', r))
  return { server, hits, base: `http://127.0.0.1:${server.address().port}` }
}

async function startChrome() {
  const profile = mkdtempSync(join(tmpdir(), 'causelens-chrome-'))
  const proc = spawn(CHROME, [
    '--headless=new', '--disable-gpu', '--no-first-run', '--no-default-browser-check', '--remote-debugging-port=0',
    `--user-data-dir=${profile}`, '--window-size=1200,900', 'about:blank',
  ], { stdio: 'ignore', detached: true }) // its own process group, so the whole browser (helpers too) can be stopped
  proc.unref()
  const portFile = join(profile, 'DevToolsActivePort')
  for (let i = 0; i < 100 && !existsSync(portFile); i++) await sleep(100)
  if (!existsSync(portFile)) { try { process.kill(-proc.pid, 'SIGKILL') } catch {} throw new Error('Chrome did not open its DevTools port') }
  const port = readFileSync(portFile, 'utf8').split('\n')[0]
  const targets = await within(10000, 'DevTools target list', fetch(`http://127.0.0.1:${port}/json/list`).then((r) => r.json()))
  const ws = new WebSocket(targets.find((t) => t.type === 'page').webSocketDebuggerUrl)
  await within(10000, 'DevTools connection', new Promise((r, j) => { ws.onopen = r; ws.onerror = j }))
  let id = 0
  const pending = new Map()
  const listeners = []
  ws.onmessage = (m) => {
    const msg = JSON.parse(m.data)
    if (msg.id && pending.has(msg.id)) {
      const { resolve, reject } = pending.get(msg.id)
      pending.delete(msg.id)
      msg.error ? reject(new Error(JSON.stringify(msg.error))) : resolve(msg.result)
    } else if (msg.method) listeners.forEach((f) => f(msg))
  }
  const send = (method, params = {}, sessionId) => within(15000, method, new Promise((resolve, reject) => {
    const n = ++id
    pending.set(n, { resolve, reject })
    ws.send(JSON.stringify(sessionId ? { id: n, method, params, sessionId } : { id: n, method, params }))
  }))
  const close = () => {
    try { ws.close() } catch {}
    try { process.kill(-proc.pid, 'SIGKILL') } catch { try { proc.kill('SIGKILL') } catch {} }
    rmSync(profile, { recursive: true, force: true })
  }
  return { send, listeners, close }
}

// The sandboxed srcdoc frame has an opaque origin, so Chrome runs it out of process: it is a separate DevTools target, reached through
// Target.setAutoAttach. Its state is read in an isolated world (the frame itself may not run scripts).
async function frameState(cdp, session, frameId, target) {
  const { executionContextId } = await cdp.send('Page.createIsolatedWorld', { frameId, worldName: 'probe' }, session)
  const expression = `JSON.stringify({url: location.href, title: document.title, scrollY: scrollY, height: innerHeight,
    top: document.getElementById(${JSON.stringify(target)})?.getBoundingClientRect().top ?? null})`
  const { result } = await cdp.send('Runtime.evaluate', { expression, contextId: executionContextId, returnByValue: true }, session)
  return JSON.parse(result.value)
}

/** Load `path`, click a visible source link inside the iframe whose target is out of view, with a real mouse event, and report what happened. */
async function clickThrough(cdp, base, path) {
  const requests = []
  let frameSession = null
  let frameTargetId = null
  const onEvent = (m) => {
    if (m.method === 'Network.requestWillBeSent') requests.push(m.params.request.url)
    if (m.method === 'Target.attachedToTarget' && m.params.targetInfo.type === 'iframe') {
      frameSession = m.params.sessionId
      frameTargetId = m.params.targetInfo.targetId
      cdp.send('Network.enable', {}, frameSession).catch(() => {})
      cdp.send('Runtime.runIfWaitingForDebugger', {}, frameSession).catch(() => {})
    }
  }
  cdp.listeners.push(onEvent)
  await cdp.send('Target.setAutoAttach', { autoAttach: true, waitForDebuggerOnStart: false, flatten: true })
  const loaded = new Promise((r) => cdp.listeners.push((m) => m.method === 'Page.loadEventFired' && !m.sessionId && r()))
  await cdp.send('Page.navigate', { url: base + path })
  await within(15000, `load of ${path}`, loaded)
  for (let i = 0; i < 50 && !frameSession; i++) await sleep(100)
  assert.ok(frameSession, 'the console frame is attached as its own target')
  await cdp.send('Page.enable', {}, frameSession)
  await sleep(300)

  // where the iframe sits in the parent, and where the link sits inside the iframe
  const parentDoc = (await cdp.send('DOM.getDocument', { depth: 1 })).root
  const { nodeId: iframeNode } = await cdp.send('DOM.querySelector', { nodeId: parentDoc.nodeId, selector: 'iframe' })
  const frameBox = (await cdp.send('DOM.getBoxModel', { nodeId: iframeNode })).model.content
  // the first source link painted in the frame's viewport whose target lies below it, measured in the frame's own viewport
  const { executionContextId: ctx } = await cdp.send('Page.createIsolatedWorld', { frameId: frameTargetId, worldName: 'locate' }, frameSession)
  const located = (await cdp.send('Runtime.evaluate', {
    contextId: ctx, returnByValue: true,
    expression: `(() => {
      for (const a of document.querySelectorAll('a[href*="#call-"]')) {
        const r = a.getBoundingClientRect()
        if (!(r.width > 0 && r.top >= 0 && r.bottom <= innerHeight && a.checkVisibility())) continue
        const hit = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2)
        if (!(hit === a || a.contains(hit))) continue // laid out but not hit, e.g. inside a closed <details>
        const id = a.getAttribute('href').split('#')[1]
        const t = document.getElementById(id)
        if (t && t.getBoundingClientRect().top > innerHeight) return { x: r.left + r.width / 2, y: r.top + r.height / 2, href: a.getAttribute('href'), id }
      }
      return null
    })()`,
  }, frameSession)).result.value
  assert.ok(located, 'a visible, clickable source link whose target is out of view')
  const target = located.id
  const before = await frameState(cdp, frameSession, frameTargetId, target)
  const x = frameBox[0] + located.x
  const y = frameBox[1] + located.y
  const requestsBeforeClick = requests.length
  for (const type of ['mousePressed', 'mouseReleased']) {
    await cdp.send('Input.dispatchMouseEvent', { type, x, y, button: 'left', clickCount: 1 })
  }
  await sleep(1000)
  let after
  try {
    after = await frameState(cdp, frameSession, frameTargetId, target)
  } catch (e) {
    after = { error: String(e) }
  }
  cdp.listeners.splice(cdp.listeners.indexOf(onEvent), 1)
  return { before, after, newRequests: requests.slice(requestsBeforeClick), href: located.href, target }
}

test('a source link in the embedded console scrolls within the frame and makes no request', { skip: !CHROME && 'no Chrome/Chromium found', timeout: 60000 }, async () => {
  const { server, hits, base } = await startServer()
  const cdp = await startChrome()
  try {
    await cdp.send('Page.enable')
    await cdp.send('Network.enable')
    const r = await clickThrough(cdp, base, '/page')
    assert.equal(r.href, `about:srcdoc#${r.target}`, 'the clicked link is the rewritten one')
    assert.equal(r.before.title, 'CauseLens investigation', 'the console is in the frame before the click')
    assert.ok(r.before.top > r.before.height, 'the target starts below the visible part of the frame')
    assert.ok(!r.after.error, r.after.error)
    assert.equal(r.after.title, 'CauseLens investigation', 'the console is still in the frame after the click')
    assert.ok(r.after.url.startsWith('about:srcdoc'), `the frame stayed on its srcdoc document (got ${r.after.url})`)
    assert.ok(r.after.scrollY > r.before.scrollY, 'the frame scrolled')
    assert.ok(r.after.top >= -1 && r.after.top < r.after.height, `the target is in view (top ${r.after.top}, height ${r.after.height})`)
    assert.deepEqual(r.newRequests, [], 'no network request after the click')
    assert.deepEqual(hits.filter((h) => !h.startsWith('/favicon')), ['/page'], `only the page load reached the server (${hits.join(', ')})`)
  } finally {
    cdp.close()
    server.closeAllConnections() // a browser that is gone may leave sockets that would keep this process alive
    server.close()
  }
})

test('control: with bare #call-N links the same click navigates the frame away from the console', { skip: !CHROME && 'no Chrome/Chromium found', timeout: 60000 }, async () => {
  const { server, hits, base } = await startServer()
  const cdp = await startChrome()
  try {
    await cdp.send('Page.enable')
    await cdp.send('Network.enable')
    const r = await clickThrough(cdp, base, '/control')
    assert.equal(r.href, `#${r.target}`)
    assert.equal(r.before.title, 'CauseLens investigation')
    // what the reviewer's finding predicts: the frame resolves the bare fragment against the parent URL and loads the parent page into itself
    assert.equal(r.after.title, 'parent', `the frame now shows the parent page (after: ${JSON.stringify(r.after)})`)
    assert.ok(String(r.after.url).startsWith(`${base}/control#`), `the frame navigated to the parent URL (${r.after.url})`)
    assert.ok(r.newRequests.includes(`${base}/control`), 'and requested it again')
    assert.equal(hits.filter((h) => h.startsWith('/control')).length, 2)
  } finally {
    cdp.close()
    server.closeAllConnections() // a browser that is gone may leave sockets that would keep this process alive
    server.close()
  }
})
