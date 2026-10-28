/**
 * The TUI conversation as fynns core chat rows.
 *
 * Block semantics come from scripts/volume_tui/transcript.py + event_text.py
 * (server-built): task is the user's (▌), » lines are the model's own words,
 * ● ▣ ⚠ ✓ ✗ are measured facts. Component mapping follows core contracts:
 * - a model turn with work -> one TurnGroupRow: “Working” while live,
 *   “Worked for …” once settled; reasoning, commentary and tool rows are
 *   flattened inside, and frames/slices are 8rem thumbnails in one grid
 * - a turn without work -> ChatMessage.thinking + ChatThinking, as before
 * - the turn’s final answer stays outside the group (one assistant row)
 * - errors/warnings -> one plain red line or independent ChatThinking rows
 * - transport (meta) -> retained in evidence, never rendered in the conversation
 */
import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import {
  BusyRegion,
  ChatMarkdown,
  ChatMessage,
  ChatReveal,
  ChatThinking,
  IconButton,
  InlineAlert,
  LoadingSkeleton,
  ArchiveIcon,
  BotIcon,
  InfoIcon,
  RefreshIcon,
  Tooltip,
} from '@fynns/ui'
import { formatRunDuration, tuiArtifactUrl, tuiMark, type TuiBlock } from './tuiApi'
import type { TurnGroup } from './turns'

export type ZoomPayload = { url: string; alt: string; caption: string }

