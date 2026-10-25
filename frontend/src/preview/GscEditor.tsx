import { useEffect, useRef, type KeyboardEvent } from 'react'
import { CodeBlock } from '@fynns/ui'
import { GSC_LANGUAGE, ensureGscHighlightLanguage, GSC_HIGHLIGHT_PROFILE } from './gscHighlight'
import { validateGscSource, type GscValidationIssue } from './gscValidate'
import type { GscLanguageConfig } from './gscHighlight'

type GscEditorProps = {
  value: string
  onChange: (source: string) => void
  onReady: (ready: boolean) => void
  onRunShortcut: () => void
  onValidationChange?: (issues: GscValidationIssue[]) => void
  /** Optional; defaults to built-in GSC language lists. */
  languageConfig?: GscLanguageConfig
}

/**
 * Editable GSC surface: keep-set `CodeBlock` (`variant="editable"`) + consumer
 * `highlightProfile`. Ported from `gsc-live-preview` (`GscEditor.tsx`).
 * Page-scroll host keeps the previous fixed well (`rows` + `autoGrow={false}`).
 */
export function GscEditor({
  value,
  onChange,
  onReady,
  onRunShortcut,
  onValidationChange,
  languageConfig = GSC_LANGUAGE,
}: GscEditorProps) {
  const onReadyRef = useRef(onReady)
  const onRunShortcutRef = useRef(onRunShortcut)
  const onValidationChangeRef = useRef(onValidationChange)
  const validateTimerRef = useRef<number | null>(null)

  useEffect(() => {
    onReadyRef.current = onReady
  }, [onReady])

  useEffect(() => {
    onRunShortcutRef.current = onRunShortcut
  }, [onRunShortcut])

  useEffect(() => {
    onValidationChangeRef.current = onValidationChange
  }, [onValidationChange])

  useEffect(() => {
    ensureGscHighlightLanguage()
    onReadyRef.current(true)
    return () => {
      onReadyRef.current(false)
    }
  }, [])

  useEffect(() => {
    if (validateTimerRef.current) {
      window.clearTimeout(validateTimerRef.current)
    }
    validateTimerRef.current = window.setTimeout(() => {
      const issues = validateGscSource(value, languageConfig)
      onValidationChangeRef.current?.(issues)
    }, 200)
    return () => {
      if (validateTimerRef.current) {
        window.clearTimeout(validateTimerRef.current)
      }
    }
  }, [languageConfig, value])

  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if ((event.ctrlKey || event.metaKey) && event.key === 'Enter') {
      event.preventDefault()
      onRunShortcutRef.current()
    }
  }

  return (
    <div className="gsc-editor-host" onKeyDown={onKeyDown}>
      <CodeBlock
        variant="editable"
        className="gsc-editor-code"
        language="gsc"
        highlightProfile={GSC_HIGHLIGHT_PROFILE}
        wrap
        rows={14}
        autoGrow={false}
        aria-label="GSC script"
        value={value}
        onChange={onChange}
      />
    </div>
  )
}
