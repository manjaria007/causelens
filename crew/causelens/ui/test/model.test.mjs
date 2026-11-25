// Unit tests of src/model.ts (pure logic; no host, no Crew). Run: node --test test/
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { AGENTS, DEFAULT_AGENT, STATIC_LABEL, STATIC_EXPLANATION, LIVE_EXPLANATION, agentFor, consoleStateFrom, freshSlotName, launchOptions, openTemporarySession, checkCreatedSlot, temporarySender, temporarySendBody, CHAT_SEND_PATH } from '../src/model.ts'

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
    [{ key: 'n', memory_mode: 'temporary', agent: 'someone-else' }, /bound n to "someone-else"/],
    [{ key: 'n', memory_mode: 'temporary' }, /bound n to null, not causelens-detective/],
    [{ key: 'n', memory_mode: 'temporary', agent: null }, /bound n to null, not causelens-detective/],
    [{ key: 'n', memory_mode: 'temporary', agent: 7 }, /bound n to 7, not causelens-detective/],
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

/**
 * A stand-in for the gateway's two chat routes, written from Crew 0.13.1's documented behaviour (not Crew itself): POST /api/chat creates
 * a missing slot with the body's memory_mode (persistent when absent) and refuses an existing slot whose mode differs (409).
 */
function fakeGateway() {
  const slots = new Map()
  const sends = []
  const post = async (path, body) => {
    sends.push({ path, body: structuredClone(body) })
    const have = slots.get(body.slot)
    if (have && body.memory_mode && have !== body.memory_mode) {
      throw Object.assign(new Error('HTTP 409: memory_mode mismatch'), { status: 409 })
    }
    if (!have) slots.set(body.slot, body.memory_mode ?? 'persistent')
    return { ok: true }
  }
  return { slots, sends, post }
}

test('every embedded send carries the temporary mode, the slot, the agent and the meta unchanged', async () => {
  const d = AGENTS[0]
  const gw = fakeGateway()
  gw.slots.set('s1', 'temporary')
  const send = temporarySender(gw.post, 's1', d)
  const meta = { sendId: 'send-1', files: ['/tmp/a.csv'], pastes: [{ id: 1, text: 'x' }] }
  await send('first', meta)
  await send('second')
  assert.deepEqual(gw.sends, [
    { path: CHAT_SEND_PATH, body: { message: 'first', slot: 's1', agent: 'causelens-detective', embed_app: 'causelens', memory_mode: 'temporary', meta } },
    { path: CHAT_SEND_PATH, body: { message: 'second', slot: 's1', agent: 'causelens-detective', embed_app: 'causelens', memory_mode: 'temporary' } },
  ])
  assert.deepEqual(temporarySendBody('s1', d, 'm', meta).meta, meta)
})

test('a verified slot that disappears is recreated as temporary, never persistent', async () => {
  const gw = fakeGateway()
  gw.slots.set('s1', 'temporary')
  const send = temporarySender(gw.post, 's1', AGENTS[1])
  await send('one')
  gw.slots.delete('s1') // closed or lost on the gateway between two messages
  await send('two')
  assert.equal(gw.slots.get('s1'), 'temporary')
  assert.ok(gw.sends.every((x) => x.body.memory_mode === 'temporary'))
  // control: the default sender's body (no memory_mode) would have recreated it as persistent
  const control = fakeGateway()
  await control.post(CHAT_SEND_PATH, { message: 'two', slot: 's1', agent: AGENTS[1].agent })
  assert.equal(control.slots.get('s1'), 'persistent')
})

test('a mode mismatch is refused once and never retried, with or without the mode', async () => {
  const gw = fakeGateway()
  gw.slots.set('s1', 'persistent') // a same-key session in another mode
  const send = temporarySender(gw.post, 's1', AGENTS[2])
  await assert.rejects(send('hello', { sendId: 'x' }), /409: memory_mode mismatch/)
  assert.equal(gw.sends.length, 1)
  assert.equal(gw.sends[0].body.memory_mode, 'temporary')
  assert.equal(gw.slots.get('s1'), 'persistent')
})

test('a reply that is not an acceptance rejects with Crew\'s own error and is not retried', async () => {
  const calls = []
  for (const [reply, why] of [[{ ok: false, error: 'slot is closed' }, /slot is closed/], [{}, /did not accept/], [null, /did not accept/]]) {
    const send = temporarySender(async (path, body) => { calls.push(body); return reply }, 's1', AGENTS[0])
    await assert.rejects(send('m'), why)
  }
  assert.equal(calls.length, 3)
  assert.ok(calls.every((b) => b.memory_mode === 'temporary'))
  for (const ok of [{ ok: true }, { queued: true, queue_id: 'q' }, { work: { id: 'w' } }]) {
    assert.deepEqual(await temporarySender(async () => ok, 's1', AGENTS[0])('m'), ok)
  }
})
