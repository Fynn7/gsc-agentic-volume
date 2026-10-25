import { registerHighlightLanguage, type SimpleHighlightProfile } from '@fynns/ui'

/**
 * GSC line-command lists for the editable CodeBlock highlight profile.
 * Ported from `gsc-live-preview` (`highlightConfig.ts`): row-head commands
 * color as functions, control words as keywords, `#` rest-of-line as comments.
 */
export type GscLanguageConfig = {
  keywords: string[]
  commands: string[]
}

export const GSC_LANGUAGE: GscLanguageConfig = {
  keywords: ['if', 'else', 'endif', 'repeat', 'endrepeat', 'unset', 'set'],
  commands: [
    'reset',
    'setinteraction',
    'setbackground',
    'resize',
    'screenshot',
    'setfpswindow',
    'clearlog',
    'logfile',
    'logtime',
    'logfps',
    'logGLInfo',
    'log',
    'setdir',
    'quit',
    'setvolume',
    'loadvolume',
    'setmethod',
    'setrate',
    'setsubdiv',
    'constantSampleCount',
    'setLevel',
    'settfparams',
    'settffile',
    'settfcode',
    'setalphathreshold',
    'setusennfilter',
    'setuseortho',
    'resetrotation',
    'setclipbounds',
    'resetclipbounds',
    'setrotation',
    'addrotationx',
    'addrotationy',
    'addrotationz',
    'settranslation',
    'settransformparams',
    'prefilter',
    'dumpstate',
    'noop',
  ],
}

export const GSC_HIGHLIGHT_PROFILE: SimpleHighlightProfile = {
  keywords: GSC_LANGUAGE.keywords,
  commands: GSC_LANGUAGE.commands,
}

let registered = false

/** Idempotent boot registration so `language="gsc"` resolves without a one-shot profile prop. */
export function ensureGscHighlightLanguage(): void {
  if (registered) return
  registerHighlightLanguage('gsc', GSC_HIGHLIGHT_PROFILE)
  registered = true
}
