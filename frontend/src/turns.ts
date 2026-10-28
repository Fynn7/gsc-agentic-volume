/**
 * Turn grouping for the chat thread.
 *
 * The bridge emits one flat block stream (transcript.py). The GUI shows one
 * "Working / Worked for …" group per model turn: the turn's reasoning, its
 * commentary, its tool calls and every frame it produced live inside the
 * group; the turn's final answer stays outside. This file owns only that
 * projection — wording still comes from the server blocks.
 */
import type { TuiBlock } from './tuiApi'

export type TurnGroup = {
  key: string
  /** First visible block of the turn (the group header's position). */
  index: number
  /** Thinking, commentary and tool rows, in transcript order. */
  bodyBlocks: TuiBlock[]
  /** Every image the turn produced, in transcript order. */
  images: TuiBlock[]
  /** The last assistant block of the turn, shown outside the group. */
  finalAnswer: TuiBlock | null
  /** True while this is the live turn and its work has not finished. */
  running: boolean
  /** True when the turn has a tool, frame or interstitial commentary. */
  hasWork: boolean
  /** Work duration in ms; null when the evidence has no usable stamps. */
  durationMs: number | null
}

export type TurnRow =
  | { kind: 'block'; key: string; index: number; block: TuiBlock }
  | { kind: 'group'; key: string; group: TurnGroup }

type Segment = {
  startIndex: number
  blocks: TuiBlock[]
  warnings: TuiBlock[]
  hasThinking: boolean
}

type Entry =
  | { kind: 'outside'; index: number; block: TuiBlock }
  | { kind: 'segment'; index: number; segment: Segment }

/** Kinds that end a turn instead of belonging to it. */
function isBoundary(kind: string): boolean {
  return (
    kind === 'user' ||
    kind === 'compaction' ||
    kind === 'finish' ||
    kind === 'error' ||
    kind === 'notice'
  )
}

function blockKey(block: TuiBlock, index: number): string {
  if (block.kind === 'user') return 'user'
  return block.id || `${block.kind}-${index}`
}

function isNumber(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value)
}

function splitEntries(blocks: TuiBlock[]): Entry[] {
  const entries: Entry[] = []
  let current: Segment | null = null

  const close = () => {
    if (!current) return
    entries.push({ kind: 'segment', index: current.startIndex, segment: current })
    current = null
  }

  blocks.forEach((block, index) => {
    if (block.kind === 'meta') return
    if (isBoundary(block.kind)) {
      close()
      entries.push({ kind: 'outside', index, block })
      return
    }
    // Harness warnings are not work: keep them outside the group. When one
    // lands inside a turn, the row builder emits it just before that group so
    // the group itself is never split into two headers.
    if (block.kind === 'notices' || block.kind === 'warn') {
      if (current) current.warnings.push(block)
      else entries.push({ kind: 'outside', index, block })
      return
    }
    if (block.kind === 'thinking') {
      // One model call = one Thinking row in the server transcript. A second
      // thinking block means the previous turn already finished.
      if (current?.hasThinking) close()
      if (!current) {
        current = { startIndex: index, blocks: [], warnings: [], hasThinking: false }
      }
      current.blocks.push(block)
      current.hasThinking = true
      return
    }
    if (block.kind === 'tool' || block.kind === 'image' || block.kind === 'assistant') {
      if (!current) {
        current = { startIndex: index, blocks: [], warnings: [], hasThinking: false }
      }
      current.blocks.push(block)
      return
    }
    // Unknown kind: keep it visible and do not let it hide inside a group.
    close()
    entries.push({ kind: 'outside', index, block })
  })

  close()
  return entries
}

function finalize(
  segment: Segment,
  key: string,
  isLast: boolean,
  runStatus: string,
): TurnGroup {
  const lastAssistant = [...segment.blocks].reverse().find((block) => block.kind === 'assistant') ?? null
  const lastAssistantIndex = lastAssistant ? segment.blocks.indexOf(lastAssistant) : -1
  // Commentary is an assistant block with a tool or frame after it. Only the
  // last assistant block with no later work is the visible final answer.
  const finalAnswer =
    lastAssistant &&
    !segment.blocks
      .slice(lastAssistantIndex + 1)
      .some((block) => block.kind === 'tool' || block.kind === 'image')
      ? lastAssistant
      : null

  const workBlocks = segment.blocks.filter((block) => block !== finalAnswer)
  const images = workBlocks.filter((block) => block.kind === 'image')
  const bodyBlocks = workBlocks.filter((block) => block.kind !== 'image')
  const hasWork =
    images.length > 0 ||
    bodyBlocks.some((block) => block.kind === 'tool' || block.kind === 'assistant')
  const running = isLast && runStatus === 'running' && finalAnswer === null

  const startBlock = segment.blocks.find((block) => block.kind === 'thinking') ?? segment.blocks[0]
  const lastWork = workBlocks[workBlocks.length - 1] ?? null
  const start = startBlock?.ts
  const end = finalAnswer?.ts ?? lastWork?.ts
  const durationMs =
    !running && isNumber(start) && isNumber(end) && end > start
      ? Math.round((end - start) * 1000)
      : null

  return {
    key,
    index: segment.startIndex,
    bodyBlocks,
    images,
    finalAnswer,
    running,
    hasWork,
    durationMs,
  }
}

/** Project the flat transcript into chat rows and per-turn groups. */
export function buildTurnRows(blocks: TuiBlock[], runStatus: string): TurnRow[] {
  const entries = splitEntries(blocks)
  const indexOf = new Map(blocks.map((block, index) => [block, index]))
  const lastSegment = [...entries].reverse().find((entry) => entry.kind === 'segment') ?? null
  const rows: TurnRow[] = []
  let turnIndex = 0

  for (const entry of entries) {
    if (entry.kind === 'outside') {
      rows.push({
        kind: 'block',
        key: blockKey(entry.block, entry.index),
        index: entry.index,
        block: entry.block,
      })
      continue
    }

    const group = finalize(
      entry.segment,
      `turn-${turnIndex}`,
      entry === lastSegment,
      runStatus,
    )
    turnIndex += 1

    // A warning that arrived inside the turn goes above the group, so the
    // group can stay one continuous disclosure.
    for (const warning of entry.segment.warnings) {
      const index = indexOf.get(warning) ?? entry.index
      rows.push({
        kind: 'block',
        key: blockKey(warning, index),
        index,
        block: warning,
      })
    }

    if (!group.hasWork) {
      entry.segment.blocks.forEach((block) => {
        const index = indexOf.get(block) ?? entry.index
        rows.push({
          kind: 'block',
          key: blockKey(block, index),
          index,
          block,
        })
      })
      continue
    }

    rows.push({ kind: 'group', key: group.key, group })
    if (group.finalAnswer) {
      const index = indexOf.get(group.finalAnswer) ?? entry.index
      rows.push({
        kind: 'block',
        key: blockKey(group.finalAnswer, index),
        index,
        block: group.finalAnswer,
      })
    }
  }

  return rows
}
