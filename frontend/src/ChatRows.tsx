import { useState } from 'react'
import {
  Button,
  ChatActivity,
  ChatMessage,
  ChatReveal,
  ChatThinking,
  CodeBlock,
  Collapsible,
  InlineAlert,
  SparklesIcon,
} from '@fynns/ui'
import { artifactUrl, type RenderResult, type SessionEvent } from './api'

export type ZoomPayload = { url: string; alt: string; caption: string }

/** Opening user turn: the free-text goal plus the bound volume/model config. */
export function TaskMessage({ goal, source, model }: { goal: string; source: string; model: string }) {
  const markdown = `${goal}\n\n- Volume: \`${source}\`\n- Model: \`${model}\``
  return <ChatMessage role="user" markdown={markdown} />
}

function FrameFigure({
  result,
  event,
  onZoom,
}: {
  result: RenderResult
  event: SessionEvent
  onZoom: (payload: ZoomPayload) => void
}) {
  const [failed, setFailed] = useState(false)
  const url = artifactUrl(result.image)
  const revision = result.scene?.revision ?? 0
  const digest = typeof result.imageSha256 === 'string' ? result.imageSha256.slice(0, 12) : ''
  const alt = `Actual native renderer screenshot for scene revision ${revision} (event ${event.seq})`
  const caption = `Scene revision ${revision} · evidence: event ${event.seq} · sha256 ${digest} · task correctness unverified`
  if (!url || failed) {
    return (
      <InlineAlert
        severity="warning"
        message={`The frame for scene revision ${revision} could not be loaded from the local artifact route. The recorded event and its hash remain valid.`}
      />
    )
  }
  return (
    <figure className="fynns-media-figure">
      <button
        type="button"
        className="gav-media-zoom"
        aria-label={`Zoom: actual native renderer screenshot, scene revision ${revision}`}
        onClick={() => onZoom({ url, alt, caption })}
      >
        <img src={url} alt={alt} onError={() => setFailed(true)} />
      </button>
      <figcaption>{caption}</figcaption>
    </figure>
  )
}

/** One captured native frame bound to its producing event and scene revision. */
export function FrameRow({ event, onZoom }: { event: SessionEvent; onZoom: (payload: ZoomPayload) => void }) {
  const result = event.result
  if (!result) return null
  const revision = result.scene?.revision ?? 0
  const title = event.kind === 'initial_render' ? 'Initial frame' : 'Native frame'
  return (
    <ChatActivity label={`${title} — scene revision ${revision}`} defaultOpen>
      <div className="fynns-unit-stack">
        <FrameFigure result={result} event={event} onZoom={onZoom} />
        <Collapsible title="Native readback" chrome="plain">
          <CodeBlock
            variant="plain"
            code={JSON.stringify(
              {
                nativeState: result.nativeState,
                nonblackPixels: result.nonblackPixels,
                imageSha256: result.imageSha256,
                taskSuccess: result.taskSuccess,
              },
              null,
              2,
            )}
          />
        </Collapsible>
      </div>
    </ChatActivity>
  )
}

/** One model decision with the model-stated reason and the full action JSON. */
export function ActionRow({ event, decision, budget }: { event: SessionEvent; decision: number; budget: number }) {
  const action = event.action
  if (!action) return null
  const summary = action.tool === 'render_scene' ? 'requested a new native frame' : 'requested to finish'
  return (
    <ChatActivity label={`Decision ${decision} of ${budget} — ${summary}`} defaultOpen={false}>
      <div className="fynns-content-flow">
        <p>
          <strong>Model-stated reason:</strong> {action.reason}
        </p>
        <CodeBlock label={`Decision ${decision} action (JSON)`} code={JSON.stringify(action, null, 2)} />
      </div>
    </ChatActivity>
  )
}

/** Recorded pause event: remaining budget, or the exhausted budget. */
export function PauseRow({ event, budget }: { event: SessionEvent; budget: number }) {
  const remaining = typeof event.remaining === 'number' ? event.remaining : 0
  const label =
    remaining > 0
      ? `Paused — ${remaining} of ${budget} decisions remain`
      : `Decision budget exhausted — all ${budget} decisions used`
  return <ChatMessage role="assistant" thinking={<ChatThinking variant="status" label={label} />} />
}

/** Recorded finish event: the model's claim stays visibly unverified. */
export function FinishRow({ event }: { event: SessionEvent }) {
  const claim = typeof event.modelClaim === 'string' ? event.modelClaim.trim() : ''
  const markdown = `${claim ? `> ${claim}\n\n` : ''}Model requested to finish exploration. External task verification: **unverified**.`
  return <ChatMessage role="assistant" markdown={markdown} />
}

/** Recorded error event. A user stop is not a failure. */
export function ErrorRow({ event, stopped }: { event: SessionEvent; stopped: boolean }) {
  const detail = event.message ?? 'unknown error'
  return (
    <div className="fynns-unit-stack">
      <InlineAlert
        severity={stopped ? 'info' : 'error'}
        role={stopped ? 'status' : 'alert'}
        message={
          stopped
            ? `Run stopped — ${detail}. Earlier frames remain valid evidence.`
            : `Session failed — ${detail}`
        }
      />
      {!stopped && event.traceback ? (
        <Collapsible title="Failure traceback" chrome="plain">
          <CodeBlock variant="plain" code={event.traceback} />
        </Collapsible>
      ) : null}
    </div>
  )
}

/** Current provable run state, derived from session state plus recorded events. */
export function LiveStatusRow({ label }: { label: string }) {
  return (
    <ChatMessage
      role="assistant"
      thinking={<ChatThinking variant="status" streaming streamingLabel={label} label={label} />}
    />
  )
}

/** Paused-session control row; the only affordance that resumes the same run. */
export function PauseControlRow({ onContinue, busy }: { onContinue: () => void; busy: boolean }) {
  return (
    <ChatReveal>
      <div className="fynns-control-cluster fynns-control-cluster--start-align">
        <Button variant="primary" onClick={onContinue} disabled={busy} aria-busy={busy || undefined}>
          <SparklesIcon aria-hidden />
          Continue exploration
        </Button>
      </div>
    </ChatReveal>
  )
}
