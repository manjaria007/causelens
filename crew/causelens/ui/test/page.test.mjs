// Renders the BUILT page against the TEST STUBS (see render.mjs). Checks what the page asks the host for and what it says; it is not a
// Crew session and proves nothing about native execution.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { built, calls, html, text, frame, packagedConsole } from './render.mjs'

test('the built bundle imports only the host SDK, react and lucide', () => {
  const specifiers = [...built.matchAll(/from\s+"([^"]+)"/g)].map((m) => m[1]).sort()
  assert.deepEqual([...new Set(specifiers)], ['@kirocrew/app-sdk', '@kirocrew/app-sdk/ui', 'lucide-react', 'react', 'react/jsx-runtime'])
  assert.ok(!/callTool|\/api\/mcp|mcp\/call/.test(built), 'no invented MCP-call method or route')
  assert.equal((built.match(/\bfetch\(/g) || []).length, 0, 'no fetch: the console is bundled, not loaded by URL')
  assert.ok(!built.includes('/apps/causelens/ui/console.html'), 'Crew 0.13.1 refuses .html on the app asset route')
})

test('the page mounts no chat until a fresh temporary session exists, and never reuses a fixed session key', () => {
  // Under react-dom/server effects do not run: the slot is not created yet, so no ChatEmbed may be mounted.
  assert.equal(calls.chatEmbed.length, 0)
  assert.equal(calls.api.length, 0)
  assert.match(text, /Creating a new temporary session/)
  for (const label of ['Detective', 'Scientist', 'Experiment designer']) assert.ok(text.includes(label), label)
  assert.match(html, /role="tablist"/)
  assert.equal((html.match(/role="tab"/g) || []).length, 3)
  assert.ok(built.includes('/api/chat/slots') && built.includes('memory_mode') && built.includes('memoryMode'))
  for (const fixed of ['"causelens-detective"', '"causelens-scientist"', '"causelens-experiment-designer"']) {
    assert.ok(!built.includes(`slotKey: ${fixed}`), `no fixed slot key ${fixed}`)
  }
})

test('the static console is labelled and shown from the bundle through a sandboxed srcDoc iframe', () => {
  assert.match(text, /Static reference result\./)
  assert.match(text, /not the output of a native Crew agent or of a hosted model/)
  const { attrs, srcdoc } = frame()
  assert.match(attrs, /sandbox=""/)
  assert.ok(!/\ssrc=/.test(attrs), 'no src URL')
  // the frame holds the packaged console, with only its in-page links made srcdoc-local
  assert.equal(srcdoc.replaceAll('href="about:srcdoc#', 'href="#'), packagedConsole)
  assert.ok(!/href="#/.test(srcdoc), 'no bare fragment link is left in the frame')
})

test('the in-page links of the embedded console are srcdoc-local and each has its target', () => {
  const { srcdoc } = frame()
  const links = [...srcdoc.matchAll(/href="about:srcdoc#([\w-]+)"/g)].map((m) => m[1])
  const ids = new Set([...srcdoc.matchAll(/id="([\w-]+)"/g)].map((m) => m[1]))
  assert.ok(links.filter((l) => l.startsWith('call-')).length >= 100 && links.some((l) => l.startsWith('step-')))
  for (const l of new Set(links)) assert.ok(ids.has(l), `#${l} has a target`)
  assert.ok(!/<script/i.test(srcdoc), 'no script in the console')
  assert.ok(/href="#call-\d+"/.test(packagedConsole), 'the packaged console itself keeps its canonical links')
})

test('the page states no finding of its own', () => {
  for (const planted of ['Checkout v4.2', 'Homepage hero', 'safari', '2026-09-20', 'moderate', '-0.6']) {
    assert.ok(!text.toLowerCase().includes(planted.toLowerCase()), planted)
  }
})
