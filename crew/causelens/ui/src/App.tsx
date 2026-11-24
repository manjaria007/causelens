import { Component, useState, type ReactNode } from 'react'
import { ChatEmbed, useChatLauncher } from '@kirocrew/app-sdk'
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
  slotKeyFor,
  type AgentKey,
  type ConsoleState,
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

function AgentChat() {
  const [key, setKey] = useState<AgentKey>(DEFAULT_AGENT)
  const choice = agentFor(key)
  const { openChat } = useChatLauncher()
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
        <ChatEmbed key={choice.key} slotKey={slotKeyFor(choice.key)} agent={choice.agent} placeholder={choice.starter} startAtBottom />
      </PanelBoundary>
      <div className="mt-2">
        <Btn onClick={() => openChat({ agent: choice.agent, message: choice.starter, autoSend: false })}>
          Open {choice.label} in a full chat (draft, not sent)
        </Btn>
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
        // sandbox="" : no scripts, no same-origin access, no top navigation. The console's own source links are in-page anchors (#call-N),
        // which still work inside the frame.
        <iframe
          title={`CauseLens reference console (${STATIC_LABEL.toLowerCase()})`}
          srcDoc={state.html}
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
