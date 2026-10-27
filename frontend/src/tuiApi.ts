/**
 * Typed client for the Volume TUI bridge served by src/app.py (/api/tui/*).
 * Every block is produced server-side by scripts/volume_tui (transcript +
 * event_text), so the GUI chat and the Textual TUI paint the same wording
 * and marks. Nothing is invented client-side.
 */

export type TuiBlockKind =
  | 'user'
  | 'thinking'
  | 'assistant'
  | 'compaction'
  | 'tool'
  | 'image'
  | 'notice'
  | 'notices'
  | 'warn'
  | 'error'
  | 'finish'
  | 'meta'

export type TuiBlock = {
  kind: TuiBlockKind
  title: string
  subtitle: string
  body: string
  status: 'running' | 'done' | 'failed' | string | null
  ts: number | null
  data: Record<string, unknown>
}

/** The mark in front of a row: task = user's, » = model's own, rest = measured facts. */
export const TUI_MARKS: Record<string, string> = {
  user: '▌',
  assistant: '»',
  compaction: '◈',
  tool: '●',
  image: '▣',
  notices: '⚠',
  warn: '⚠',
  error: '✗',
  finish: '✓',
  notice: '·',
  meta: '·',
}

export function tuiMark(kind: string): string {
  return TUI_MARKS[kind] ?? '·'
}

export type TuiRun = {
  runId: string
  directory: string
  task: string
  status: string
  model: string
  frames: number | null
  startedAt: number | null
  sessionId: string | null
}

export type TuiTranscript = {
  runId: string
  directory: string
  status: string
  task: string
  model: string
  budget: number
  used: number
  startedAt: number | null
  sessionId: string | null
  volumePath: string
  blocks: TuiBlock[]
}

export type TuiSettings = {
  mode: string
  model: string
  endpoint: string
  h200Endpoint: string
  rendererService: string
  volumePath: string
  contextSize: number
  renderBudget: number
  temperature: number
  think: boolean
  debugWindows: boolean
}

export type TuiVolume = {
  path: string
  label: string
  dimensions: number[] | null
  hash: string | null
  kind: string
  default: boolean
  copies: number
}

export type TuiBatchRecord = {
  batchId: string
  status: string
  planned: number
  config?: Record<string, unknown>
  items?: Array<{
    seq: number
    runId: string | null
    directory: string | null
    status: string
    seconds: number
    error: string | null
  }>
}

export type TuiGlossaryEntry = { term: string; meaning: string }

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, init)
  const text = await response.text()
  let payload: unknown = null
  try {
    payload = text ? JSON.parse(text) : null
  } catch {
    payload = text
  }
  if (!response.ok) {
    const detail =
      payload && typeof payload === 'object' && typeof (payload as { error?: unknown }).error === 'string'
        ? (payload as { error: string }).error
        : `Request failed (${response.status})`
    throw new Error(detail)
  }
  return payload as T
}

function postJson<T>(route: string, body: Record<string, unknown>): Promise<T> {
  return request<T>(route, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
}

export function tuiStatus() {
  return request<{ available: boolean; error: string | null }>('/api/tui/status')
}

export function tuiListRuns() {
  return request<{ runs: TuiRun[] }>('/api/tui/runs')
}

export function tuiTranscript(runId: string) {
  return request<TuiTranscript>(`/api/tui/runs/${encodeURIComponent(runId)}/transcript`)
}

export function tuiRunImages(runId: string) {
  return request<{ images: Array<{ revision: unknown; image: string; nonblack: unknown }> }>(
    `/api/tui/runs/${encodeURIComponent(runId)}/images`,
  )
}

export function tuiStartRun(body: { taskKind: string; task: string; settings: TuiSettings }) {
  return postJson<{ started: boolean; configPath: string }>('/api/tui/runs', body)
}

export function tuiStopRun(runId: string) {
  return postJson<{ state: string; marker: string }>('/api/tui/runs/stop', { runId })
}

export function tuiVerifyRun(runId: string) {
  return postJson<{ started: boolean; running: boolean }>('/api/tui/runs/verify', { runId })
}

export function tuiVerifyState(runId: string) {
  return request<{
    running: boolean
    result: { code: number | null; summary: string[]; report: unknown; output: string } | null
  }>(`/api/tui/runs/${encodeURIComponent(runId)}/verify`)
}

export function tuiCopyRun(body: { runId: string; kind: 'chat' | 'json' }) {
  return postJson<{ message: string }>('/api/tui/copy', body)
}

export function tuiExportChat(runId: string) {
  return postJson<{ path: string }>('/api/tui/runs/export-chat', { runId })
}

export async function tuiDownloadChatLog(runId: string): Promise<{ name: string; text: string }> {
  const response = await fetch(`/api/tui/runs/${encodeURIComponent(runId)}/chat-log`)
  if (!response.ok) throw new Error(`Download failed (${response.status})`)
  return { name: `${runId}.log`, text: await response.text() }
}

export function tuiOpenPath(path: string) {
  return postJson<{ message: string }>('/api/tui/open-path', { path })
}

export function tuiArchiveAll() {
  return postJson<{
    moved: string[]
    sessions_moved: string[]
    skipped_live: string[]
    errors: Array<{ runId: string; error: string }>
    archive_dir: string
    remaining: number
  }>('/api/tui/archive-all', {})
}

export function tuiLoadSettings() {
  return request<TuiSettings>('/api/tui/settings')
}

export function tuiSaveSettings(settings: TuiSettings) {
  return postJson<TuiSettings>('/api/tui/settings', { settings })
}

export function tuiVolumes() {
  return request<{ volumes: TuiVolume[] }>('/api/tui/volumes')
}

export function tuiEnvironment() {
  return request<{
    settings: TuiSettings
    environment: { items?: Array<{ name: string; ok: boolean; detail?: string }> }
    modelProblems: Array<{ name: string; ok: boolean; detail?: string }>
  }>('/api/tui/environment')
}

export function tuiGlossary() {
  return request<{ glossary: TuiGlossaryEntry[] }>('/api/tui/glossary')
}

export function tuiListBatches() {
  return request<{ batches: TuiBatchRecord[] }>('/api/tui/batches')
}

export function tuiBatchDetail(batchId: string) {
  return request<TuiBatchRecord>(`/api/tui/batches/${encodeURIComponent(batchId)}`)
}

export function tuiStartBatch(body: { count: number; task: string; settings: TuiSettings }) {
  return postJson<{ started: boolean; batchId: string }>('/api/tui/batches', body)
}

export function tuiCancelBatch(batchId: string) {
  return postJson<{ cancelled: boolean }>('/api/tui/batches/cancel', { batchId })
}

/** Map a recorded run-dir file path onto the read-only artifact route. */
export function tuiArtifactUrl(image: string): string | null {
  const normalized = String(image || '').replaceAll('\\', '/')
  const marker = '/runtime/'
  const index = normalized.indexOf(marker)
  if (index < 0) return null
  return `/artifact/${normalized.slice(index + marker.length)}`
}

export function errorText(error: unknown): string {
  return error instanceof Error ? error.message : String(error)
}
