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

/** One chat session per agent, owned by this page, so switching agents does not mix their transcripts. */
export function slotKeyFor(key: AgentKey): string {
  return `causelens-${key}`
}

/** The static console is a file inside this app, served from the app's UI route. */
export const CONSOLE_URL = '/apps/causelens/ui/console.html'

export type ConsoleState =
  | { kind: 'checking' }
  | { kind: 'ready' }
  | { kind: 'missing'; detail: string }

/** Decide what the console panel shows from the result of probing CONSOLE_URL. Never falls back to any finding. */
export function consoleStateFrom(result: { ok: boolean; status?: number; contentType?: string | null } | { error: string }): ConsoleState {
  if ('error' in result) return { kind: 'missing', detail: `The reference console could not be loaded (${result.error}).` }
  if (!result.ok) return { kind: 'missing', detail: `The reference console is not available in this install (HTTP ${result.status ?? 'error'}).` }
  if (result.contentType && !result.contentType.toLowerCase().includes('text/html')) {
    return { kind: 'missing', detail: 'The reference console file is not an HTML page.' }
  }
  return { kind: 'ready' }
}

export const STATIC_LABEL = 'Static reference result'
export const STATIC_EXPLANATION =
  "This console was written by the repository's template reasoner from the reference dataset's logged tool results. It is not the output of a native Crew agent or of a hosted model, and it does not update when the agents run."
export const LIVE_EXPLANATION =
  'Answers here come from the native CauseLens agents. Every number they state should come from a recorded MCP tool call shown in the transcript; if a tool fails or the evidence is insufficient, the agent should say so and recommend an experiment rather than name a cause.'
