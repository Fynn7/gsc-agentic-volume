/**
 * Typed client for the local harness API served by src/app.py.
 * All shapes mirror the recorded session JSON; nothing is invented client-side.
 */

export type SessionState =
  | 'created'
  | 'running'
  | 'paused'
  | 'budget_exhausted'
  | 'finished'
  | 'stopped'
  | 'failed'

export type SceneSpec = { revision: number } & Record<string, unknown>

export type RenderResult = {
  version: number
  scene: SceneSpec
  nativeState: Record<string, unknown>
  image: string
  imageSha256: string
  nonblackPixels: number
  taskSuccess: string
}

export type ModelAction = {
  tool: 'render_scene' | 'finish_exploration'
  sceneRevision: number
  reason: string
  parameters: {
    tfStart: number
    tfWidth: number
    rotationXYZ: number[]
    crop: number[]
  }
}

export type SessionEventKind =
  | 'initial_render'
  | 'model_action'
  | 'render'
  | 'pause'
  | 'finish'
  | 'error'

export type SessionEvent = {
  seq: number
  kind: SessionEventKind
  time: string
  result?: RenderResult
  action?: ModelAction
  remaining?: number
  message?: string
  traceback?: string
  modelClaim?: string
  externalTaskVerification?: string
}

export type SessionData = {
  id: string
  goal: string
  source: string
  model: string
  budget: number
  used: number
  state: SessionState
  scene: SceneSpec | null
  volume: Record<string, unknown> | null
  result: RenderResult | null
  events: SessionEvent[]
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, init)
  let payload: unknown = null
  try {
    payload = await response.json()
  } catch {
    payload = null
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

export function startSession(body: { source: string; model: string; goal: string; importId?: string }) {
  return postJson<{ id: string }>('/api/start', body)
}

export function fetchSession(id: string) {
  return request<SessionData>(`/api/session/${encodeURIComponent(id)}`)
}

export function stopSession(id: string) {
  return postJson<{ state: string }>('/api/stop', { id })
}

export type BrowseFileResult = { cancelled: true; path?: undefined } | { cancelled: false; path: string }

export function pickVolumeFile() {
  return postJson<BrowseFileResult>('/api/browse-file', {})
}

export function continueSession(id: string) {
  return postJson<{ state: string }>('/api/continue', { id })
}

export type VolumeImport = {
  importId: string
  source: string
  volume: Record<string, unknown> & { dimensions?: number[] }
  datUrl: string
  rawUrl: string
}

export function importVolumeFile(path: string) {
  return postJson<VolumeImport>('/api/volume/import', { path })
}

/** Import the runnable file behind a built-in preview dataset (by file stem). */
export function importBuiltinVolume(stem: string) {
  return postJson<VolumeImport>('/api/volume/builtin', { stem })
}

export function fetchVolumeImport(importId: string) {
  return request<VolumeImport>(`/api/volume/import/${encodeURIComponent(importId)}`)
}

/** Stems of built-in datasets that also have a runnable volume on this machine. */
export function listRunnableBuiltins() {
  return request<{ stems: string[] }>('/api/volume/builtins')
}

export function listVolumeImports() {
  return request<{ imports: Array<{ importId: string; source: string; datUrl?: string; rawUrl?: string; dimensions?: unknown }> }>(
    '/api/volume/imports',
  )
}

/** Map a recorded renderer path onto the read-only artifact route. */
export function artifactUrl(image: string): string | null {
  const normalized = image.replaceAll('\\', '/')
  const marker = '/runtime/'
  const index = normalized.indexOf(marker)
  if (index < 0) return null
  return `/artifact/${normalized.slice(index + marker.length)}`
}

export function errorText(error: unknown): string {
  return error instanceof Error ? error.message : String(error)
}
