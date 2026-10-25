/** Mirrors Raycaster / GLApp interpreter registrations (edit-time validation). */

export type ArgKind = 'number' | 'int' | 'bool' | 'string'

export type CommandSignature = {
  required?: ArgKind[]
  /** Extra optional args after `required`, each matching the corresponding kind. */
  optional?: ArgKind[]
  /** Accept any number of extra tokens (e.g. `log`). */
  variadic?: boolean
}

/** @see VirtualSampling/Raycaster/RaycasterScript.inc and VirtualSampling/Utils/GLApp.cpp */
export const GSC_COMMAND_SIGNATURES: Record<string, CommandSignature> = {
  reset: {},
  setinteraction: { required: ['bool'] },
  setbackground: { required: ['number', 'number', 'number', 'number'] },
  resize: { required: ['int', 'int'] },
  screenshot: { optional: ['string'] },
  setfpswindow: { required: ['number'] },
  clearlog: {},
  logfile: { required: ['string'] },
  logtime: {},
  logfps: {},
  logGLInfo: { required: ['bool'] },
  log: { variadic: true },
  setdir: { required: ['string'] },
  quit: {},
  setvolume: { required: ['int'] },
  /** Append QVis `.dat` (+ sibling raw) and select the new index (Checkpoint C path-load). */
  loadvolume: { required: ['string'] },
  setmethod: { required: ['int'] },
  setrate: { required: ['number'] },
  setsubdiv: { required: ['int'] },
  constantSampleCount: { required: ['bool'] },
  setLevel: { required: ['int'] },
  settfparams: { required: ['bool', 'number', 'number'] },
  settffile: { required: ['string'] },
  settfcode: { required: ['string'] },
  setalphathreshold: { required: ['number'] },
  setusennfilter: { required: ['bool'] },
  setuseortho: { required: ['bool'] },
  resetrotation: {},
  resetclipbounds: {},
  setclipbounds: { required: ['number', 'number', 'number', 'number', 'number', 'number'] },
  setrotation: {
    required: Array<ArgKind>(16).fill('number'),
  },
  addrotationx: { required: ['number'] },
  addrotationy: { required: ['number'] },
  addrotationz: { required: ['number'] },
  settranslation: { required: ['number', 'number', 'number'] },
  settransformparams: { required: ['string'] },
  prefilter: {},
  noop: {},
}

const NUMBER_RE = /^-?\d+(?:\.\d+)?$/
const INT_RE = /^-?\d+$/
const BOOL_RE = /^(true|false)$/i

export function argKindLabel(kind: ArgKind): string {
  switch (kind) {
    case 'number':
      return 'number'
    case 'int':
      return 'integer'
    case 'bool':
      return 'true or false'
    case 'string':
      return 'string'
  }
}

export function tokenMatchesArgKind(text: string, kind: ArgKind): boolean {
  switch (kind) {
    case 'number':
      return NUMBER_RE.test(text)
    case 'int':
      return INT_RE.test(text)
    case 'bool':
      return BOOL_RE.test(text)
    case 'string':
      return text.length > 0 && !/\s/.test(text)
  }
}
