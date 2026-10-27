/** GSC draft, params block and generated-zone helpers for Live Preview. */

export const GSC_DRAFT_KEY = 'gav-gsc-draft'
export const GSC_PARAMS_KEY = 'gav-gsc-params'
export const GSC_DATASET_KEY = 'gav-gsc-dataset'

export const GSC_GENERATED_BEGIN = '# === GSC-PREVIEW-GENERATED-BEGIN ==='
export const GSC_GENERATED_END = '# === GSC-PREVIEW-GENERATED-END ==='

export type PreviewParams = {
  dataset: number
  method: number
  samplingRateExp: number
  virtualSamples: number
  constantSampleCount: boolean
  ortho: boolean
  ertEnabled: boolean
  tfType: 0 | 1 | 2
  tfStart: number
  tfWidth: number
  customTf: string
  transform: string
  level: number
}

/** Identity pose in the C++ setTransformParams encoding (rotation ~ translation).
 * Used as the default (instead of '') so the boot echo of the same string is
 * a no-op — otherwise a boot race decides whether rotation readback works. */
export const IDENTITY_TRANSFORM = '1_0_0_0_0_1_0_0_0_0_1_0_0_0_0_1~0_0_0'

export const DEFAULT_PREVIEW_PARAMS: PreviewParams = {
  dataset: 7,
  method: 0,
  samplingRateExp: 0,
  virtualSamples: 1,
  constantSampleCount: false,
  ortho: true,
  ertEnabled: true,
  tfType: 0,
  tfStart: 0.1,
  tfWidth: 0.12,
  customTf: '',
  transform: IDENTITY_TRANSFORM,
  level: 0,
}

export const DEFAULT_GSC = `# GSC live preview scratch
# Run with the Run button or Ctrl/Cmd+Enter. GUI edits update the generated
# zone once the operation settles; hand-written lines outside stay untouched.
setinteraction true
resize 512 512
setvolume 7
setmethod 0
settfparams false 0.1 0.12
resetrotation
addrotationx -20
addrotationy 35
${GSC_GENERATED_BEGIN}
# Managed by Live Preview — do not hand-edit between the markers.
${GSC_GENERATED_END}
`

function fmt(n: number): string {
  if (!Number.isFinite(n)) return '0'
  // Match C++ max_digits10 printing closely enough for GSC floats.
  return String(Number(n.toPrecision(9)))
}

function clamp01(v: number): number {
  if (!Number.isFinite(v)) return 0
  return Math.min(1, Math.max(0, v))
}

/** Render current panel params as GSC lines for the generated zone.
 * NOTE: transform is deliberately excluded. It has no GUI knob — the panel
 * value is a read-only C++ readback. Emitting it here would let a stale
 * readback override hand-written pose lines (resetrotation/addrotation*),
 * since the zone executes after them. Pose is owned by the script. */
export function paramsToGsc(params: PreviewParams): string {
  const lines: string[] = []
  lines.push(`setvolume ${params.dataset}`)
  lines.push(`setmethod ${params.method}`)
  lines.push(`setrate ${fmt(Math.pow(2, params.samplingRateExp))}`)
  lines.push(`setsubdiv ${Math.max(1, Math.round(params.virtualSamples))}`)
  lines.push(`constantSampleCount ${params.constantSampleCount ? 'true' : 'false'}`)
  lines.push(`setuseortho ${params.ortho ? 'true' : 'false'}`)
  lines.push(`setalphathreshold ${params.ertEnabled ? '0.99' : '2.0'}`)
  lines.push(`setLevel ${Math.max(0, Math.round(params.level))}`)
  // Native agent renderer pins black (renderer.py); keep preview identical.
  lines.push('setbackground 0 0 0 1')
  if (params.tfType === 2 && params.customTf) {
    lines.push(`settfcode ${params.customTf}`)
  } else {
    lines.push(`settfparams ${params.tfType === 1 ? 'true' : 'false'} ${fmt(clamp01(params.tfStart))} ${fmt(Math.max(0.00001, params.tfWidth))}`)
  }
  return lines.join('\n')
}

/** Replace only the generated zone; append it when absent. Hand-written text is preserved. */
export function syncParamsToScript(script: string, params: PreviewParams): string {
  const normalized = script.replace(/\r\n/g, '\n')
  const body = paramsToGsc(params)
  const replacement = `${GSC_GENERATED_BEGIN}\n${body}\n${GSC_GENERATED_END}`
  if (normalized.includes(GSC_GENERATED_BEGIN) && normalized.includes(GSC_GENERATED_END)) {
    const re = new RegExp(`${escapeRegExp(GSC_GENERATED_BEGIN)}[\\s\\S]*?${escapeRegExp(GSC_GENERATED_END)}`, 'm')
    return normalized.replace(re, replacement)
  }
  const trimmed = normalized.endsWith('\n') || normalized.length === 0 ? normalized : `${normalized}\n`
  return `${trimmed}${replacement}\n`
}

function escapeRegExp(s: string): string {
  return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
}

export function readStoredDraft(): string {
  try {
    const stored = localStorage.getItem(GSC_DRAFT_KEY)
    if (stored == null || stored.trim().length === 0) return DEFAULT_GSC
    return stored
  } catch {
    return DEFAULT_GSC
  }
}

export function readStoredParams(): PreviewParams {
  try {
    const raw = localStorage.getItem(GSC_PARAMS_KEY)
    if (!raw) return { ...DEFAULT_PREVIEW_PARAMS }
    const parsed = JSON.parse(raw) as Partial<PreviewParams>
    return { ...DEFAULT_PREVIEW_PARAMS, ...parsed }
  } catch {
    return { ...DEFAULT_PREVIEW_PARAMS }
  }
}

export function storeDraft(script: string): void {
  try {
    localStorage.setItem(GSC_DRAFT_KEY, script)
  } catch {
    /* storage unavailable — preview still works */
  }
}

export function storeParams(params: PreviewParams): void {
  try {
    localStorage.setItem(GSC_PARAMS_KEY, JSON.stringify(params))
  } catch {
    /* ignore */
  }
}
