// Renders the BUILT page (dist/index.mjs) with react-dom/server against the TEST STUBS in test/stubs/. This checks what the page asks the
// host to do and what it says; it is not a Crew session and proves nothing about native execution.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync, writeFileSync, mkdtempSync, rmSync } from 'node:fs'
import { join, dirname } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const ui = join(here, '..')
const built = readFileSync(join(ui, 'dist', 'index.mjs'), 'utf8')
const stub = (name) => pathToFileURL(join(here, 'stubs', name)).href
const rewired = built
  .replaceAll('"@kirocrew/app-sdk/ui"', JSON.stringify(stub('app-sdk-ui.mjs')))
  .replaceAll('"@kirocrew/app-sdk"', JSON.stringify(stub('app-sdk.mjs')))
  .replaceAll('"lucide-react"', JSON.stringify(stub('lucide-react.mjs')))
const tmp = mkdtempSync(join(ui, 'node_modules', '.page-test-'))  // removed by the cleanup below
process.on('exit', () => rmSync(tmp, { recursive: true, force: true }))  // inside the ui tree so 'react' resolves from node_modules
const file = join(tmp, 'page.mjs')
writeFileSync(file, rewired)

const React = await import('react')
const { renderToStaticMarkup } = await import('react-dom/server')
const { default: Page } = await import(pathToFileURL(file).href)
const { calls } = await import(stub('app-sdk.mjs'))
const html = renderToStaticMarkup(React.createElement(Page))
const text = html.replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ')

test('the built bundle imports only the host SDK, react and lucide', () => {
  const specifiers = [...built.matchAll(/from\s+"([^"]+)"/g)].map((m) => m[1]).sort()
  assert.deepEqual([...new Set(specifiers)], ['@kirocrew/app-sdk', '@kirocrew/app-sdk/ui', 'lucide-react', 'react', 'react/jsx-runtime'])
  assert.ok(!/callTool|\/api\/mcp|mcp\/call/.test(built), 'no invented MCP-call method or route')
  assert.equal((built.match(/\bfetch\(/g) || []).length, 1, 'one fetch: the probe of the packaged console')
  assert.ok(built.includes('"/apps/causelens/ui/console.html"'))
})

test('the page mounts a native ChatEmbed for the selected agent, defaulting to the Detective', () => {
  assert.equal(calls.chatEmbed.length, 1)
  const props = calls.chatEmbed[0]
  assert.equal(props.agent, 'causelens-detective')
  assert.equal(props.slotKey, 'causelens-detective')
  assert.equal(props.onSend, undefined)  // the embed's own documented send path (POST /api/chat)
  for (const label of ['Detective', 'Scientist', 'Experiment designer']) assert.ok(text.includes(label), label)
  assert.match(html, /role="tablist"/)
  assert.equal((html.match(/role="tab"/g) || []).length, 3)
})

test('the static console is labelled and is not shown before it is found', () => {
  assert.match(text, /Static reference result\./)
  assert.match(text, /not the output of a native Crew agent or of a hosted model/)
  assert.match(text, /Loading the reference console/)  // the first render, before the probe answers
  assert.ok(!html.includes('<iframe'))
})

test('the page states no finding of its own', () => {
  for (const planted of ['Checkout v4.2', 'Homepage hero', 'safari', '2026-09-20', 'moderate', '-0.6']) {
    assert.ok(!text.toLowerCase().includes(planted.toLowerCase()), planted)
  }
})
