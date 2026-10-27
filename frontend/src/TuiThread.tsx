/**
 * The TUI conversation as fynns core chat rows.
 *
 * Block semantics come from scripts/volume_tui/transcript.py + event_text.py
 * (server-built): task is the user's (▌), » lines are the model's own words,
 * ● ▣ ⚠ ✓ ✗ are measured facts. Component mapping follows core contracts:
 * - model turns -> ChatMessage.thinking + ChatThinking (disclosure while the
 *   chain exists, status strip otherwise; streaming while running)
 * - tool calls -> ChatActivity + ChatActivityStep (active = *-ing, done = past)
 * - frames/answers/errors/notices -> ChatMessage rows (errors are one plain
 *   red line with hover retry) or InlineAlert quiet rows
 * - transport (meta) -> hidden unless the caller passes showPlumbing (Ctrl+H)
 */
import { useEffect, useRef, useState } from 'react'
import {
  BusyRegion,
  ChatActivity,
  ChatActivityStep,
  ChatMessage,
  ChatReveal,
  ChatThinking,
  IconButton,
  InlineAlert,
  LoadingSkeleton,
  ArchiveIcon,
  BotIcon,
  CheckCircleIcon,
  InfoIcon,
  RefreshIcon,
  Tooltip,
  WrenchIcon,
} from '@fynns/ui'
import type { ChatThinkingDetail } from '@fynns/ui'
import { tuiArtifactUrl, formatRunDuration, tuiMark, type TuiBlock } from './tuiApi'

export type ZoomPayload = { url: string; alt: string; caption: string }

function ToolStepStatus(status: string | null): 'active' | 'done' {
  // Core step states are pending/active/done; a refused call is done work —
  // the ✗ stays in the label copy, exactly like the TUI row suffix.
  if (status === 'running') return 'active'
  return 'done'
}

/** One model turn: Thinking/Thought when it reasoned, Working/Model answered when not. */
function ThinkingRow({ block }: { block: TuiBlock }) {
  const running = block.status === 'running'
  const reasoned = (block.data as { reasoning?: unknown } | null)?.reasoning === true
  const label = reasoned ? 'Thought' : 'Model answered'
  const streamingLabel = reasoned ? 'Thinking' : 'Working'
  const body = block.body.trim()
  // Thought stays label-only (no star); Model answered owns BotIcon. Neither
  // falls back to the core Sparkles default.
  const reasonedIcon = null
  const answeredIcon = <BotIcon aria-hidden />
  return (
    <ChatMessage
      role="assistant"
      thinking={
        <ChatThinking
          variant={body ? 'disclosure' : 'status'}
          streaming={running}
          streamingLabel={streamingLabel}
          label={label}
          summary={block.subtitle || undefined}
          details={body ? [{ id: 'thought', text: body }] : []}
          icon={reasoned ? reasonedIcon : answeredIcon}
          marker={reasoned ? 'none' : undefined}
        />
      }
    />
  )
}

/** Context checkpoint: housekeeping summary, never a » answer line. */
function CompactionRow({ block }: { block: TuiBlock }) {
  const running = block.status === 'running'
  const body = block.body.trim()
  return (
    <ChatMessage
      role="assistant"
      thinking={
        <ChatThinking
          variant={body ? 'disclosure' : 'status'}
          streaming={running}
          streamingLabel="Context checkpoint"
          label="Context checkpoint"
          summary={block.subtitle || undefined}
          details={body ? [{ id: 'checkpoint', text: body }] : []}
          icon={<ArchiveIcon aria-hidden />}
        />
      }
    />
  )
}