/** One model turn: an active choice, then Thought or Model answered. */
function ThinkingRow({ block, initial }: { block: TuiBlock; initial: boolean }) {
  const running = block.status === 'running'
  const reasoned = (block.data as { reasoning?: unknown } | null)?.reasoning === true
  const label = reasoned ? 'Thought' : 'Model answered'
  // Working means the call has not produced reasoning yet. As soon as the
  // reasoning stream starts, the same row says Thinking; it becomes Thought
  // when the call settles. The first row also uses this wording.
  const streamingLabel = reasoned
    ? 'Thinking'
    : initial
      ? 'Working'
      : 'Model is choosing the next action'
  const body = block.body.trim()
  // Thought stays label-only (no star); the active choice has no icon.
  const statusIcon = !running && !reasoned ? <BotIcon aria-hidden /> : null
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
          icon={statusIcon}
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

/** Harness warnings stay visible as independent gray detail rows. */
function HarnessWarningRow({ block }: { block: TuiBlock }) {
  const title = block.title.trim() || 'Harness warning'
  const body = block.body.trim()
  if (!body) {
    return <ChatThinking variant="status" label={title} details={[]} icon={<InfoIcon aria-hidden />} className="gav-harness-notice" />
  }
  return (
    <ChatThinking
      variant="disclosure"
      label={title}
      details={[{ id: 'warning-body', text: body }]}
      icon={<InfoIcon aria-hidden />}
      className="gav-harness-notice"
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
      <p
        role="alert"
        style={{
          color: 'var(--fynns-color-danger)',
          fontSize: 'var(--fynns-font-size-md)',
          margin: 0,
        }}
      >
        {message}
      </p>
    </ChatMessage>
  )
}

/** One renderer tool call with the numbers it changed and what came back. */
function ToolRow({ block, animate = true }: { block: TuiBlock; animate?: boolean }) {
  const running = animate && block.status === 'running'
  const facts = block.subtitle.trim()
  const body = block.body.trim()
  const description = [facts, body].filter(Boolean).join('\n\n') || undefined
  return (
    <ChatThinking
      variant="status"
      label={block.title}
      summary={description}
      details={[]}
      streaming={running}
      streamingLabel={block.title}
      icon={null}
      marker="none"
    />
  )
}

/** One evidence frame as an 8rem thumbnail; the caption keeps server wording. */
function FrameThumb({
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
  const alt = block.title
  useEffect(() => {
    // Cached frames may complete before React attaches onLoad.
    if (url && imgRef.current?.complete && imgRef.current.naturalWidth > 0) setLoaded(true)
  }, [url])
  if (!url || failed) {
    return (
      <figure className="fynns-media-figure gav-media-thumb">
        <div
          className="gav-media-thumb-fallback"
          title={`The frame could not be loaded from the local artifact route. The recorded row and its hash remain valid: ${alt}`}
        >
          <span aria-hidden>{tuiMark(block.kind)}</span>
        </div>
        <figcaption className="gav-media-thumb-caption" title={alt}>
          {alt}
        </figcaption>
      </figure>
    )
  }
  return (
    <figure className="fynns-media-figure gav-media-thumb">
      <BusyRegion
        busy={!loaded}
        label={alt}
        skeleton={<LoadingSkeleton variant="block" aria-hidden />}
      >
        <button
          type="button"
          className="gav-media-zoom gav-media-thumb-button"
          aria-label={`Zoom: ${alt}`}
          onClick={() => onZoom({ url, alt, caption: `${alt} · task correctness unverified` })}
        >
          <img
            ref={imgRef}
            src={url}
            alt={alt}
            decoding="async"
            onLoad={() => setLoaded(true)}
            onError={() => setFailed(true)}
          />
        </button>
      </BusyRegion>
      <figcaption className="gav-media-thumb-caption" title={alt}>
        {alt}
      </figcaption>
    </figure>
  )
}

function ReasoningText({ text }: { text: string }) {
  const [expanded, setExpanded] = useState(false)
  const [clampable, setClampable] = useState(false)
  const ref = useRef<HTMLParagraphElement | null>(null)
  useLayoutEffect(() => {
    const node = ref.current
    if (!node) return
    const measure = () => {
      // A closed disclosure has no measurable height; wait until it opens.
      if (node.clientHeight <= 0) return
      if (expanded) return
      setClampable(node.scrollHeight > node.clientHeight + 1)
    }
    measure()
    const observer = new ResizeObserver(measure)
    observer.observe(node)
    return () => observer.disconnect()
  }, [text, expanded])
  return (
    <div className="gav-turn-reasoning-wrap">
      <p ref={ref} className={`gav-turn-reasoning${expanded ? '' : ' gav-turn-reasoning--clamped'}`}>
        {text}
      </p>
      {clampable ? (
        <button
          type="button"
          className="gav-turn-more"
          aria-expanded={expanded}
          onClick={() => setExpanded((value) => !value)}
        >
          {expanded ? 'Show less' : 'Show more'}
        </button>
      ) : null}
    </div>
  )
}

/** Reasoning inside a turn group: one quiet line, then clamped muted text. */
function FlatThinking({ block }: { block: TuiBlock }) {
  const reasoned = (block.data as { reasoning?: unknown } | null)?.reasoning === true
  const body = block.body.trim()
  return (
    <div className="gav-turn-item gav-turn-item--thinking">
      <div className="gav-turn-line">
        <span className="gav-turn-mark" aria-hidden>
          {reasoned ? '✳' : '·'}
        </span>
        <span className="gav-turn-line-label">{reasoned ? 'Thought' : 'Model answered'}</span>
      </div>
      {body ? <ReasoningText text={body} /> : null}
    </div>
  )
}

/** One non-image block inside a flattened turn group. */
function TurnBlock({ block }: { block: TuiBlock }) {
  switch (block.kind) {
    case 'thinking':
      return <FlatThinking block={block} />
    case 'assistant': {
      // An answer that is still streaming may merge into a tool call, so it
      // appears once the harness closes it (same rule as the old answer row).
      const open = (block.data as { open?: unknown } | null)?.open === true
      if (open) return null
      return (
        <div className="gav-turn-item gav-turn-item--commentary">
          <ChatMarkdown source={block.title} />
        </div>
      )
    }
    case 'tool':
      return (
        <div className="gav-turn-item gav-turn-item--tool">
          {/* The group header owns the live shimmer; keep the tool title
              tense-accurate but static so two rows never sweep at once. */}
          <ToolRow block={block} animate={false} />
        </div>
      )
    default:
      return null
  }
}

/**
 * One model turn: “Working” while live, “Worked for …” once settled, open by
 * default. Reasoning, commentary, tool rows and the frame thumbnails all live
 * inside this one disclosure; only the final answer stays outside.
 */
export function TurnGroupRow({
  group,
  onZoom,
}: {
  group: TurnGroup
  onZoom: (payload: ZoomPayload) => void
}) {
  const [open, setOpen] = useState(true)
  return (
    <ChatMessage
      role="assistant"
      thinking={
        <ChatThinking
          className="gav-turn-group"
          streaming={group.running}
          streamingLabel="Working"
          label="Worked"
          durationMs={group.running ? undefined : group.durationMs ?? undefined}
          durationLabel={(seconds) => `Worked for ${formatRunDuration(seconds)}`}
          open={open}
          onOpenChange={setOpen}
          icon={null}
          marker="none"
          data-turn-group={group.key}
        >
          <div className="gav-turn-body">
            {group.bodyBlocks.map((block, index) => (
              <TurnBlock key={block.id || `${block.kind}-${index}`} block={block} />
            ))}
            {group.images.length > 0 ? (
              <div className="fynns-media-gallery gav-turn-gallery">
                {group.images.map((block, index) => (
                  <FrameThumb key={block.id || `image-${index}`} block={block} onZoom={onZoom} />
                ))}
              </div>
            ) : null}
          </div>
        </ChatThinking>
      }
    />
  )
}

/** One transcript block as core chat rows. */
export function TuiBlockRow({
  block,
  initialThinking = false,
  onZoom,
  onRetryError,
}: {
  block: TuiBlock
  initialThinking?: boolean
  onZoom: (payload: ZoomPayload) => void
  onRetryError?: (block: TuiBlock) => void
}) {
  switch (block.kind) {
    case 'user':
      return <ChatMessage role="user" markdown={block.title} />
    case 'thinking':
      return <ThinkingRow block={block} initial={initialThinking} />
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
      // Normally every image is rendered by its turn group. This fallback
      // keeps a malformed/older transcript from dropping the evidence.
      return (
        <ChatReveal>
          <FrameThumb block={block} onZoom={onZoom} />
        </ChatReveal>
      )
    case 'finish': {
      const claim = block.body.trim()
      const markdown = `${block.title}${claim ? `\n\n${tuiMark('assistant')} ${claim}` : ''}`
      return <ChatMessage role="assistant" markdown={markdown} />
    }
    case 'notices':
      return <HarnessWarningRow block={block} />
    case 'warn': {
      return <HarnessWarningRow block={block} />
    }
    case 'notice':
      if (block.title.startsWith('Run failed')) {
        return <ErrorRow message={block.title} onRetry={onRetryError ? () => onRetryError(block) : undefined} />
      }
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
      return null
    default:
      return null
  }
}
