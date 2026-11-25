// Pure data and decisions for the CauseLens page. No React, no host SDK: tested directly in Node (tests/ui/model.test.mjs).
//
// Nothing here states a finding. The page never shows a fixed answer; findings come only from the native agents' recorded tool calls
// or, clearly labelled, from the packaged static reference console.

export type AgentKey = 'detective' | 'scientist' | 'experiment-designer'

export interface AgentChoice {
  key: AgentKey
  /** The native agent name registered from agents/<key>.json. */
  agent: string
  label: string
  /** What this agent does, in one sentence. */
  role: string
  /** A starting message, seeded as a draft: the person presses Send. */
  starter: string
}

export const AGENTS: readonly AgentChoice[] = [
  {
    key: 'detective',
    agent: 'causelens-detective',
    label: 'Detective',
    role: 'Checks data quality, confirms the change is real, and finds when it started and where it is concentrated. It does not say why.',
    starter: 'Why did checkout conversion fall? Check the data first, confirm the change and localize it.',
  },
  {
    key: 'scientist',
    agent: 'causelens-scientist',
    label: 'Scientist',
    role: 'Tests each logged event against a named hypothesis and estimates the effect of a supported one, with its checks, assumptions and evidence strength.',
    starter: 'Test the candidate explanations for the checkout conversion change, using the windows the Detective found.',
  },
  {
    key: 'experiment-designer',
    agent: 'causelens-experiment-designer',
    label: 'Experiment designer',
    role: 'Recommends the randomized experiment that would settle what the data cannot, sized by the power tools.',
    starter: 'Design the experiment that would settle the checkout conversion question, using the same windows.',
  },
]

export const DEFAULT_AGENT: AgentKey = 'detective'

export function agentFor(key: string): AgentChoice {
  return AGENTS.find((a) => a.key === key) ?? AGENTS[0]
}

/**
 * Every CauseLens chat is a Crew **temporary** session: Crew neither injects stored memories into it nor consolidates it into memory
 * afterwards, so one investigation cannot carry another's numbers or windows into its answer.
 *
 * `ChatEmbed` has no memory-mode prop and creates a missing slot as persistent, so the page creates a fresh slot first, through
 * `POST /api/chat/slots` with `memory_mode: "temporary"`, checks the mode Crew reports back, and only then mounts the embed on that key.
 * An existing session is never reused: each slot name is new.
 */
export const MEMORY_MODE = 'temporary' as const

/** A new slot name for one agent: never one an earlier page view or session used. */
export function freshSlotName(key: AgentKey, now: number, nonce: string): string {
  return `causelens-${key}-${now}-${nonce}`
}

export interface SlotCreateRequest {
  name: string
  agent: string
  memory_mode: typeof MEMORY_MODE
}

export function temporarySlotRequest(choice: AgentChoice, name: string): SlotCreateRequest {
  return { name, agent: choice.agent, memory_mode: MEMORY_MODE }
}

export type TemporarySession =
  | { kind: 'ready'; slotKey: string; memoryMode: typeof MEMORY_MODE }
  | { kind: 'refused'; detail: string }

/** Accept a created slot only if Crew reports it under the requested name, for this agent, in temporary mode. */
export function checkCreatedSlot(choice: AgentChoice, name: string, slot: unknown): TemporarySession {
  const s = (slot && typeof slot === 'object' ? slot : {}) as Record<string, unknown>
  if (s.key !== name) return { kind: 'refused', detail: `Crew did not return the new session ${name}; it is not used.` }
  if (s.memory_mode !== MEMORY_MODE) {
    return { kind: 'refused', detail: `Crew reported memory mode ${JSON.stringify(s.memory_mode ?? null)} for ${name}, not temporary; it is not used.` }
  }
  if (s.agent !== choice.agent) {
    return { kind: 'refused', detail: `Crew bound ${name} to ${JSON.stringify(s.agent ?? null)}, not ${choice.agent}; it is not used.` }
  }
  return { kind: 'ready', slotKey: name, memoryMode: MEMORY_MODE }
}

/** Create the temporary slot and check it. A failed request is reported as it was returned, and no chat is mounted. */
export async function openTemporarySession(
  post: (path: string, body: unknown) => Promise<unknown>,
  choice: AgentChoice,
  name: string,
): Promise<TemporarySession> {
  let slot: unknown
  try {
    slot = await post('/api/chat/slots', temporarySlotRequest(choice, name))
  } catch (err) {
    return { kind: 'refused', detail: `Crew refused to create a temporary session: ${err instanceof Error ? err.message : String(err)}` }
  }
  return checkCreatedSlot(choice, name, slot)
}