/** Terminal run: whole history collapsed into one summary disclosure. */
export function WorkedRow({ blocks, totalSeconds }: { blocks: TuiBlock[]; totalSeconds: number | null }) {
  const label = totalSeconds == null ? 'Worked' : `Worked for ${formatRunDuration(totalSeconds)}`
  const details: ChatThinkingDetail[] = blocks.map((block, index) => {
    const id = `worked-${index}`
    switch (block.kind) {
      case 'thinking': {
        const reasoned = (block.data as { reasoning?: unknown } | null)?.reasoning === true
        const nestedLabel = reasoned ? 'Thought' : 'Model answered'
        const body = block.body.trim()
        // Thought is label-only (no star); Model answered owns BotIcon.
        if (!body)
          return reasoned
            ? { id, label: nestedLabel, summary: block.subtitle || undefined, marker: 'none' as const }
            : { id, label: nestedLabel, summary: block.subtitle || undefined, icon: <BotIcon aria-hidden /> }
        return reasoned
          ? {
              id,
              label: nestedLabel,
              summary: block.subtitle || undefined,
              marker: 'none' as const,
              details: [{ id: `${id}-body`, text: body }],
            }
          : {
              id,
              label: nestedLabel,
              summary: block.subtitle || undefined,
              icon: <BotIcon aria-hidden />,
              details: [{ id: `${id}-body`, text: body }],
            }
      }
      case 'compaction': {
        const body = block.body.trim()
        if (!body)
          return { id, label: 'Context checkpoint', summary: block.subtitle || undefined, icon: <ArchiveIcon aria-hidden /> }
        return {
          id,
          label: 'Context checkpoint',
          summary: block.subtitle || undefined,
          icon: <ArchiveIcon aria-hidden />,
          details: [{ id: `${id}-body`, text: body }],
        }
      }
      case 'tool': {
        const body = block.body.trim()
        // Tool calls read as work items: wrench marker, never the default sparkle.
        if (!body && !block.subtitle) return { id, label: block.title, icon: <WrenchIcon aria-hidden /> }
        return {
          id,
          label: block.title,
          summary: block.subtitle || undefined,
          icon: <WrenchIcon aria-hidden />,
          details: body ? [{ id: `${id}-body`, text: body }] : undefined,
        }
      }
      case 'assistant':
        // The model's own words stay verbatim as muted detail text.
        return { id, text: block.title }
      case 'finish': {
        const claim = block.body.trim()
        if (!claim) return { id, label: block.title, icon: <CheckCircleIcon aria-hidden /> }
        return { id, label: block.title, icon: <CheckCircleIcon aria-hidden />, details: [{ id: `${id}-body`, text: claim }] }
      }
      case 'notices': {
        const body = block.body.trim()
        if (!body) return { id, label: block.title, icon: <InfoIcon aria-hidden /> }
        return { id, label: block.title, icon: <InfoIcon aria-hidden />, details: [{ id: `${id}-body`, text: body }] }
      }
      case 'notice':
      case 'meta':
      default:
        return { id, text: block.title }
    }
  })
  return (
    <ChatMessage
      role="assistant"
      thinking={<ChatThinking label={label} details={details} icon={null} marker="none" />}
    />
  )
}

/** A failed turn: one plain red line, no alert box; retry lives in hover. */
export function ErrorRow({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <ChatMessage
      role="assistant"
      actions={
        onRetry ? (
          <Tooltip content="Retry">
            <IconButton type="button" size="sm" aria-label="Retry" onClick={onRetry}>
              <RefreshIcon size={16} aria-hidden />
            </IconButton>
          </Tooltip>
        ) : undefined
      }
    >
      <p role="alert" style={{ color: 'var(--fynns-color-danger)', margin: 0 }}>
        {message}
      </p>
    </ChatMessage>
  )
}

/** One renderer tool call with the numbers it changed and what came back. */
function ToolRow({ block }: { block: TuiBlock }) {
  const running = block.status === 'running'
  // Core owns status chrome (step dot, streaming shimmer, chevron): the label
  // stays plain action text. Facts + measured body go to the step description.
  const facts = block.subtitle.trim()
  const body = block.body.trim()
  const description = [facts, body].filter(Boolean).join('\n\n') || undefined
  return (
    <ChatActivity
      label={block.title}
      streaming={running}
      defaultOpen={running}
    >
      <ChatActivityStep status={ToolStepStatus(block.status)} label={block.title} description={description} />
    </ChatActivity>
  )
}

