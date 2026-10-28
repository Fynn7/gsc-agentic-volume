/**
 * Run-management dialogs: the GUI counterpart of the TUI modal screens
 * (scripts/volume_tui/app.py SettingsScreen/VolumesScreen/RunsScreen/
 * HistoryScreen/BatchScreen/BatchDetailScreen/HelpScreen/GlossaryScreen).
 * Only fynns core components: Dialog, Table, DropdownMenu, Field* inputs.
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import {
  Autocomplete,
  Button,
  BusyRegion,
  CheckIcon,
  CodeBlock,
  Dialog,
  DropdownMenu,
  DropdownMenuItem,
  EmptyState,
  FieldBlock,
  FieldHint,
  FieldStack,
  IconButton,
  Input,
  List,
  ListItem,
  RefreshIcon,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeaderCell,
  TableRow,
  Tabs,
  Tooltip,
} from '@fynns/ui'
import type { TuiBatchRecord, TuiRun, TuiSettings, TuiVolume } from './tuiApi'
import { tuiListModels } from './tuiApi'

function Shell({ children }: { children: React.ReactNode }) {
  return <FieldStack>{children}</FieldStack>
}

/* -- settings -------------------------------------------------------- */

/** The server rejects any other shape; invalid drafts stay local instead of being applied. */
function settingsProblems(next: TuiSettings): string[] {
  const problems: string[] = []
  for (const key of ['endpoint', 'h200Endpoint', 'rendererService'] as const) {
    const value = String(next[key] ?? '').trim()
    if (!value.startsWith('http://127.0.0.1:') && !value.startsWith('http://localhost:')) {
      problems.push(`${key} must be a loopback URL (http://127.0.0.1:<port>)`)
    }
  }
  if (!String(next.model ?? '').trim()) problems.push('model must not be empty')
  if (!(next.renderBudget >= 1 && next.renderBudget <= 30)) problems.push('render budget must be 1..30')
  if (!(next.contextSize >= 1)) problems.push('context size must be >= 1')
  if (!(next.temperature >= 0 && next.temperature <= 2)) problems.push('temperature must be 0..2')
  return problems
}

function sameSettings(a: TuiSettings, b: TuiSettings): boolean {
  return (Object.keys(a) as Array<keyof TuiSettings>).every((key) => a[key] === b[key])
}

/** Keeps the typed text so a half-typed number is not rewritten mid-edit. */
function NumberField({
  value,
  integer,
  inputMode,
  onCommit,
}: {
  value: number
  integer?: boolean
  inputMode: 'numeric' | 'decimal'
  onCommit: (value: number) => void
}) {
  const parse = (raw: string) => (integer ? Number.parseInt(raw, 10) : Number.parseFloat(raw))
  const [buffer, setBuffer] = useState<{ text: string; value: number } | null>(null)
  // The buffer wins only while it belongs to the current value; an external change
  // (auto-save round trip, reopen) falls back to the prop.
  const text = buffer && buffer.value === value ? buffer.text : String(value)
  return (
    <Input
      value={text}
      onChange={(event) => {
        const raw = event.target.value
        const parsed = parse(raw)
        const finite = Number.isFinite(parsed)
        setBuffer({ text: raw, value: finite ? parsed : value })
        if (finite) onCommit(parsed)
      }}
      inputMode={inputMode}
      spellCheck={false}
      autoComplete="off"
    />
  )
}

