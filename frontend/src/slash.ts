/**
 * Slash commands (opencode-style): the GUI counterpart of the TUI slash menu
 * (scripts/volume_tui/app.py SLASH_NAMES + CommandScreen.COMMANDS).
 * Labels/tooltips mirror the TUI palette so both skins promise the same effect.
 */

export type SlashAction =
  | 'new'
  | 'task-a'
  | 'task-b'
  | 'verify'
  | 'copy-json'
  | 'copy-chat'
  | 'export-chat'
  | 'export-images'
  | 'open-image'
  | 'open-folder'
  | 'history'
  | 'batch'
  | 'archive-all'
  | 'change-volume'
  | 'settings'
  | 'theme'
  | 'help'
  | 'glossary'
  | 'hard-stop'
  | 'quit'

export type SlashCommand = { slash: string; action: SlashAction; tooltip: string }

export const SLASH_COMMANDS: SlashCommand[] = [
  { slash: '/new', action: 'new', tooltip: 'Clear the open run and refresh, ready for the next task. Never stops a live run.' },
  { slash: '/task-a', action: 'task-a', tooltip: 'Start the audited preset Task A (camera rotation check).' },
  { slash: '/task-b', action: 'task-b', tooltip: 'Start the audited preset Task B (informative view search).' },
  { slash: '/verify', action: 'verify', tooltip: 'Read-only check of this run\u2019s evidence; writes verification.json, never calls the model.' },
  { slash: '/copy-json', action: 'copy-json', tooltip: 'Copy the whole run\u2019s JSON log to the clipboard (read-only).' },
  { slash: '/copy-chat', action: 'copy-chat', tooltip: 'Copy the conversation of this run as plain text.' },
  { slash: '/export-chat', action: 'export-chat', tooltip: 'Download the conversation as a .log file and open the folder.' },
  { slash: '/export-images', action: 'export-images', tooltip: 'Zip every frame PNG of this run and download it.' },
  { slash: '/open-image', action: 'open-image', tooltip: 'Open the latest frame PNG in your image viewer.' },
  { slash: '/open-folder', action: 'open-folder', tooltip: 'Open the folder that holds the run\u2019s PNGs.' },
  { slash: '/history', action: 'history', tooltip: 'Pick one of the runs on this machine and open it here.' },
  { slash: '/batch', action: 'batch', tooltip: 'Repeat one task N times with the same frozen config.' },
  { slash: '/archive-all', action: 'archive-all', tooltip: 'Move every history run to runtime/archive (live runs are kept).' },
  { slash: '/change-volume', action: 'change-volume', tooltip: 'Pick the volume the next run works on; writes the settings file.' },
  { slash: '/settings', action: 'settings', tooltip: 'Show the form that writes the settings file.' },
  { slash: '/theme', action: 'theme', tooltip: 'Switch the colour theme of the whole app.' },
  { slash: '/help', action: 'help', tooltip: 'Open this help.' },
  { slash: '/glossary', action: 'glossary', tooltip: 'What the words of the screen mean.' },
  { slash: '/hard-stop', action: 'hard-stop', tooltip: 'Stop the live run and the batch queue now, then stay.' },
  { slash: '/quit', action: 'quit', tooltip: 'Stop what the GUI started, then clear the view.' },
]

function fuzzyMatch(needle: string, haystack: string): boolean {
  let pos = 0
  for (const char of needle) {
    pos = haystack.indexOf(char, pos)
    if (pos < 0) return false
    pos += 1
  }
  return true
}

/** VSCode-style fuzzy filter: prefix chars must appear in order in the command name. */
export function matchSlashCommands(prefix: string): SlashCommand[] {
  const needle = (prefix || '').trim().toLowerCase().replace(/^\//, '')
  if (!needle) return SLASH_COMMANDS
  return SLASH_COMMANDS.filter((item) => fuzzyMatch(needle, item.slash.slice(1).toLowerCase()))
}

/** The action id for a '/command ...' composer line, or null. */
export function parseSlashCommand(text: string): { action: SlashAction; args: string } | null {
  if (text == null) return null
  const stripped = String(text).trimStart()
  if (!stripped.startsWith('/')) return null
  const space = stripped.search(/\s/)
  const token = (space < 0 ? stripped : stripped.slice(0, space)).toLowerCase()
  const args = space < 0 ? '' : stripped.slice(space + 1).trim()
  const found = SLASH_COMMANDS.find((item) => item.slash === token)
  return found ? { action: found.action, args } : null
}