/** The app name Crew knows this page by; the embed's default sender sends it as `embed_app`. */
export const APP_NAME = 'causelens'
/** The path the embed's default sender posts to. */
export const CHAT_SEND_PATH = '/api/chat?ws=1'

/** What `ChatEmbed` hands an `onSend` host: forwarded unchanged as `meta`, as the default sender does. */
export interface EmbedSendMeta {
  sendId?: string
  files?: string[]
  pastes?: unknown[]
}

/**
 * The body of one chat send. Same fields as the embed's default sender (message, slot, agent, embed_app, meta), plus
 * `memory_mode: "temporary"` on every message: if the verified slot has disappeared, Crew recreates it as temporary rather than persistent,
 * and if a slot under this key exists in another mode, Crew refuses the send instead of running it.
 */
export function temporarySendBody(slotKey: string, choice: AgentChoice, message: string, meta?: EmbedSendMeta) {
  return { message, slot: slotKey, agent: choice.agent, embed_app: APP_NAME, memory_mode: MEMORY_MODE, ...(meta ? { meta } : {}) }
}

/** Whether Crew accepted a send, read as the embed's default sender reads it (`ok`, `queued` or a `work` receipt). */
function accepted(reply: unknown): boolean {
  if (!reply || typeof reply !== 'object' || Array.isArray(reply)) return false
  const r = reply as Record<string, unknown>
  return Boolean(r.ok || r.queued || r.work)
}

/**
 * The `onSend` transport for one verified temporary slot. Every send carries the temporary mode. A refusal (an HTTP error such as a 409
 * memory-mode mismatch, or a reply that is not an acceptance) rejects, so the embed shows it and hands the text back. It is never retried,
 * with or without the mode.
 */
export function temporarySender(post: (path: string, body: unknown) => Promise<unknown>, slotKey: string, choice: AgentChoice) {
  return async (message: string, meta?: EmbedSendMeta): Promise<unknown> => {
    const reply = await post(CHAT_SEND_PATH, temporarySendBody(slotKey, choice, message, meta))
    if (!accepted(reply)) {
      const error = reply && typeof reply === 'object' ? (reply as Record<string, unknown>).error : undefined
      throw new Error(typeof error === 'string' && error ? error : 'Crew did not accept the message.')
    }
    return reply
  }
}

/** The full-chat launch: no slotKey, so Crew opens a NEW session in temporary mode with the starter as an unsent draft. */
export function launchOptions(choice: AgentChoice) {
  return { agent: choice.agent, message: choice.starter, autoSend: false, memoryMode: MEMORY_MODE }
}

/**
 * The static console is bundled into the page at build time (Vite `?raw` import of ../console.html) and shown through a sandboxed iframe's
 * `srcDoc`. Crew 0.13.1's app asset route refuses `.html` (HTTP 403), so the file cannot be loaded by URL.
 */
export type ConsoleState = { kind: 'ready'; html: string } | { kind: 'missing'; detail: string }

/** Decide what the console panel shows from the bundled text. Never falls back to any finding. */
export function consoleStateFrom(html: string | null | undefined): ConsoleState {
  if (!html || !html.trim()) return { kind: 'missing', detail: 'The reference console is not bundled in this build.' }
  if (!/^\s*<!doctype html>/i.test(html)) return { kind: 'missing', detail: 'The bundled reference console is not an HTML page.' }
  return { kind: 'ready', html }
}

/**
 * The console's in-page links (`href="#call-N"` source links and `href="#step-..."` step links) for display inside a `srcDoc` frame.
 *
 * An `about:srcdoc` document takes the parent page's URL as its base, so a bare `#call-N` resolves to `<parent URL>#call-N` and clicking it
 * navigates the frame away to the parent page (a new request, and the console is lost). `about:srcdoc#call-N` is the same document's own
 * fragment, so the click only scrolls. The packaged console file itself is not changed; only the copy handed to `srcDoc` is.
 */
export function embedForSrcdoc(html: string): string {
  return html.replace(/href="#([A-Za-z][\w-]*)"/g, 'href="about:srcdoc#$1"')
}

export const STATIC_LABEL = 'Static reference result'
export const STATIC_EXPLANATION =
  "This console was written by the repository's template reasoner from the reference dataset's logged tool results. It is not the output of a native Crew agent or of a hosted model, and it does not update when the agents run."
export const LIVE_EXPLANATION =
  'Answers here come from the native CauseLens agents. Every number they state should come from a recorded MCP tool call shown in the transcript; if a tool fails or the evidence is insufficient, the agent should say so and recommend an experiment rather than name a cause.'
