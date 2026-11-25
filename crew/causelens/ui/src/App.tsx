import { Component, useCallback, useEffect, useRef, useState, type ReactNode } from 'react'
import { ChatEmbed, useAppApi, useChatLauncher } from '@kirocrew/app-sdk'
import { Btn, Card, CardTitle, EmptyState, PageHeader } from '@kirocrew/app-sdk/ui'
import { FileWarning } from 'lucide-react'
import {
  AGENTS,
  DEFAULT_AGENT,
  LIVE_EXPLANATION,
  STATIC_EXPLANATION,
  STATIC_LABEL,
  agentFor,
  consoleStateFrom,
  embedForSrcdoc,
  freshSlotName,
  launchOptions,
  openTemporarySession,
  type AgentChoice,
  type AgentKey,
  type ConsoleState,
  type TemporarySession,
} from './model'
// The packaged console (generated into ui/console.html by scripts/package_crew_app.py), inlined as text by Vite at build time.
import consoleHtml from '../console.html?raw'

/** Keeps a failure in one panel (for example a host without ChatEmbed) from blanking the whole page. */
class PanelBoundary extends Component<{ what: string; children: ReactNode }, { error: string | null }> {
  state = { error: null as string | null }
  static getDerivedStateFromError(err: unknown) {
    return { error: err instanceof Error ? err.message : String(err) }
  }
  render() {
    if (this.state.error) {
      return (
        <p role="alert" className="text-sm">
          The {this.props.what} could not be shown: {this.state.error}
        </p>
      )
    }
    return this.props.children
  }
}

type SessionState = { kind: 'creating' } | TemporarySession

function nonce(): string {
  const bytes = new Uint8Array(4)
  globalThis.crypto.getRandomValues(bytes)
  return Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('')
}

/** One fresh temporary session per agent tab, created when the tab is first shown or when the person asks for a new one. */
function useTemporarySessions() {
  const api = useAppApi()
  const [sessions, setSessions] = useState<Partial<Record<AgentKey, SessionState>>>({})
  const inFlight = useRef(new Set<AgentKey>())
  const start = useCallback(
    (choice: AgentChoice) => {
      if (inFlight.current.has(choice.key)) return
      inFlight.current.add(choice.key)
      setSessions((s) => ({ ...s, [choice.key]: { kind: 'creating' } }))
      const name = freshSlotName(choice.key, Date.now(), nonce())
      openTemporarySession((path, body) => api.post(path, body), choice, name)
        .then((result) => setSessions((s) => ({ ...s, [choice.key]: result })))
        .finally(() => inFlight.current.delete(choice.key))
    },
    [api],
  )
  return { sessions, start }
}

function AgentChat() {
  const [key, setKey] = useState<AgentKey>(DEFAULT_AGENT)
  const choice = agentFor(key)
  const { openChat } = useChatLauncher()
  const { sessions, start } = useTemporarySessions()
  const session = sessions[key]
  useEffect(() => {
    if (!session) start(choice)
  }, [key, session, start, choice])
  return (
    <Card>
      <CardTitle>Investigate with the native agents</CardTitle>
      <p className="text-sm text-muted mb-3">{LIVE_EXPLANATION}</p>
      <div role="tablist" aria-label="CauseLens agent" className="flex flex-wrap gap-2 mb-2">
        {AGENTS.map((a) => (
          <button
            key={a.key}
            type="button"
            role="tab"
            aria-selected={a.key === key}
            onClick={() => setKey(a.key)}
            className={a.key === key ? 'px-3 py-1 rounded border font-semibold' : 'px-3 py-1 rounded border text-muted'}
          >
            {a.label}
          </button>
        ))}
      </div>
      <p className="text-sm mb-3">
        <strong>{choice.label}</strong> ({choice.agent}): {choice.role}
      </p>
      <PanelBoundary what="chat">
        {(!session || session.kind === 'creating') && <p className="text-sm text-muted">Creating a new temporary session…</p>}
        {session?.kind === 'refused' && (
          <p role="alert" className="text-sm">
            {session.detail}
          </p>
        )}
        {session?.kind === 'ready' && (
          <>
            <p className="text-sm text-muted mb-2">
              Temporary session <code>{session.slotKey}</code>: Crew reads no stored memory into it and saves none from it.
            </p>
            <ChatEmbed key={session.slotKey} slotKey={session.slotKey} agent={choice.agent} placeholder={choice.starter} startAtBottom />
          </>
        )}
      </PanelBoundary>
      <div className="mt-2 flex flex-wrap gap-2">
        <Btn onClick={() => start(choice)}>New temporary {choice.label} session</Btn>
        <Btn onClick={() => openChat(launchOptions(choice))}>Open {choice.label} in a full temporary chat (draft, not sent)</Btn>
      </div>
    </Card>
  )
}

function ReferenceConsole() {
  const state: ConsoleState = consoleStateFrom(consoleHtml)
  return (
    <Card>
      <CardTitle>Reference console ({STATIC_LABEL.toLowerCase()})</CardTitle>
      <p className="text-sm text-muted mb-3">
        <strong>{STATIC_LABEL}.</strong> {STATIC_EXPLANATION}
      </p>
      {state.kind === 'missing' && <EmptyState icon={<FileWarning size={20} />} title="Reference console unavailable" subtitle={state.detail} />}
      {state.kind === 'ready' && (
        // sandbox="" : no scripts, no same-origin access, no top navigation. The console's in-page links are rewritten to
        // about:srcdoc#... so a click scrolls within this frame instead of navigating it to the parent page's URL.
        <iframe
          title={`CauseLens reference console (${STATIC_LABEL.toLowerCase()})`}
          srcDoc={embedForSrcdoc(state.html)}
          sandbox=""
          referrerPolicy="no-referrer"
          style={{ width: '100%', height: '70vh', border: '1px solid var(--border, #d5dae3)', borderRadius: 8 }}
        />
      )}
    </Card>
  )
}

// The app's page: native chat with the three CauseLens agents, and the packaged static reference console.
export default function CauseLens() {
  return (
    <>
      <PageHeader title="CauseLens" subtitle="Why did a metric move? Three agents, read-only deterministic analytics, and no overclaiming." />
      <div className="px-6 pb-8 overflow-y-auto flex-1 min-h-0 flex flex-col gap-4">
        <AgentChat />
        <ReferenceConsole />
      </div>
    </>
  )
}
