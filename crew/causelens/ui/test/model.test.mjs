// Unit tests of src/model.ts (pure logic; no host, no Crew). Run: node --test test/
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { AGENTS, DEFAULT_AGENT, CONSOLE_URL, STATIC_LABEL, STATIC_EXPLANATION, LIVE_EXPLANATION, agentFor, consoleStateFrom, slotKeyFor } from '../src/model.ts'

test('three agents, named as registered from agents/*.json', () => {
  assert.deepEqual(AGENTS.map((a) => a.agent), ['causelens-detective', 'causelens-scientist', 'causelens-experiment-designer'])
  assert.equal(DEFAULT_AGENT, 'detective')
  assert.equal(new Set(AGENTS.map((a) => slotKeyFor(a.key))).size, 3)
  assert.equal(agentFor('nonsense').key, 'detective')
})

test('the console probe never falls back to a finding', () => {
  assert.deepEqual(consoleStateFrom({ ok: true, status: 200, contentType: 'text/html; charset=utf-8' }), { kind: 'ready' })
  for (const r of [{ ok: false, status: 404 }, { ok: true, status: 200, contentType: 'application/json' }, { error: 'network down' }]) {
    const s = consoleStateFrom(r)
    assert.equal(s.kind, 'missing')
    assert.match(s.detail, /not available|could not be loaded|not an HTML page/)
  }
  assert.equal(CONSOLE_URL, '/apps/causelens/ui/console.html')
})

test('no text in the model states a finding', () => {
  const text = JSON.stringify(AGENTS) + STATIC_LABEL + STATIC_EXPLANATION + LIVE_EXPLANATION
  for (const planted of ['Checkout v4.2', 'Homepage hero', 'safari', 'mobile', '2026-', 'moderate']) {
    assert.ok(!text.toLowerCase().includes(planted.toLowerCase()), planted)
  }
  assert.ok(!/\d\s*pp\b|\d%/.test(text), 'no effect sizes or rates')
  assert.match(STATIC_EXPLANATION, /not the output of a native Crew agent/)
  assert.match(LIVE_EXPLANATION, /insufficient/)
})
