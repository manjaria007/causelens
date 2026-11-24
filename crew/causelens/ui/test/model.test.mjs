// Unit tests of src/model.ts (pure logic; no host, no Crew). Run: node --test test/
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { AGENTS, DEFAULT_AGENT, STATIC_LABEL, STATIC_EXPLANATION, LIVE_EXPLANATION, agentFor, consoleStateFrom, slotKeyFor } from '../src/model.ts'

test('three agents, named as registered from agents/*.json', () => {
  assert.deepEqual(AGENTS.map((a) => a.agent), ['causelens-detective', 'causelens-scientist', 'causelens-experiment-designer'])
  assert.equal(DEFAULT_AGENT, 'detective')
  assert.equal(new Set(AGENTS.map((a) => slotKeyFor(a.key))).size, 3)
  assert.equal(agentFor('nonsense').key, 'detective')
})

test('the console state never falls back to a finding', () => {
  const html = '<!doctype html><html><body>x</body></html>'
  assert.deepEqual(consoleStateFrom(html), { kind: 'ready', html })
  for (const bad of ['', '   ', null, undefined, '{"not": "html"}']) {
    const s = consoleStateFrom(bad)
    assert.equal(s.kind, 'missing')
    assert.match(s.detail, /not bundled|not an HTML page/)
  }
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
