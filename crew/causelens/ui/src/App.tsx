import { ChatEmbed } from '@kirocrew/app-sdk'
import { Card, CardTitle, PageHeader } from '@kirocrew/app-sdk/ui'

// The app's page: a native chat with the CauseLens agents, and a packaged static reference console.
// The console below is NOT produced by a native agent. It is the repository's template-written console for the reference dataset.
export default function CauseLens() {
  return (
    <>
      <PageHeader
        title="CauseLens"
        subtitle="Why did a metric move? Three agents, read-only deterministic analytics, and no overclaiming."
      />
      <div className="px-6 pb-8 overflow-y-auto flex-1 min-h-0">
        <Card>
          <CardTitle>Investigate</CardTitle>
          <p className="text-sm text-muted mb-3">
            Ask a question about the reference dataset, for example "Why did checkout conversion fall?". The
            causelens-detective, causelens-scientist and causelens-experiment-designer agents call the CauseLens tools; every number in
            their answers should come from a recorded tool call, and their Activity shows which.
          </p>
          <ChatEmbed
            slotKey="causelens-investigation"
            agent="causelens-detective"
            placeholder="Why did checkout conversion fall?"
          />
        </Card>
        <Card>
          <CardTitle>Reference console (static)</CardTitle>
          <p className="text-sm text-muted mb-3">
            <strong>Static reference result.</strong> This page was written by the repository's template reasoner from the reference
            dataset's logged tool results. It is not the output of a native Crew agent or of a hosted model.
          </p>
          <iframe
            title="CauseLens reference console (static)"
            src="/apps/causelens/ui/console.html"
            sandbox=""
            style={{ width: '100%', height: '70vh', border: '1px solid var(--border, #d5dae3)', borderRadius: 8 }}
          />
        </Card>
      </div>
    </>
  )
}