function FrameFigure({
  block,
  onZoom,
}: {
  block: TuiBlock
  onZoom: (payload: ZoomPayload) => void
}) {
  const [failed, setFailed] = useState(false)
  const [loaded, setLoaded] = useState(false)
  const imgRef = useRef<HTMLImageElement | null>(null)
  const image = String((block.data as { image?: unknown } | null)?.image ?? '')
  const url = tuiArtifactUrl(image)
  const alt = `${block.title}`
  useEffect(() => {
    // Cached frames may complete before React attaches onLoad.
    if (url && imgRef.current?.complete && imgRef.current.naturalWidth > 0) setLoaded(true)
  }, [url])
  if (!url || failed) {
    return (
      <InlineAlert
        severity="warning"
        message={`The frame could not be loaded from the local artifact route. The recorded row and its hash remain valid: ${block.title}`}
      />
    )
  }
  return (
    <figure className="fynns-media-figure">
      <BusyRegion
        busy={!loaded}
        label={alt}
        skeleton={<LoadingSkeleton variant="block" aria-hidden />}
      >
        <button
          type="button"
          className="gav-media-zoom"
          aria-label={`Zoom: ${alt}`}
          onClick={() => onZoom({ url, alt, caption: `${alt} · task correctness unverified` })}
        >
          <img
            ref={imgRef}
            src={url}
            alt={alt}
            onLoad={() => setLoaded(true)}
            onError={() => setFailed(true)}
          />
        </button>
      </BusyRegion>
      <figcaption>
        {tuiMark(block.kind)} {block.title}
      </figcaption>
    </figure>
  )
}

/** One transcript block as core chat rows. Returns null for hidden plumbing. */
export function TuiBlockRow({
  block,
  showPlumbing,
  onZoom,
  onRetryError,
}: {
  block: TuiBlock
  showPlumbing: boolean
  onZoom: (payload: ZoomPayload) => void
  onRetryError?: (block: TuiBlock) => void
}) {
  switch (block.kind) {
    case 'user':
      return <ChatMessage role="user" markdown={block.title} />
    case 'thinking':
      return <ThinkingRow block={block} />
    case 'compaction':
      return <CompactionRow block={block} />
    case 'assistant': {
      const open = (block.data as { open?: unknown } | null)?.open === true
      return <ChatMessage role="assistant" markdown={block.title} streaming={open} />
    }
    case 'tool':
      return (
        <ChatReveal>
          <ToolRow block={block} />
        </ChatReveal>
      )
    case 'image':
      return (
        <ChatReveal>
          <FrameFigure block={block} onZoom={onZoom} />
        </ChatReveal>
      )
    case 'finish': {
      const claim = block.body.trim()
      const markdown = `${tuiMark(block.kind)} ${block.title}${claim ? `\n\n${tuiMark('assistant')} ${claim}` : ''}`
      return <ChatMessage role="assistant" markdown={markdown} />
    }
    case 'notices':
      return (
        <ChatMessage
          role="assistant"
          thinking={<ChatThinking variant="status" label={block.title || 'harness notices'} details={[]} icon={<InfoIcon aria-hidden />} />}
        >
          {block.body || undefined}
        </ChatMessage>
      )
    case 'warn':
      return <InlineAlert severity="warning" message={`${tuiMark(block.kind)} ${block.title}`} />
    case 'notice':
      return (
        <ChatMessage
          role="assistant"
          thinking={<ChatThinking variant="status" label={block.title} details={[]} icon={<InfoIcon aria-hidden />} />}
        />
      )
    case 'error': {
      const stopped = /stop/i.test(block.title)
      if (stopped) {
        return (
          <InlineAlert
            severity="info"
            role="status"
            message={`${tuiMark(block.kind)} ${block.title}`}
          />
        )
      }
      return <ErrorRow message={block.title} onRetry={onRetryError ? () => onRetryError(block) : undefined} />
    }
    case 'meta':
      if (!showPlumbing) return null
      return (
        <ChatMessage
          role="assistant"
          thinking={<ChatThinking variant="status" label={block.title} details={[]} icon={null} marker="none" />}
        />
      )
    default:
      return null
  }
}
