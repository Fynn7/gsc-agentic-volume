import type { GscLanguageConfig } from './gscHighlight'
import {
  argKindLabel,
  GSC_COMMAND_SIGNATURES,
  tokenMatchesArgKind,
  type CommandSignature,
} from './gscCommandSignatures'

export type GscValidationIssue = {
  line: number
  startColumn: number
  endColumn: number
  message: string
}

type ArgToken = {
  text: string
  /** 0-based start index in full line (before `#`). */
  start: number
  end: number
}

function levenshtein(a: string, b: string): number {
  const rows = a.length + 1
  const cols = b.length + 1
  const matrix = Array.from({ length: rows }, () => Array<number>(cols).fill(0))

  for (let i = 0; i < rows; i++) {
    matrix[i][0] = i
  }
  for (let j = 0; j < cols; j++) {
    matrix[0][j] = j
  }

  for (let i = 1; i < rows; i++) {
    for (let j = 1; j < cols; j++) {
      const cost = a[i - 1] === b[j - 1] ? 0 : 1
      matrix[i][j] = Math.min(matrix[i - 1][j] + 1, matrix[i][j - 1] + 1, matrix[i - 1][j - 1] + cost)
    }
  }

  return matrix[a.length][b.length]
}

export function suggestClosestCommand(word: string, commands: string[]): string | null {
  let best: string | null = null
  let bestDistance = Number.POSITIVE_INFINITY

  for (const candidate of commands) {
    const distance = levenshtein(word, candidate)
    const threshold = Math.max(2, Math.floor(candidate.length * 0.35))
    if (distance <= threshold && distance < bestDistance) {
      bestDistance = distance
      best = candidate
    }
  }

  return best
}

function parseArgTokens(code: string, commandEndOffset: number): ArgToken[] {
  const tokens: ArgToken[] = []
  let i = commandEndOffset
  while (i < code.length) {
    while (i < code.length && /\s/.test(code[i])) {
      i++
    }
    if (i >= code.length) {
      break
    }
    const start = i
    while (i < code.length && !/\s/.test(code[i])) {
      i++
    }
    tokens.push({ text: code.slice(start, i), start, end: i })
  }
  return tokens
}

function validateCommandArgs(
  command: string,
  signature: CommandSignature,
  argTokens: ArgToken[],
  lineNumber: number,
): GscValidationIssue[] {
  const issues: GscValidationIssue[] = []
  const required = signature.required ?? []
  const optional = signature.optional ?? []

  for (let index = 0; index < required.length; index++) {
    const kind = required[index]
    const token = argTokens[index]
    if (!token) {
      issues.push({
        line: lineNumber,
        startColumn: 1,
        endColumn: 1,
        message: `"${command}" requires ${required.length} argument(s) (argument ${index + 1} should be ${argKindLabel(kind)}); too few arguments.`,
      })
      return issues
    }
    if (!tokenMatchesArgKind(token.text, kind)) {
      issues.push({
        line: lineNumber,
        startColumn: token.start + 1,
        endColumn: token.end + 1,
        message: `Argument ${index + 1} of "${command}" should be ${argKindLabel(kind)}, got "${token.text}".`,
      })
    }
  }

  const optionalStart = required.length
  for (let j = 0; j < optional.length; j++) {
    const kind = optional[j]
    const token = argTokens[optionalStart + j]
    if (!token) {
      break
    }
    if (!tokenMatchesArgKind(token.text, kind)) {
      issues.push({
        line: lineNumber,
        startColumn: token.start + 1,
        endColumn: token.end + 1,
        message: `Optional argument of "${command}" should be ${argKindLabel(kind)}, got "${token.text}".`,
      })
    }
  }

  const maxArgs = required.length + optional.length
  if (signature.variadic) {
    for (let k = optionalStart; k < argTokens.length; k++) {
      const token = argTokens[k]
      if (!tokenMatchesArgKind(token.text, 'string')) {
        issues.push({
          line: lineNumber,
          startColumn: token.start + 1,
          endColumn: token.end + 1,
          message: `Argument of "${command}" should be a non-empty string, got "${token.text}".`,
        })
      }
    }
    return issues
  }

  if (!signature.variadic && argTokens.length > maxArgs && maxArgs > 0) {
    const extra = argTokens[maxArgs]
    issues.push({
      line: lineNumber,
      startColumn: extra.start + 1,
      endColumn: extra.end + 1,
      message: `"${command}" accepts at most ${maxArgs} argument(s); the extra argument "${extra.text}" will be ignored or cause a runtime error.`,
    })
  } else if (!signature.variadic && argTokens.length > maxArgs && maxArgs === 0) {
    const extra = argTokens[0]
    issues.push({
      line: lineNumber,
      startColumn: extra.start + 1,
      endColumn: extra.end + 1,
      message: `"${command}" does not accept arguments, got "${extra.text}".`,
    })
  }

  return issues
}

/** Edit-time checks: command spell + static argument types. */
export function validateGscSource(source: string, language: GscLanguageConfig): GscValidationIssue[] {
  const commands = new Set(language.commands)
  const keywords = new Set(language.keywords)
  const issues: GscValidationIssue[] = []

  const lines = source.split(/\r?\n/)
  for (let lineIndex = 0; lineIndex < lines.length; lineIndex++) {
    const line = lines[lineIndex] ?? ''
    const hashPos = line.indexOf('#')
    const code = hashPos >= 0 ? line.slice(0, hashPos) : line
    const trimmed = code.trim()
    if (!trimmed) {
      continue
    }

    const leadingWhitespace = code.length - code.trimStart().length
    const head = trimmed.match(/^([a-zA-Z_]\w*)(.*)$/)
    if (!head) {
      const firstWord = trimmed.split(/\s+/)[0] || ''
      const startColumn = leadingWhitespace + 1
      const endColumn = startColumn + firstWord.length
      issues.push({
        line: lineIndex + 1,
        startColumn,
        endColumn,
        message: `Invalid command format "${firstWord}"; a command must start with a letter or underscore.`,
      })
      continue
    }

    const word = head[1]
    const commandEndInTrimmed = word.length
    const commandEndInCode = leadingWhitespace + commandEndInTrimmed

    if (keywords.has(word)) {
      continue
    }

    if (!commands.has(word)) {
      const startColumn = leadingWhitespace + 1
      const endColumn = startColumn + word.length
      const suggestion = suggestClosestCommand(word, language.commands)
      const message = suggestion ? `Unknown command "${word}". Did you mean "${suggestion}"?` : `Unknown command "${word}".`

      issues.push({
        line: lineIndex + 1,
        startColumn,
        endColumn,
        message,
      })
      continue
    }

    const signature = GSC_COMMAND_SIGNATURES[word]
    if (!signature) {
      continue
    }

    const argTokens = parseArgTokens(code, commandEndInCode)
    issues.push(...validateCommandArgs(word, signature, argTokens, lineIndex + 1))
  }

  return issues
}

export function formatValidationSummary(issues: GscValidationIssue[]): string {
  if (issues.length === 0) {
    return ''
  }
  const head = issues[0]
  const more = issues.length > 1 ? `\n${issues.length - 1} more similar issue(s).` : ''
  return `Line ${head.line}: ${head.message}${more}`
}
