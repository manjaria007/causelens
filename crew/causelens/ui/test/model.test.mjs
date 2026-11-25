// Unit tests of src/model.ts (pure logic; no host, no Crew). Run: node --test test/
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { AGENTS, DEFAULT_AGENT, STATIC_LABEL, STATIC_EXPLANATION, LIVE_EXPLANATION, agentFor, consoleStateFrom, freshSlotName, launchOptions, openTemporarySession, checkCreatedSlot } from '../src/model.ts'

test('three agents, named as registered from agents/*.json', () => {
  assert.deepEqual(AGENTS.map((a) => a.agent), ['causelens-detective', 'causelens-scientist', 'causelens-experiment-designer'])
  assert.equal(DEFAULT_AGENT, 'detective')
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

test('a fresh slot name is new for every agent, time and nonce, and never a fixed per-agent key', () => {
  const names = AGENTS.flatMap((a) => [freshSlotName(a.key, 1, 'aa'), freshSlotName(a.key, 2, 'aa'), freshSlotName(a.key, 1, 'bb')])
  assert.equal(new Set(names).size, 9)
  for (const a of AGENTS) assert.ok(!names.includes(`causelens-${a.key}`), 'the old fixed key is never produced')
})

test('a session is created as temporary before any message, and mounted only when Crew reports temporary', async () => {
  const d = AGENTS[0]
  const posted = []
  const echo = async (path, body) => { posted.push({ path, body }); return { key: body.name, memory_mode: body.memory_mode, agent: body.agent } }
  const ok = await openTemporarySession(echo, d, 'causelens-detective-1-aa')
  assert.deepEqual(posted, [{ path: '/api/chat/slots', body: { name: 'causelens-detective-1-aa', agent: 'causelens-detective', memory_mode: 'temporary' } }])
  assert.deepEqual(ok, { kind: 'ready', slotKey: 'causelens-detective-1-aa', memoryMode: 'temporary' })

  for (const [reply, why] of [
    [{ key: 'n', memory_mode: 'persistent' }, /memory mode "persistent".*not temporary/],
    [{ key: 'n' }, /memory mode null/],
    [{ key: 'other', memory_mode: 'temporary' }, /did not return the new session n/],
    [{ key: 'n', memory_mode: 'temporary', agent: 'someone-else' }, /bound n to someone-else/],
    [null, /did not return/],
  ]) {
    const r = checkCreatedSlot(d, 'n', reply)
    assert.equal(r.kind, 'refused')
    assert.match(r.detail, why)
  }
  const failed = await openTemporarySession(async () => { throw new Error('HTTP 409 memory mode mismatch') }, d, 'n')
  assert.deepEqual(failed, { kind: 'refused', detail: 'Crew refused to create a temporary session: HTTP 409 memory mode mismatch' })
})

test('the full-chat launch has no slotKey and asks for a temporary draft', () => {
  for (const a of AGENTS) {
    const o = launchOptions(a)
    assert.deepEqual(o, { agent: a.agent, message: a.starter, autoSend: false, memoryMode: 'temporary' })
    assert.ok(!('slotKey' in o))
  }
})