export function SettingsDialog({
  open,
  onOpenChange,
  settings,
  volumes,
  onChange,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  settings: TuiSettings
  volumes: TuiVolume[]
  onChange: (next: TuiSettings) => void
}) {
  const [draft, setDraft] = useState<TuiSettings>(settings)
  const [models, setModels] = useState<string[]>([])
  const [modelsLoading, setModelsLoading] = useState(false)
  const edited = useRef(false)
  const wasOpen = useRef(false)
  const loadModels = useCallback(async (endpoints: string[]) => {
    setModelsLoading(true)
    try {
      const data = await tuiListModels(endpoints.map((item) => item.trim()).filter(Boolean))
      setModels(data.models)
    } finally {
      setModelsLoading(false)
    }
  }, [])
  // Reset the draft only on the closed→open edge; the auto-save round trip must not
  // overwrite what the user is typing.
  useEffect(() => {
    if (open && !wasOpen.current) {
      setDraft(settings)
      edited.current = false
      void loadModels([settings.endpoint, settings.h200Endpoint])
    }
    wasOpen.current = open
  }, [loadModels, open, settings])
  const problems = settingsProblems(draft)
  // No Save button: every runnable edit is applied immediately (the App debounces
  // the write); an invalid draft stays in the form with its reason underneath.
  useEffect(() => {
    if (open && edited.current && problems.length === 0 && !sameSettings(draft, settings)) {
      onChange(draft)
    }
  }, [draft, onChange, open, problems.length, settings])
  const set = <K extends keyof TuiSettings>(key: K, value: TuiSettings[K]) => {
    edited.current = true
    setDraft((prev) => ({ ...prev, [key]: value }))
  }
  const volumeLabel =
    volumes.find((v) => v.path === draft.volumePath)?.label ?? draft.volumePath ?? '(gate default)'
  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      title="Settings"
      description="Changes save automatically to runtime/tui-settings.json — the same file the TUI writes. Applies to the next run."
      size="md"
    >
      <Shell>
        <FieldBlock label="Mode">
          <DropdownMenu trigger={draft.mode} ariaLabel="Mode">
            {['Local', 'H200'].map((mode) => (
              <DropdownMenuItem
                key={mode}
                icon={draft.mode === mode ? <CheckIcon aria-hidden /> : undefined}
                onClick={() => set('mode', mode)}
              >
                {mode}
              </DropdownMenuItem>
            ))}
          </DropdownMenu>
        </FieldBlock>
        <FieldBlock label="Model">
          <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
            <div style={{ flex: 1, minWidth: 0 }}>
              <Autocomplete
                value={draft.model}
                onChange={(value) => {
                  // 严格单选：手输未知型号直接丢弃，只接受列表中的选择。
                  if (value) set('model', value)
                }}
                options={models}
                ariaLabel="Model"
                placeholder="选择本机模型"
                emptyText="无匹配 — 请先 ollama pull 并刷新"
              />
            </div>
            <Tooltip content="重新读取本机 Ollama 模型">
              <IconButton
                variant="ghost"
                size="sm"
                aria-label="刷新本机模型列表"
                disabled={modelsLoading}
                aria-busy={modelsLoading || undefined}
                onClick={() => void loadModels([draft.endpoint, draft.h200Endpoint])}
              >
                <RefreshIcon aria-hidden />
              </IconButton>
            </Tooltip>
          </div>
        </FieldBlock>
        <FieldBlock label="Volume">
          <DropdownMenu trigger={volumeLabel} ariaLabel="Volume">
            {volumes.map((volume) => (
              <DropdownMenuItem
                key={volume.path}
                icon={draft.volumePath === volume.path ? <CheckIcon aria-hidden /> : undefined}
                onClick={() => set('volumePath', volume.path)}
              >
                {volume.label}
                {volume.dimensions ? `  [${volume.dimensions.join('x')}]` : '  [dims unknown]'}
                {volume.default ? '  [default]' : ''}
              </DropdownMenuItem>
            ))}
          </DropdownMenu>
        </FieldBlock>
        <FieldBlock label="Local endpoint">
          <Input
            value={draft.endpoint}
            onChange={(event) => set('endpoint', event.target.value)}
            spellCheck={false}
            autoComplete="off"
          />
        </FieldBlock>
        <FieldBlock label="H200 endpoint">
          <Input
            value={draft.h200Endpoint}
            onChange={(event) => set('h200Endpoint', event.target.value)}
            spellCheck={false}
            autoComplete="off"
          />
        </FieldBlock>
        <FieldBlock label="Renderer">
          <Input
            value={draft.rendererService}
            onChange={(event) => set('rendererService', event.target.value)}
            spellCheck={false}
            autoComplete="off"
          />
        </FieldBlock>
        <FieldBlock label="Context size">
          <NumberField
            value={draft.contextSize}
            integer
            inputMode="numeric"
            onCommit={(value) => set('contextSize', value)}
          />
        </FieldBlock>
        <FieldBlock label="Render budget">
          <NumberField
            value={draft.renderBudget}
            integer
            inputMode="numeric"
            onCommit={(value) => set('renderBudget', value)}
          />
        </FieldBlock>
        <FieldBlock label="Temperature">
          <NumberField
            value={draft.temperature}
            inputMode="decimal"
            onCommit={(value) => set('temperature', value)}
          />
        </FieldBlock>
        <FieldBlock label="Think">
          <DropdownMenu trigger={draft.think ? 'on' : 'off'} ariaLabel="Think">
            {(['on', 'off'] as const).map((mode) => (
              <DropdownMenuItem
                key={mode}
                icon={(draft.think ? 'on' : 'off') === mode ? <CheckIcon aria-hidden /> : undefined}
                onClick={() => set('think', mode === 'on')}
              >
                {mode}
              </DropdownMenuItem>
            ))}
          </DropdownMenu>
        </FieldBlock>
        <FieldBlock label="Debug windows">
          <DropdownMenu trigger={draft.debugWindows ? 'on (show popups)' : 'off (headless)'} ariaLabel="Debug windows">
            {(['on', 'off'] as const).map((mode) => (
              <DropdownMenuItem
                key={mode}
                icon={(draft.debugWindows ? 'on' : 'off') === mode ? <CheckIcon aria-hidden /> : undefined}
                onClick={() => set('debugWindows', mode === 'on')}
              >
                {mode === 'on' ? 'on (show popups)' : 'off (headless)'}
              </DropdownMenuItem>
            ))}
          </DropdownMenu>
        </FieldBlock>
        {problems.length > 0 ? <FieldHint error>{problems[0]}</FieldHint> : null}
      </Shell>
    </Dialog>
  )
}

/* -- volumes --------------------------------------------------------- */

export function VolumesDialog({
  open,
  onOpenChange,
  volumes,
  current,
  loading,
  onPick,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  volumes: TuiVolume[]
  current: string
  loading: boolean
  onPick: (volume: TuiVolume) => void
}) {
  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      title="Volumes"
      description="The volumes on this machine; one becomes the next run's volume."
      size="md"
    >
      <BusyRegion busy={loading} label="Loading volumes">
        {volumes.length === 0 ? (
          <EmptyState size="sm" title="No volumes" description="Nothing discoverable under volumes/." />
        ) : (
        <Table>
          <TableHead>
            <TableRow>
              <TableHeaderCell>Volume</TableHeaderCell>
              <TableHeaderCell>Dimensions</TableHeaderCell>
              <TableHeaderCell>Note</TableHeaderCell>
              <TableHeaderCell>Pick</TableHeaderCell>
            </TableRow>
          </TableHead>
          <TableBody>
            {volumes.map((volume) => (
              <TableRow key={volume.path}>
                <TableCell>{volume.label}</TableCell>
                <TableCell>{volume.dimensions ? volume.dimensions.join('x') : 'dims unknown'}</TableCell>
                <TableCell>
                  {volume.path === current ? 'in use' : volume.default ? 'default' : ''}
                </TableCell>
                <TableCell>
                  <Button variant="ghost" size="sm" onClick={() => onPick(volume)}>
                    Switch
                  </Button>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
      </BusyRegion>
    </Dialog>
  )
}

/* -- history --------------------------------------------------------- */

function statusNote(status: string): string {
  return status
}

export function HistoryDialog({
  open,
  onOpenChange,
  batches,
  batchesLoading,
  runs,
  activeRunId,
  loading,
  onOpen,
  onOpenBatch,
  onArchiveAll,
  archiving,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  batches: TuiBatchRecord[]
  batchesLoading: boolean
  runs: TuiRun[]
  activeRunId: string | null
  loading: boolean
  onOpen: (run: TuiRun) => void
  onOpenBatch: (batch: TuiBatchRecord) => void
  onArchiveAll: () => void
  archiving: boolean
}) {
  const [tab, setTab] = useState<'batches' | 'runs'>('batches')
  useEffect(() => {
    if (open) setTab('batches')
  }, [open])
  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      title="History: batches and runs"
      description="A batch opens its detail first, then a member opens its own chat. Archived runs live under runtime/archive."
      size="lg"
    >
      <Tabs
        tabs={[
          { id: 'batches', label: 'Batches' },
          { id: 'runs', label: 'Runs' },
        ]}
        activeId={tab}
        onChange={(id) => setTab(id === 'runs' ? 'runs' : 'batches')}
        size="sm"
        ariaLabel="History view"
      />
      {tab === 'batches' ? (
        <BusyRegion busy={batchesLoading} label="Loading batches">
          {batches.length === 0 ? (
            <EmptyState
              size="sm"
              title="No batches yet"
              description="Use /batch to repeat one task N times with the same frozen config."
            />
          ) : (
            <Table>
              <TableHead>
                <TableRow>
                  <TableHeaderCell>Batch</TableHeaderCell>
                  <TableHeaderCell>Status</TableHeaderCell>
                  <TableHeaderCell>Planned</TableHeaderCell>
                  <TableHeaderCell>Members</TableHeaderCell>
                  <TableHeaderCell>Task</TableHeaderCell>
                  <TableHeaderCell>Open</TableHeaderCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {batches.map((batch) => {
                  const config = (batch.config ?? {}) as Record<string, unknown>
                  return (
                    <TableRow key={batch.batchId}>
                      <TableCell>{batch.batchId}</TableCell>
                      <TableCell>{statusNote(batch.status)}</TableCell>
                      <TableCell>{batch.planned}</TableCell>
                      <TableCell>{(batch.items ?? []).length}</TableCell>
                      <TableCell>{String(config.task ?? '').slice(0, 60)}</TableCell>
                      <TableCell>
                        <Button variant="ghost" size="sm" onClick={() => onOpenBatch(batch)}>
                          Open
                        </Button>
                      </TableCell>
                    </TableRow>
                  )
                })}
              </TableBody>
            </Table>
          )}
        </BusyRegion>
      ) : (
        <BusyRegion busy={loading} label="Loading runs">
          {runs.length === 0 ? (
            <EmptyState size="sm" title="No runs yet" description="Send the first task from the composer." />
          ) : (
            <Table>
              <TableHead>
                <TableRow>
                  <TableHeaderCell>Run</TableHeaderCell>
                  <TableHeaderCell>Status</TableHeaderCell>
                  <TableHeaderCell>Model</TableHeaderCell>
                  <TableHeaderCell>Frames</TableHeaderCell>
                  <TableHeaderCell>Task</TableHeaderCell>
                  <TableHeaderCell>Open</TableHeaderCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {runs.map((run) => (
                  <TableRow key={run.runId}>
                    <TableCell>{run.runId}{run.runId === activeRunId ? '  ●' : ''}</TableCell>
                    <TableCell>{statusNote(run.status)}</TableCell>
                    <TableCell>{run.model}</TableCell>
                    <TableCell>{run.frames ?? '?'}</TableCell>
                    <TableCell>{run.task.slice(0, 60)}</TableCell>
                    <TableCell>
                      <Button variant="ghost" size="sm" onClick={() => onOpen(run)}>
                        Open
                      </Button>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </BusyRegion>
      )}
      <div className="fynns-control-cluster fynns-control-cluster--end-align">
        <Button variant="ghost" disabled={archiving} onClick={onArchiveAll}>
          {archiving ? 'Archiving…' : 'Archive all (runs only)'}
        </Button>
      </div>
    </Dialog>
  )
}

/* -- batch ----------------------------------------------------------- */

export function BatchDialog({
  open,
  onOpenChange,
  taskPrefill,
  onStart,
  starting,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  taskPrefill: string
  onStart: (count: number, task: string) => void
  starting: boolean
}) {
  const [count, setCount] = useState('3')
  const [task, setTask] = useState(taskPrefill)
  useEffect(() => {
    if (open) setTask(taskPrefill)
  }, [open, taskPrefill])
  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      title="Batch: same frozen config runs N times"
      description="Config is frozen at start; the settings file is untouched. Each member keeps its own run folder and chat."
      size="md"
    >
      <Shell>
        <FieldBlock label="Repeat count N">
          <Input value={count} onChange={(event) => setCount(event.target.value)} inputMode="numeric" placeholder="positive integer, e.g. 3" />
        </FieldBlock>
        <FieldBlock label="Task">
          <Input value={task} onChange={(event) => setTask(event.target.value)} placeholder="required task text" spellCheck={false} />
        </FieldBlock>
        <div className="fynns-control-cluster fynns-control-cluster--end-align">
          <Button
            variant="primary"
            disabled={starting}
            onClick={() => {
              const n = Number.parseInt(count, 10)
              if (Number.isFinite(n)) onStart(n, task)
            }}
          >
            {starting ? 'Starting…' : 'Start batch'}
          </Button>
        </div>
      </Shell>
    </Dialog>
  )
}

export function BatchDetailDialog({
  open,
  onOpenChange,
  batch,
  onOpenMember,
  onCancel,
  cancelling,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  batch: TuiBatchRecord | null
  onOpenMember: (runId: string) => void
  onCancel: () => void
  cancelling: boolean
}) {
  const config = (batch?.config ?? {}) as Record<string, unknown>
  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      title={batch ? `Batch ${batch.batchId}` : 'Batch'}
      description={
        batch
          ? `status=${batch.status} planned=${batch.planned} model=${String(config.model ?? '?')} task=${String(config.task ?? '').slice(0, 120)}`
          : undefined
      }
      size="lg"
    >
      {(batch?.items ?? []).length === 0 ? (
        <EmptyState size="sm" title="No members yet" description="Members appear here as the batch runs." />
      ) : (
        <Table>
          <TableHead>
            <TableRow>
              <TableHeaderCell>Seq</TableHeaderCell>
              <TableHeaderCell>Status</TableHeaderCell>
              <TableHeaderCell>Run</TableHeaderCell>
              <TableHeaderCell>Seconds</TableHeaderCell>
              <TableHeaderCell>Error</TableHeaderCell>
              <TableHeaderCell>Open</TableHeaderCell>
            </TableRow>
          </TableHead>
          <TableBody>
            {(batch?.items ?? []).map((item) => (
              <TableRow key={item.seq}>
                <TableCell>{item.seq}</TableCell>
                <TableCell>{item.status}</TableCell>
                <TableCell>{item.runId ?? '-'}</TableCell>
                <TableCell>{item.seconds}</TableCell>
                <TableCell>{(item.error ?? '').slice(0, 60)}</TableCell>
                <TableCell>
                  {item.runId ? (
                    <Button variant="ghost" size="sm" onClick={() => onOpenMember(item.runId as string)}>
                      Open
                    </Button>
                  ) : null}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
      {batch && batch.status === 'running' ? (
        <div className="fynns-control-cluster fynns-control-cluster--end-align">
          <Button variant="ghost" disabled={cancelling} onClick={onCancel}>
            {cancelling ? 'Cancelling…' : 'Cancel remaining'}
          </Button>
        </div>
      ) : null}
    </Dialog>
  )
}

/* -- glossary / help ------------------------------------------------- */

export function GlossaryDialog({
  open,
  onOpenChange,
  entries,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  entries: Array<{ term: string; meaning: string }>
}) {
  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      title="Glossary: what the words of the screen mean"
      size="md"
    >
      <List>
        {entries.map((entry) => (
          <ListItem key={entry.term} headline={entry.term} supportingText={entry.meaning} lines={3} />
        ))}
      </List>
    </Dialog>
  )
}

export function HelpDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (open: boolean) => void }) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange} title="Help: what every key does" size="md">
      <CodeBlock
        variant="plain"
        code={[
          'Enter          send the composer text: it starts a new run',
          'Shift+Enter    add a line without sending',
          'Up/Down        recall the previous/next input (like a shell)',
          'Esc            stop the running agent (evidence already written stays)',
          '/              slash commands with a menu (Up/Down moves, Tab completes, Enter runs, Esc closes)',
          'F1             this help',
          'F2             the glossary: what the words of the screen mean',
          'Ctrl+Q         stop what the GUI started, then clear the view',
          '',
          'WHAT THE TRANSCRIPT SHOWS',
          '  ▌ <task>       what you asked for',
          '  » <model>      the model\u2019s own words: its answer, its reason, its claim',
          '  ✳ Thinking      the model reasoned; the row opens the chain',
          '  ✓ Thought       the chain is closed; click it to read it',
          '  ◈ Context checkpoint the model\u2019s summary when its context filled up',
          '  ✳ Working       a model turn that did not reason (Think is off)',
          '  ✓ Model answered the turn is over, there is nothing to open',
          '  ● <step>       what the agent is doing, then what went in and out',
          '  ▣ frame        a PNG the renderer wrote (click to zoom)',
          '  Run finished what the agent picked and what it claims',
          '  ⚠ / ✗          a notice and a failure',
        ].join('\n')}
      />
    </Dialog>
  )
}
