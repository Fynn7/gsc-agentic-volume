/**
 * Volume-agent GUI chat: the coding-agent skin over the Codex launcher runs.
 *
 * Data model (single track, same as the Textual TUI): runs discovered by
 * scripts/volume_tui, each rendered from its server-built transcript blocks
 * (transcript.py + event_text.py wording, never reworded client-side).
 * The old Session/OllamaDriver model (api.ts SessionData + ChatRows.tsx) is
 * gone; the Live Preview Stage stays as the volume workbench and writes the
 * shared settings volume.
 */
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import {
  applyFynnsThemeMode,
  ArchivedChatsSettingsDialog,
  ArchiveIcon,
  Avatar,
  BusyRegion,
  Button,
  Card,
  Chat,
  ChatComposer,
  ChatMessage,
  ChatScrollToBottom,
  ChatSessionsDrawer,
  ChatThinking,
  ChatThread,
  ClippedNavShell,
  CodeBlock,
  Collapsible,
  ConfirmDialog,
  Dialog,
  DownloadIcon,
  DropdownMenuItem,
  DropdownMenuSeparator,
  EmptyState,
  EndAside,
  ExpandableCodeBlock,
  FillColumn,
  getFynnsThemeMode,
  IconButton,
  Input,
  LinearProgress,
  List,
  ListItem,
  LoadingSkeleton,
  MenuIcon,
  NavigationDrawer,
  NavigationDrawerItem,
  NavigationDrawerNewChat,
  PageScroll,
  PanelLeftIcon,
  PanelRightIcon,
  PencilIcon,
  PlusIcon,
  restoreFynnsThemeMode,
  SettingsIcon,
  SnackbarHost,
  SplitPane,
  Tabs,
  TopAppBar,
  Tooltip,
  TrashIcon,
  snackbar,
  useChatSequencer,
  type SelectionMenuContext,
} from '@fynns/ui'
import { fetchVolumeImport, importVolumeFile, listVolumeImports, pickVolumeFile, type VolumeImport } from './api'
import { LiveStage, STAGE_EXPORT_EVENT } from './preview/LiveStage'
import { ErrorRow, TuiBlockRow, TurnGroupRow, type ZoomPayload } from './TuiThread'
import { buildTurnRows, type TurnGroup } from './turns'
import {
  BatchDetailDialog,
  BatchDialog,
  GlossaryDialog,
  HelpDialog,
  HistoryDialog,
  SettingsDialog,
  VolumesDialog,
} from './TuiDialogs'
import { matchSlashCommands, parseSlashCommand, type SlashAction } from './slash'
import {
  errorText,
  tuiArchiveAll,
  tuiArchiveRun,
  tuiBatchDetail,
  tuiCancelBatch,
  tuiCopyRun,
  tuiDeleteRun,
  tuiDownloadChatLog,
  tuiGlossary,
  tuiListArchived,
  tuiListBatches,
  tuiListRuns,
  tuiLoadSettings,
  tuiOpenPath,
  tuiRenameRun,
  tuiRunImages,
  tuiSaveSettings,
  tuiStartBatch,
  tuiStartRun,
  tuiStopRun,
  tuiTranscript,
  tuiUnarchiveRun,
  tuiVerifyRun,
  tuiVerifyState,
  tuiVolumes,
  type TuiBatchRecord,
  type TuiBlock,
  type TuiGlossaryEntry,
  type TuiRun,
  type TuiSettings,
  type TuiTranscript,
  type TuiVolume,
} from './tuiApi'

const POLL_INTERVAL_MS = 1500
const RUNS_POLL_MS = 3000
const STAGE_WIDTH_KEY = 'gav-stage-width-v2'
const STAGE_OPEN_KEY = 'gav-stage-open'
const HISTORY_KEY = 'gav-composer-history'

type Pending = null | 'starting' | 'stopping'

/**
 * One batch the chat is following. TUI parity: while a batch runs the chat
 * shows only the current member; the sidebar batch item's right-edge ring is
 * the only running status, and the batch record stays the source of truth.
 */
type BatchProgress = {
  batchId: string
  status: string
  startedAt: number
  latestRunId: string | null
}

/** Project a persisted batch record onto the chat's follow state. */
function batchProgressFromRecord(record: TuiBatchRecord, prev?: BatchProgress | null): BatchProgress {
  const items = record.items ?? []
  return {
    batchId: record.batchId,
    status: String(record.status || 'running'),
    startedAt: Number(record.createdAt) || prev?.startedAt || Date.now() / 1000,
    latestRunId: [...items].reverse().find((item) => item.runId)?.runId ?? prev?.latestRunId ?? null,
  }
}

type VerifyState = {
  running: boolean
  result: { code: number | null; summary: string[]; report: unknown; output: string } | null
}

function readStageWidth(): number {
  try {
    const raw = localStorage.getItem(STAGE_WIDTH_KEY)
    if (raw != null) {
      const v = Number.parseFloat(raw)
      if (Number.isFinite(v) && v >= 30 && v <= 75) return v
    }
  } catch {
    /* ignore */
  }
  return 50
}

function readStageOpen(): boolean {
  try {
    const raw = localStorage.getItem(STAGE_OPEN_KEY)
    if (raw === '0' || raw === 'false') return false
  } catch {
    /* ignore */
  }
  return true
}

function loadHistory(): string[] {
  try {
    const raw = localStorage.getItem(HISTORY_KEY)
    const parsed = raw ? JSON.parse(raw) : []
    return Array.isArray(parsed) ? parsed.filter((item) => typeof item === 'string') : []
  } catch {
    return []
  }
}

function runStateLabel(status: string, pending: Pending): string {
  if (pending === 'starting') return 'Starting'
  if (pending === 'stopping') return 'Stop requested'
  switch (status) {
    case 'running':
      return 'Running'
    case 'finished':
      return 'Finished — claim unverified'
    case 'failed':
      return 'Failed'
    case 'stopped':
    case 'interrupted':
      return 'Stopped'
    default:
      return status || 'Unknown'
  }
}

function downloadBlob(blob: Blob, name: string): void {
  const url = URL.createObjectURL(blob)
  try {
    const a = document.createElement('a')
    a.href = url
    a.download = name
    document.body.appendChild(a)
    a.click()
    a.remove()
  } finally {
    window.setTimeout(() => URL.revokeObjectURL(url), 4000)
  }
}

function sameVolumePath(a: string, b: string): boolean {
  const norm = (s: string) => s.trim().replace(/\\/g, '/').toLowerCase()
  return norm(a) === norm(b)
}

/** Core chat entrance duration; the live status follows the first row. */
function chatEntryDelayMs(): number {
  try {
    if (window.matchMedia?.('(prefers-reduced-motion: reduce)').matches) return 0
    const raw = getComputedStyle(document.documentElement)
      .getPropertyValue('--fynns-chat-entry-duration')
      .trim()
    const value = Number.parseFloat(raw)
    if (!Number.isFinite(value) || value <= 0) return 360
    return raw.endsWith('ms') ? value : raw.endsWith('s') ? value * 1000 : value
  } catch {
    return 360
  }
}

function App() {
  const [navOpen, setNavOpen] = useState(true)
  const [navMode, setNavMode] = useState<'batches' | 'chats'>('chats')
  const [asideOpen, setAsideOpen] = useState(false)

  const [settings, setSettings] = useState<TuiSettings | null>(null)
  const [source, setSource] = useState('')
  const [draft, setDraft] = useState('')
  const [picking, setPicking] = useState(false)
  const [previewImport, setPreviewImport] = useState<VolumeImport | null>(null)
  const [history, setHistory] = useState<string[]>(() => loadHistory())
  const [histIdx, setHistIdx] = useState<number | null>(null)

  const [runs, setRuns] = useState<TuiRun[]>([])
  const [runsLoaded, setRunsLoaded] = useState(false)
  const [batches, setBatches] = useState<TuiBatchRecord[]>([])
  const [batchesLoading, setBatchesLoading] = useState(false)
  const [activeRunId, setActiveRunId] = useState<string | null>(null)
  const [transcript, setTranscript] = useState<TuiTranscript | null>(null)
  const [pending, setPending] = useState<Pending>(null)
  const startStampRef = useRef(0)
  const cancelStartRef = useRef(false)
  // Live reveal: the user's turn is painted at submit, before the launcher
  // mints a run, so the thread never falls back to the idle "Start a run"
  // state while the backend is already working.
  const [pendingTask, setPendingTask] = useState<{ text: string; startedAt: number } | null>(null)
  const [viewEpoch, setViewEpoch] = useState(0)
  const [startedHere, setStartedHere] = useState(false)
  const [stageVisible, setStageVisible] = useState(false)

  const [slashSel, setSlashSel] = useState(0)

  const [settingsOpen, setSettingsOpen] = useState(false)
  const settingsSaveTimer = useRef<number | null>(null)
  const pendingSettings = useRef<TuiSettings | null>(null)
  const [volumesOpen, setVolumesOpen] = useState(false)
  const [volumes, setVolumes] = useState<TuiVolume[]>([])
  const [volumesLoading, setVolumesLoading] = useState(false)
  const [historyOpen, setHistoryOpen] = useState(false)
  const [archiving, setArchiving] = useState(false)
  const [archivedRuns, setArchivedRuns] = useState<TuiRun[]>([])
  const [archivedOpen, setArchivedOpen] = useState(false)
  const [archivingOne, setArchivingOne] = useState<string | null>(null)
  const [unarchiving, setUnarchiving] = useState<string | null>(null)
  const [batchOpen, setBatchOpen] = useState(false)
  const [batchStarting, setBatchStarting] = useState(false)
  const [batchDetail, setBatchDetail] = useState<TuiBatchRecord | null>(null)
  const [batchDetailOpen, setBatchDetailOpen] = useState(false)
  const [batchProgress, setBatchProgress] = useState<BatchProgress | null>(null)
  const [batchCancelling, setBatchCancelling] = useState(false)
  const [glossaryOpen, setGlossaryOpen] = useState(false)
  const [glossary, setGlossary] = useState<TuiGlossaryEntry[]>([])
  const [helpOpen, setHelpOpen] = useState(false)
  const [quitConfirm, setQuitConfirm] = useState(false)
  const [archiveConfirm, setArchiveConfirm] = useState(false)
  const [renameTarget, setRenameTarget] = useState<TuiRun | null>(null)
  const [renameDraft, setRenameDraft] = useState('')
  const [renameSaving, setRenameSaving] = useState(false)
  const [deleteTarget, setDeleteTarget] = useState<TuiRun | null>(null)
  const [deleting, setDeleting] = useState(false)
  const [deleteAllConfirm, setDeleteAllConfirm] = useState(false)
  const [deletingAll, setDeletingAll] = useState(false)

  const [verify, setVerify] = useState<VerifyState>({ running: false, result: null })
  const batchRunning = batchProgress?.status === 'running'
  useEffect(() => {
    if (batchRunning) setNavMode('batches')
  }, [batchRunning])

  const [zoom, setZoom] = useState<ZoomPayload | null>(null)
  const [zoomOpen, setZoomOpen] = useState(false)
  const pollFailed = useRef(false)
  const activeRef = useRef<string | null>(null)
  useEffect(() => {
    activeRef.current = activeRunId
  }, [activeRunId])
  const liveRef = useRef(false)

  // Live Preview workbench: desktop Chat 50% / Stage 50% in a core SplitPane;
  // narrow viewports switch via the top-bar Tabs.
  const [stageOpen, setStageOpen] = useState<boolean>(() => readStageOpen())
  const [stageWidth, setStageWidth] = useState<number>(() => readStageWidth())
  const [narrowView, setNarrowView] = useState<'chat' | 'stage'>('chat')
  const [isNarrow, setIsNarrow] = useState<boolean>(() =>
    typeof window !== 'undefined' ? window.matchMedia('(max-width: 56.25rem)').matches : false,
  )
  const [rowWidth, setRowWidth] = useState<number>(() =>
    typeof window !== 'undefined' ? window.innerWidth : 1280,
  )
  const [remPx, setRemPx] = useState<number>(16)
  const canvasRef = useRef<HTMLDivElement | null>(null)
  const rowWidthRef = useRef<number>(typeof window !== 'undefined' ? window.innerWidth : 1280)

  useEffect(() => {
    const mq = window.matchMedia('(max-width: 56.25rem)')
    const sync = () => setIsNarrow(mq.matches)
    sync()
    mq.addEventListener('change', sync)
    return () => mq.removeEventListener('change', sync)
  }, [])

  useEffect(() => {
    try {
      localStorage.setItem(STAGE_WIDTH_KEY, String(stageWidth))
    } catch {
      /* ignore */
    }
  }, [stageWidth])

  useEffect(() => {
    try {
      localStorage.setItem(STAGE_OPEN_KEY, stageOpen ? '1' : '0')
    } catch {
      /* ignore */
    }
  }, [stageOpen])

  useLayoutEffect(() => {
    const row = canvasRef.current
    if (!row) return
    const sync = () => {
      const w = row.clientWidth
      if (w > 0) {
        rowWidthRef.current = w
        setRowWidth(w)
      }
      try {
        const root = Number.parseFloat(getComputedStyle(document.documentElement).fontSize)
        if (Number.isFinite(root) && root > 0) setRemPx(root)
      } catch {
        /* keep default */
      }
    }
    sync()
    const observer = new ResizeObserver(sync)
    observer.observe(row)
    return () => observer.disconnect()
  }, [])

  const chatMinPx = Math.round(23 * remPx)
  const stageMinPx = 320
  const splitChatPx = (() => {
    const raw = Math.round((rowWidth * (100 - stageWidth)) / 100)
    if (!(rowWidth > 0)) return raw
    return Math.min(Math.max(raw, chatMinPx), Math.max(chatMinPx, rowWidth - stageMinPx - 8))
  })()

  const handleSplitSizeChange = useCallback((chatPx: number) => {
    const w = rowWidthRef.current
    if (!(w > 0)) return
    const pct = 100 - (chatPx / w) * 100
    setStageWidth(Math.min(75, Math.max(30, Math.round(pct * 10) / 10)))
  }, [])

  /* -- boot: theme, settings, glossary, volumes, runs -------------------- */

  useEffect(() => {
    try {
      restoreFynnsThemeMode()
    } catch {
      /* keep dark */
    }
    void tuiLoadSettings()
      .then((loaded) => {
        setSettings(loaded)
        setSource(loaded.volumePath || '')
      })
      .catch((error: unknown) => {
        snackbar(`Settings are unreadable: ${errorText(error)}`, {
          severity: 'error',
          duration: 'long',
          dismissible: true,
        })
      })
    void tuiGlossary()
      .then((data) => setGlossary(data.glossary))
      .catch(() => undefined)
    void tuiVolumes()
      .then((data) => setVolumes(data.volumes))
      .catch(() => undefined)
  }, [])

  const ensureVolumes = useCallback(async () => {
    setVolumesLoading(true)
    try {
      const data = await tuiVolumes()
      setVolumes(data.volumes)
    } catch {
      /* keep the previous list; the dialog shows its empty state */
    } finally {
      setVolumesLoading(false)
    }
  }, [])

  const refreshRuns = useCallback(async () => {
    try {
      const data = await tuiListRuns()
      setRuns(data.runs)
      setRunsLoaded(true)
      return data.runs
    } catch {
      return null
    }
  }, [])

  const refreshArchived = useCallback(async () => {
    try {
      const data = await tuiListArchived()
      setArchivedRuns(data.runs)
      return data.runs
    } catch {
      return null
    }
  }, [])

  const refreshBatches = useCallback(async (quiet = false) => {
    if (!quiet) setBatchesLoading(true)
    try {
      const data = await tuiListBatches()
      setBatches(data.batches)
      const running = data.batches.find((batch) => String(batch.status) === 'running')
      if (running) {
        setBatchProgress((prev) => {
          if (prev?.batchId === running.batchId || prev?.status === 'running') return prev
          return batchProgressFromRecord(running, prev)
        })
      }
      return data.batches
    } catch {
      return null
    } finally {
      if (!quiet) setBatchesLoading(false)
    }
  }, [])

  // A page reload (or a batch started by the TUI skin) must not hide a batch
  // that is still running: adopt it only when a live member run proves the
  // queue is active, so stale batch.json records cannot pin a fake banner.
  useEffect(() => {
    let cancelled = false
    void Promise.all([tuiListBatches(), tuiListRuns()])
      .then(([batchData, runData]) => {
        if (cancelled) return
        setBatches(batchData.batches)
        const running = batchData.batches.find((batch) => {
          if (String(batch.status) !== 'running') return false
          const created = Number(batch.createdAt) || 0
          return runData.runs.some(
            (run) => run.status === 'Running' && (run.startedAt ?? 0) >= created - 10,
          )
        })
        if (running) setBatchProgress((prev) => prev ?? batchProgressFromRecord(running, null))
      })
      .catch(() => undefined)
    return () => {
      cancelled = true
    }
  }, [])

  useEffect(() => {
    void refreshRuns()
    void refreshArchived()
    void refreshBatches(true)
    // TUI parity: while a batch runs the chat has to adopt each member run
    // promptly, so poll the run list faster than the idle 3s cadence.
    const timer = window.setInterval(() => {
      void refreshRuns()
      void refreshBatches(true)
    }, batchRunning ? 1000 : RUNS_POLL_MS)
    return () => window.clearInterval(timer)
  }, [refreshRuns, refreshArchived, refreshBatches, batchRunning])

  /* -- batch progress: batch.json is the source of truth ------------------ */

  useEffect(() => {
    const batchId = batchProgress?.batchId
    if (!batchId || batchProgress?.status !== 'running') return
    let cancelled = false
    let inFlight = false
    const poll = async () => {
      if (inFlight) return
      inFlight = true
      try {
        const detail = await tuiBatchDetail(batchId)
        if (cancelled) return
        // Keep an open detail dialog live; never open it on its own.
        setBatchDetail((prev) => (prev && prev.batchId === batchId ? detail : prev))
        const items = detail.items ?? []
        const status = String(detail.status || 'running')
        const running = status === 'running'
        setBatchProgress((prev) =>
          prev && prev.batchId === batchId ? batchProgressFromRecord(detail, prev) : prev,
        )
        if (!running) {
          setBatchCancelling(false)
          void refreshRuns()
          void refreshBatches()
          const ok = items.filter((item) => item.status === 'success').length
          const failed = items.filter((item) => item.status === 'failed').length
          const skipped = items.filter((item) => item.status === 'skipped').length
          snackbar(
            `Batch ${batchId} finished: ${status} — ${ok} ok, ${failed} failed${skipped ? `, ${skipped} skipped` : ''}.`,
            { severity: failed ? 'warning' : 'info', duration: 'long', dismissible: true },
          )
        }
      } catch {
        // The record may be between writes; the next tick retries.
      } finally {
        inFlight = false
      }
    }
    void poll()
    const timer = window.setInterval(() => void poll(), 1500)
    return () => {
      cancelled = true
      window.clearInterval(timer)
    }
  }, [batchProgress?.batchId, batchProgress?.status, refreshRuns, refreshBatches])


  /* -- transcript polling: the live view follows the recorded evidence ---- */

  useEffect(() => {
    if (!activeRunId) return
    let cancelled = false
    let inFlight = false
    const poll = async () => {
      if (inFlight) return
      inFlight = true
      try {
        const data = await tuiTranscript(activeRunId)
        if (cancelled || activeRef.current !== activeRunId) return
        pollFailed.current = false
        setTranscript(data)
        if (data.blocks.some((block) => block.kind === 'user')) setPendingTask(null)
        liveRef.current = data.status === 'running'
        if (pending === 'starting') setPending(null)
        if (pending === 'stopping' && data.status !== 'running') setPending(null)
      } catch (error) {
        if (cancelled) return
        if (!pollFailed.current) {
          pollFailed.current = true
          snackbar(`Could not refresh the run: ${errorText(error)}`, {
            severity: 'error',
            duration: 'long',
            dismissible: true,
          })
        }
      } finally {
        inFlight = false
      }
    }
    void poll()
    const timer = window.setInterval(() => {
      // Finished/failed/stopped freezes the view: no more polling.
      if (!liveRef.current && pending === null) {
        window.clearInterval(timer)
        return
      }
      void poll()
    }, POLL_INTERVAL_MS)
    return () => {
      cancelled = true
      window.clearInterval(timer)
    }
  }, [activeRunId, pending])

  /* -- run lifecycle ------------------------------------------------------ */

  const openRun = useCallback((runId: string, options?: { started?: boolean; reload?: boolean }) => {
    const reload = options?.reload === true
    if (!options?.started && !reload) {
      setViewEpoch((epoch) => epoch + 1)
      setStartedHere(false)
    }
    setActiveRunId(runId)
    if (!reload) setTranscript(null)
    if (!options?.started) setPendingTask(null)
    setVerify({ running: false, result: null })
    setPending(null)
    liveRef.current = true
    setHistoryOpen(false)
  }, [])

  const handleNewTask = useCallback(() => {
    setActiveRunId(null)
    setTranscript(null)
    setVerify({ running: false, result: null })
    setPending(null)
    setPendingTask(null)
    setViewEpoch((epoch) => epoch + 1)
    setStartedHere(false)
    setDraft('')
    // A finished batch banner belongs to the previous view; a running batch
    // stays visible until it ends (the TUI can never silently lose it).
    setBatchProgress((prev) => (prev && prev.status !== 'running' ? null : prev))
  }, [])

  // Guard: the open run can vanish outside this view (archived or deleted
  // from the TUI or another skin). When history no longer contains it,
  // fall back to the new-task view instead of showing a dead transcript.
  // Skipped before the first load and while a start is in flight.
  useEffect(() => {
    if (!runsLoaded || pending !== null) return
    if (activeRunId && !runs.some((run) => run.runId === activeRunId)) handleNewTask()
  }, [runs, runsLoaded, pending, activeRunId, handleNewTask])

  // TUI parity: a running batch owns the chat. As soon as the current member
  // mints its run (status Running), open it here so its live evidence streams
  // in; when it ends, the next member is adopted the same way.
  useEffect(() => {
    if (!batchRunning || !batchProgress) return
    const active = runs.find((run) => run.runId === activeRunId)
    const since = batchProgress.startedAt - 10
    const candidate = runs
      .filter((run) => run.status === 'Running' && (run.startedAt ?? 0) >= since)
      .sort((a, b) => (b.startedAt ?? 0) - (a.startedAt ?? 0))[0]
    if (candidate) {
      // Keep the current member identity in the follow state so the single-chat
      // list can exclude it before batch.json records the finished member.
      if (batchProgress.latestRunId !== candidate.runId) {
        setBatchProgress((prev) =>
          prev && prev.batchId === batchProgress.batchId
            ? { ...prev, latestRunId: candidate.runId }
            : prev,
        )
      }
      if (active?.status === 'Running') return
      if (candidate.runId !== activeRunId) openRun(candidate.runId)
      return
    }
    if (active?.status === 'Running') return
    // A very short member can finish before the run poll sees it as Running:
    // show its recorded transcript rather than leaving the previous member up.
    if (
      batchProgress.latestRunId &&
      batchProgress.latestRunId !== activeRunId &&
      runs.some((run) => run.runId === batchProgress.latestRunId)
    ) {
      openRun(batchProgress.latestRunId)
    }
  }, [runs, batchRunning, batchProgress, activeRunId, openRun])

  // Batch members live in the batch history, never in the single-chat list.
  // `latestRunId` covers the live member before batch.json appends its item.
  const batchRunIds = useMemo(() => {
    const ids = new Set<string>()
    for (const batch of batches) {
      for (const item of batch.items ?? []) {
        if (item.runId) ids.add(item.runId)
      }
    }
    if (batchProgress?.latestRunId) ids.add(batchProgress.latestRunId)
    return ids
  }, [batches, batchProgress])
  const chatRuns = useMemo(
    () => runs.filter((run) => !batchRunIds.has(run.runId)),
    [runs, batchRunIds],
  )
  const activeBatchId = useMemo(() => {
    if (batchProgress?.status === 'running') return batchProgress.batchId
    if (!activeRunId) return null
    for (const batch of batches) {
      if ((batch.items ?? []).some((item) => item.runId === activeRunId)) return batch.batchId
    }
    return null
  }, [batches, activeRunId, batchProgress])

  const pushHistory = useCallback((value: string) => {
    const text = value.trim()
    if (!text) return
    setHistory((prev) => {
      const next = [text, ...prev.filter((item) => item !== text)].slice(0, 100)
      try {
        localStorage.setItem(HISTORY_KEY, JSON.stringify(next))
      } catch {
        /* ignore */
      }
      return next
    })
    setHistIdx(null)
  }, [])

  const startCustomRun = useCallback(
    async (task: string, taskKind: 'custom' | 'task-a' | 'task-b' = 'custom') => {
      if (!settings) {
        snackbar('Settings are still loading — retry in a second.', {
          severity: 'error',
          duration: 'long',
          dismissible: true,
        })
        return
      }
      const live = runs.some((run) => run.status === 'Running')
      if (batchRunning) {
        snackbar('A batch is running — stop it first.', {
          severity: 'error',
          duration: 'long',
          dismissible: true,
        })
        return
      }
      if (live || pending !== null || liveRef.current) {
        snackbar('A run is still live — stop it first (/new never stops a live run).', {
          severity: 'error',
          duration: 'long',
          dismissible: true,
        })
        return
      }
      // A finished batch banner must not shadow the next single run.
      setBatchProgress((prev) => (prev && prev.status !== 'running' ? null : prev))
      setNavMode('chats')
      const goal = task.trim()
      if (!goal && taskKind === 'custom') return
      if (!source.trim()) {
        snackbar('Choose the local volume file first.', {
          severity: 'error',
          duration: 'long',
          dismissible: true,
        })
        void chooseSourceRef.current()
        return
      }
      setPending('starting')
      startStampRef.current = Date.now() / 1000
      cancelStartRef.current = false
      // Paint the user's turn immediately: the launcher may spend seconds in
      // preflight and the initial frame before the run record exists. Presets
      // get their real prompt text from the start response.
      setViewEpoch((epoch) => epoch + 1)
      setStartedHere(true)
      setPendingTask({
        text: taskKind === 'custom' ? goal : `/${taskKind}`,
        startedAt: Date.now(),
      })
      try {
        const started = await tuiStartRun({ taskKind, task: goal, settings: { ...settings, volumePath: source.trim() } })
        if (started.task) {
          setPendingTask((prev) =>
            prev && (prev.text === '' || prev.text.startsWith('/'))
              ? { ...prev, text: started.task as string }
              : prev,
          )
        }
        pushHistory(taskKind === 'custom' ? goal : `/${taskKind}`)
        setDraft('')
        // The launcher mints the run id: adopt the newest run on next poll.
        const stamp = startStampRef.current
        const prevActive = activeRef.current
        let adopted: string | null = null
        for (let attempt = 0; attempt < 20; attempt++) {
          await new Promise((resolve) => setTimeout(resolve, 1000))
          const latest = await refreshRuns()
          const found = (latest ?? []).find((run) => (run.startedAt ?? 0) >= stamp - 1)
          if (found) {
            adopted = found.runId
            openRun(found.runId, { started: true })
            if (cancelStartRef.current) {
              cancelStartRef.current = false
              setPending('stopping')
              void tuiStopRun(found.runId).catch((error: unknown) => {
                setPending(null)
                snackbar(`Stop was not accepted: ${errorText(error)}`, {
                  severity: 'error',
                  duration: 'long',
                  dismissible: true,
                })
              })
            }
            break
          }
        }
        if (adopted === null) {
          // Fallback: open the newest run when it differs from the previously open one.
          const latest = await refreshRuns()
          if (latest && latest.length > 0 && latest[0].runId !== prevActive) {
            openRun(latest[0].runId, { started: true })
            if (cancelStartRef.current) {
              cancelStartRef.current = false
              setPending('stopping')
              void tuiStopRun(latest[0].runId).catch(() => setPending(null))
            }
          }
        }
        if (adopted === null && cancelStartRef.current) {
          cancelStartRef.current = false
          setPending(null)
          snackbar('The run did not start, so there is nothing to stop.', {
            severity: 'warning',
            duration: 'long',
            dismissible: true,
          })
        }
      } catch (error: unknown) {
        const message = errorText(error)
        cancelStartRef.current = false
        setPending(null)
        setPendingTask(null)
        setStartedHere(false)
        snackbar(`Could not start the task: ${message}`, {
          severity: 'error',
          duration: 'long',
          dismissible: true,
        })
        // No run was minted: keep the failed attempt in the thread instead of
        // bouncing back to the empty "Start a run" state (gsc-live-preview
        // keeps the user turn + error inline the same way).
        if (activeRef.current == null) {
          const display = goal || (taskKind === 'custom' ? '(empty task)' : `/${taskKind}`)
          pushHistory(taskKind === 'custom' ? goal || display : `/${taskKind}`)
          setDraft('')
          setTranscript({
            runId: 'local-start-failed',
            directory: '',
            status: 'failed',
            task: display,
            model: settings?.model ?? '',
            budget: 0,
            used: 0,
            startedAt: null,
            sessionId: null,
            volumePath: source.trim(),
            blocks: [
              {
                kind: 'user',
                title: display,
                subtitle: '',
                body: '',
                status: 'done',
                ts: null,
                data: { retryTask: goal, retryKind: taskKind },
              },
              {
                kind: 'error',
                title: message || 'Could not start the task',
                subtitle: '',
                body: 'Start Ollama / check Connection settings, then retry — the attempted task is kept above.',
                status: 'failed',
                ts: null,
                data: {},
              },
            ],
          })
        }
      }
    },
    [settings, runs, pending, source, refreshRuns, openRun, pushHistory, batchRunning],
  )

  const handleStop = useCallback(() => {
    // A start in flight has no run id yet: remember the request and stop the
    // run as soon as the launcher records it (the launcher also checks the
    // stop marker before it starts the agent).
    if (pending === 'starting' && !activeRunId) {
      cancelStartRef.current = true
      setPending('stopping')
      snackbar('Stop requested — the run will stop as soon as the launcher records it.', {
        severity: 'info',
        duration: 'long',
        dismissible: true,
      })
      return
    }
    // TUI parity: stopping while a batch runs cancels the queue as well as the
    // current member (remaining members become skipped, batch interrupted).
    if (batchRunning && batchProgress && !batchCancelling) {
      setBatchCancelling(true)
      void tuiCancelBatch(batchProgress.batchId)
        .then(() => {
          snackbar('Batch stop requested — the current member stops and the remaining members are skipped.', {
            severity: 'info',
            duration: 'long',
            dismissible: true,
          })
        })
        .catch((error: unknown) => {
          setBatchCancelling(false)
          snackbar(`Batch cancel failed: ${errorText(error)}`, {
            severity: 'error',
            duration: 'long',
            dismissible: true,
          })
        })
    }
    if (!activeRunId || pending === 'stopping' || transcript?.status !== 'running') return
    setPending('stopping')
    liveRef.current = true // keep polling until the launcher records the end
    void tuiStopRun(activeRunId).catch((error: unknown) => {
      setPending(null)
      snackbar(`Stop was not accepted: ${errorText(error)}`, {
        severity: 'error',
        duration: 'long',
        dismissible: true,
      })
    })
  }, [activeRunId, pending, transcript, batchRunning, batchProgress, batchCancelling])

  const chooseSource = useCallback(async () => {
    setPicking(true)
    try {
      const picked = await pickVolumeFile()
      if (!picked.cancelled && picked.path) {
        // The follow effect below imports the file and switches the preview,
        // so the Stage always ends up on exactly this volume.
        setSource(picked.path)
      }
    } catch (error: unknown) {
      snackbar(
        `Could not open the file browser: ${errorText(error)}. The picker runs in the local harness — start it with scripts/dev.ps1 and retry.`,
        { severity: 'error', duration: 'long', dismissible: true },
      )
    } finally {
      setPicking(false)
    }
  }, [])
  const chooseSourceRef = useRef(chooseSource)
  chooseSourceRef.current = chooseSource

  // Single source of truth: the preview always follows the selected volume.
  // Whenever `source` changes (settings load, volume dialog, composer picker),
  // resolve the matching preview import — reuse a previous conversion when one
  // exists, convert once otherwise — so the Stage never stays on a stale
  // built-in while the agent runs on the selected file.
  const syncedSourceRef = useRef<string | null>(null)
  useEffect(() => {
    const path = source.trim()
    if (!path) return
    if (previewImport && sameVolumePath(previewImport.source, path)) {
      syncedSourceRef.current = path
      return
    }
    if (syncedSourceRef.current === path) return
    syncedSourceRef.current = path
    let cancelled = false
    void (async () => {
      try {
        const existing = await listVolumeImports()
        if (cancelled) return
        const hit = (existing.imports ?? []).find((item) => item.source && sameVolumePath(item.source, path))
        if (hit?.importId) {
          const record = await fetchVolumeImport(hit.importId)
          if (!cancelled) setPreviewImport(record)
          return
        }
      } catch {
        if (cancelled) return
        // Fall through to a fresh conversion below.
      }
      if (cancelled) return
      try {
        const record = await importVolumeFile(path)
        if (!cancelled) setPreviewImport(record)
      } catch (error: unknown) {
        if (cancelled) return
        syncedSourceRef.current = null
        snackbar(`Preview could not follow the selected volume: ${errorText(error)}`, {
          severity: 'error',
          duration: 'long',
          dismissible: true,
        })
      }
    })()
    return () => {
      cancelled = true
    }
  }, [source, previewImport])

  const toggleTheme = useCallback(() => {
    // Core owns the mode (data-fynns-theme on <html>); read it back instead
    // of keeping a parallel state that could drift.
    const next = getFynnsThemeMode() === 'light' ? 'dark' : 'light'
    try {
      applyFynnsThemeMode(next)
    } catch {
      /* ignore */
    }
  }, [])

  /* -- slash commands ----------------------------------------------------- */

  const runSlash = useCallback(
    async (action: SlashAction, args: string) => {
      switch (action) {
        case 'new':
          handleNewTask()
          if (args) await startCustomRun(args)
          return
        case 'task-a':
          await startCustomRun('', 'task-a')
          return
        case 'task-b':
          await startCustomRun('', 'task-b')
          return
        case 'verify': {
          if (!activeRunId) {
            snackbar('No open run to verify.', { severity: 'error', duration: 'long', dismissible: true })
            return
          }
          setVerify({ running: true, result: null })
          try {
            await tuiVerifyRun(activeRunId)
            for (let attempt = 0; attempt < 150; attempt++) {
              await new Promise((resolve) => setTimeout(resolve, 2000))
              const state = await tuiVerifyState(activeRunId)
              if (!state.running && state.result) {
                setVerify({ running: false, result: state.result })
                return
              }
            }
            setVerify((prev) => ({ ...prev, running: false }))
            snackbar('Verify is still running — reopen /verify to read it.', {
              severity: 'info',
              duration: 'long',
              dismissible: true,
            })
          } catch (error: unknown) {
            setVerify({ running: false, result: null })
            snackbar(`Verify failed: ${errorText(error)}`, {
              severity: 'error',
              duration: 'long',
              dismissible: true,
            })
          }
          return
        }
        case 'copy-json':
        case 'copy-chat': {
          if (!activeRunId) {
            snackbar('No open run to copy.', { severity: 'error', duration: 'long', dismissible: true })
            return
          }
          try {
            const data = await tuiCopyRun({ runId: activeRunId, kind: action === 'copy-json' ? 'json' : 'chat' })
            snackbar(data.message, { severity: 'info', duration: 'long', dismissible: true })
          } catch (error: unknown) {
            snackbar(`Copy failed: ${errorText(error)}`, {
              severity: 'error',
              duration: 'long',
              dismissible: true,
            })
          }
          return
        }
        case 'export-chat': {
          if (!activeRunId) {
            snackbar('No open run to export.', { severity: 'error', duration: 'long', dismissible: true })
            return
          }
          try {
            const file = await tuiDownloadChatLog(activeRunId)
            downloadBlob(new Blob([file.text], { type: 'text/plain;charset=utf-8' }), file.name)
            snackbar(`Saved ${file.name} to your downloads.`, {
              severity: 'info',
              duration: 'long',
              dismissible: true,
            })
          } catch (error: unknown) {
            snackbar(`Export failed: ${errorText(error)}`, {
              severity: 'error',
              duration: 'long',
              dismissible: true,
            })
          }
          return
        }
        case 'export-images': {
          if (!activeRunId) {
            snackbar('No open run to export.', { severity: 'error', duration: 'long', dismissible: true })
            return
          }
          try {
            const response = await fetch(`/api/tui/runs/${encodeURIComponent(activeRunId)}/images.zip`)
            if (!response.ok) throw new Error(`Export failed (${response.status})`)
            downloadBlob(await response.blob(), `${activeRunId}-images.zip`)
            snackbar('Saved the frame zip to your downloads.', {
              severity: 'info',
              duration: 'long',
              dismissible: true,
            })
          } catch (error: unknown) {
            snackbar(`Export failed: ${errorText(error)}`, {
              severity: 'error',
              duration: 'long',
              dismissible: true,
            })
          }
          return
        }
        case 'open-image': {
          if (!activeRunId) {
            snackbar('No open run.', { severity: 'error', duration: 'long', dismissible: true })
            return
          }
          try {
            const data = await tuiRunImages(activeRunId)
            const latest = data.images[data.images.length - 1]
            if (!latest) {
              snackbar('This run has no frames yet.', { severity: 'info', duration: 'long', dismissible: true })
              return
            }
            const data2 = await tuiOpenPath(String(latest.image))
            snackbar(data2.message, { severity: 'info', duration: 'long', dismissible: true })
          } catch (error: unknown) {
            snackbar(`Open failed: ${errorText(error)}`, {
              severity: 'error',
              duration: 'long',
              dismissible: true,
            })
          }
          return
        }
        case 'open-folder': {
          if (!transcript) {
            snackbar('No open run.', { severity: 'error', duration: 'long', dismissible: true })
            return
          }
          try {
            const data = await tuiOpenPath(transcript.directory)
            snackbar(data.message, { severity: 'info', duration: 'long', dismissible: true })
          } catch (error: unknown) {
            snackbar(`Open failed: ${errorText(error)}`, {
              severity: 'error',
              duration: 'long',
              dismissible: true,
            })
          }
          return
        }
        case 'history':
          void refreshRuns()
          void refreshBatches()
          setHistoryOpen(true)
          return
        case 'batch':
          setBatchOpen(true)
          return
        case 'archive-all':
          setArchiveConfirm(true)
          return
        case 'change-volume':
          void ensureVolumes()
          setVolumesOpen(true)
          return
        case 'settings':
          void ensureVolumes()
          setSettingsOpen(true)
          return
        case 'theme':
          toggleTheme()
          return
        case 'help':
          setHelpOpen(true)
          return
        case 'glossary':
          setGlossaryOpen(true)
          return
        case 'hard-stop': {
          if (activeRunId) {
            try {
              // Same immediate feedback as the Stop button:淡灰 + 忙态 until the launcher settles.
              setPending('stopping')
              liveRef.current = true
              await tuiStopRun(activeRunId)
              snackbar('Stop marker written — the launcher kills the agent tree; services stay up.', {
                severity: 'info',
                duration: 'long',
                dismissible: true,
              })
            } catch (error: unknown) {
              setPending(null)
              snackbar(`Hard stop failed: ${errorText(error)}`, {
                severity: 'error',
                duration: 'long',
                dismissible: true,
              })
            }
          } else {
            snackbar('Nothing live to stop.', { severity: 'info', duration: 'long', dismissible: true })
          }
          return
        }
        case 'quit':
          setQuitConfirm(true)
          return
      }
    },
    [activeRunId, transcript, handleNewTask, startCustomRun, refreshRuns, refreshBatches, toggleTheme, ensureVolumes],
  )

  const handleSend = useCallback(
    (value: string) => {
      const parsed = parseSlashCommand(value)
      pushHistory(value)
      setDraft('')
      setHistIdx(null)
      if (parsed) {
        void runSlash(parsed.action, parsed.args)
        return
      }
      void startCustomRun(value)
    },
    [runSlash, startCustomRun, pushHistory],
  )

  /* -- settings / volumes / archive / batch writes -------------------------- */

  const persistSettings = useCallback(async (next: TuiSettings) => {
    try {
      await tuiSaveSettings(next)
    } catch (error: unknown) {
      snackbar(`Settings not saved: ${errorText(error)}`, {
        severity: 'error',
        duration: 'long',
        dismissible: true,
      })
    }
  }, [])

  const flushPendingSettings = useCallback(() => {
    if (settingsSaveTimer.current !== null) {
      window.clearTimeout(settingsSaveTimer.current)
      settingsSaveTimer.current = null
    }
    const pending = pendingSettings.current
    pendingSettings.current = null
    if (pending) void persistSettings(pending)
  }, [persistSettings])

  // No Save button: each valid edit applies right away and is written to the same
  // file the TUI uses; the debounce only collapses rapid keystrokes into one write.
  const handleSettingsChange = useCallback(
    (next: TuiSettings) => {
      setSettings(next)
      setSource(next.volumePath || '')
      pendingSettings.current = next
      if (settingsSaveTimer.current !== null) window.clearTimeout(settingsSaveTimer.current)
      settingsSaveTimer.current = window.setTimeout(flushPendingSettings, 400)
    },
    [flushPendingSettings],
  )

  const handleSettingsOpenChange = useCallback(
    (open: boolean) => {
      setSettingsOpen(open)
      if (!open) flushPendingSettings()
    },
    [flushPendingSettings],
  )

  const handlePickVolume = useCallback(
    async (volume: TuiVolume) => {
      setSource(volume.path)
      setVolumesOpen(false)
      if (!settings) return
      try {
        const saved = await tuiSaveSettings({ ...settings, volumePath: volume.path })
        setSettings(saved)
        snackbar(`Next run works on ${volume.label}; the Stage follows it automatically.`, {
          severity: 'info',
          duration: 'long',
          dismissible: true,
        })
      } catch (error: unknown) {
        snackbar(`Volume selected locally; settings not saved: ${errorText(error)}`, {
          severity: 'error',
          duration: 'long',
          dismissible: true,
        })
      }
    },
    [settings],
  )

  const handleStageSourceChange = useCallback(
    (path: string) => {
      setSource(path)
      // The Stage stays the workbench: its volume becomes the next run's volume.
      if (settings && path !== settings.volumePath) {
        const next = { ...settings, volumePath: path }
        setSettings(next)
        void tuiSaveSettings(next).catch((error: unknown) => {
          snackbar(`Stage volume kept locally; settings not saved: ${errorText(error)}`, {
            severity: 'error',
            duration: 'long',
            dismissible: true,
          })
        })
      }
    },
    [settings],
  )

  const handleArchiveAll = useCallback(async () => {
    setArchiving(true)
    try {
      const result = await tuiArchiveAll()
      setArchiveConfirm(false)
      const latest = await refreshRuns()
      await refreshArchived()
      if (activeRunId && !(latest ?? []).some((run) => run.runId === activeRunId)) handleNewTask()
      snackbar(
        `Archived ${result.moved.length} runs${result.skipped_live.length ? `; kept live: ${result.skipped_live.join(', ')}` : ''}.`,
        { severity: 'info', duration: 'long', dismissible: true },
      )
    } catch (error: unknown) {
      snackbar(`Archive failed: ${errorText(error)}`, {
        severity: 'error',
        duration: 'long',
        dismissible: true,
      })
    } finally {
      setArchiving(false)
    }
  }, [activeRunId, handleNewTask, refreshRuns, refreshArchived])

  const handleArchiveRun = useCallback(
    async (runId: string) => {
      if (archivingOne !== null) return
      setArchivingOne(runId)
      try {
        await tuiArchiveRun(runId)
        const latest = await refreshRuns()
        await refreshArchived()
        if (activeRunId === runId && !(latest ?? []).some((run) => run.runId === runId)) handleNewTask()
        snackbar(`Archived ${runId} — find it under Archived runs.`, {
          severity: 'info',
          duration: 'long',
          dismissible: true,
        })
      } catch (error: unknown) {
        snackbar(`Archive failed: ${errorText(error)}`, {
          severity: 'error',
          duration: 'long',
          dismissible: true,
        })
      } finally {
        setArchivingOne(null)
      }
    },
    [activeRunId, archivingOne, handleNewTask, refreshArchived, refreshRuns],
  )

  const handleUnarchiveRun = useCallback(
    async (runId: string) => {
      if (unarchiving !== null) return
      setUnarchiving(runId)
      try {
        await tuiUnarchiveRun(runId)
        await refreshRuns()
        await refreshArchived()
        openRun(runId)
        snackbar(`Restored ${runId} to history.`, { severity: 'info', duration: 'long', dismissible: true })
      } catch (error: unknown) {
        snackbar(`Unarchive failed: ${errorText(error)}`, {
          severity: 'error',
          duration: 'long',
          dismissible: true,
        })
      } finally {
        setUnarchiving(null)
      }
    },
    [openRun, refreshArchived, refreshRuns, unarchiving],
  )

  // Left-nav menus (core renderMenu extension point): rows keep the ChatGPT
  // order Rename / Archive / Delete; blank-area right-click and the More
  // menu offer New task / Archive all / Delete all. Selection menus stay
  // off — the drawer does not enable multi-select.
  const renderRunMenu = useCallback(
    (context: SelectionMenuContext): ReactNode => {
      if (context.kind === 'item' && context.targetId != null) {
        const id = context.targetId
        return (
          <>
            <DropdownMenuItem
              icon={<PencilIcon aria-hidden />}
              onClick={() => {
                const run = chatRuns.find((item) => item.runId === id)
                if (!run) return
                setRenameTarget(run)
                setRenameDraft(run.task)
              }}
            >
              Rename
            </DropdownMenuItem>
            <DropdownMenuItem icon={<ArchiveIcon aria-hidden />} onClick={() => void handleArchiveRun(id)}>
              Archive
            </DropdownMenuItem>
            <DropdownMenuSeparator />
            <DropdownMenuItem
              icon={<TrashIcon aria-hidden />}
              tone="danger"
              onClick={() => {
                const run = chatRuns.find((item) => item.runId === id)
                if (run) setDeleteTarget(run)
              }}
            >
              Delete
            </DropdownMenuItem>
          </>
        )
      }
      if (context.kind === 'selection') return null
      return (
        <>
          <DropdownMenuItem icon={<PlusIcon aria-hidden />} onClick={handleNewTask}>
            New task
          </DropdownMenuItem>
          <DropdownMenuSeparator />
          <DropdownMenuItem
            icon={<ArchiveIcon aria-hidden />}
            disabled={archiving || chatRuns.length === 0}
            onClick={() => setArchiveConfirm(true)}
          >
            Archive all
          </DropdownMenuItem>
          <DropdownMenuItem
            icon={<TrashIcon aria-hidden />}
            tone="danger"
            disabled={chatRuns.length === 0}
            onClick={() => setDeleteAllConfirm(true)}
          >
            Delete all
          </DropdownMenuItem>
        </>
      )
    },
    [chatRuns, handleNewTask, handleArchiveRun, archiving],
  )

  const handleRenameRun = useCallback(async () => {
    if (!renameTarget || renameSaving) return
    const name = renameDraft.trim()
    if (!name) {
      snackbar('Name must not be empty.', { severity: 'error', duration: 'long', dismissible: true })
      return
    }
    setRenameSaving(true)
    try {
      await tuiRenameRun(renameTarget.runId, name)
      setRenameTarget(null)
      await refreshRuns()
      if (activeRunId === renameTarget.runId) openRun(renameTarget.runId, { reload: true })
      snackbar(`Renamed to “${name}”.`, { severity: 'info', duration: 'long', dismissible: true })
    } catch (error: unknown) {
      snackbar(`Rename failed: ${errorText(error)}`, {
        severity: 'error',
        duration: 'long',
        dismissible: true,
      })
    } finally {
      setRenameSaving(false)
    }
  }, [renameTarget, renameDraft, renameSaving, refreshRuns, activeRunId, openRun])

  const handleDeleteRun = useCallback(async () => {
    if (!deleteTarget || deleting) return
    setDeleting(true)
    try {
      await tuiDeleteRun(deleteTarget.runId)
      setDeleteTarget(null)
      const latest = await refreshRuns()
      if (activeRunId === deleteTarget.runId) {
        if (!(latest ?? []).some((run) => run.runId === deleteTarget.runId)) handleNewTask()
      }
      snackbar(`Deleted ${deleteTarget.runId}.`, { severity: 'info', duration: 'long', dismissible: true })
    } catch (error: unknown) {
      snackbar(`Delete failed: ${errorText(error)}`, {
        severity: 'error',
        duration: 'long',
        dismissible: true,
      })
    } finally {
      setDeleting(false)
    }
  }, [deleteTarget, deleting, refreshRuns, activeRunId, handleNewTask])

  const handleDeleteAllRuns = useCallback(async () => {
    if (deletingAll) return
    setDeletingAll(true)
    try {
      // Batch members are not chat history; delete only the single-run list.
      const targets = chatRuns.filter((run) => run.status !== 'Running')
      const failed: string[] = []
      for (const run of targets) {
        try {
          await tuiDeleteRun(run.runId)
        } catch {
          failed.push(run.runId)
        }
      }
      setDeleteAllConfirm(false)
      const latest = await refreshRuns()
      await refreshBatches(true)
      if (activeRunId && !(latest ?? []).some((run) => run.runId === activeRunId)) handleNewTask()
      snackbar(
        `Deleted ${targets.length - failed.length} chats${failed.length ? `; kept: ${failed.join(', ')}` : ''}.`,
        { severity: 'info', duration: 'long', dismissible: true },
      )
    } catch (error: unknown) {
      snackbar(`Delete all failed: ${errorText(error)}`, {
        severity: 'error',
        duration: 'long',
        dismissible: true,
      })
    } finally {
      setDeletingAll(false)
    }
  }, [deletingAll, chatRuns, refreshRuns, refreshBatches, activeRunId, handleNewTask])

  const handleStartBatch = useCallback(
    async (count: number, task: string) => {
      if (!settings) return
      if (batchRunning) {
        snackbar('A batch is already running — stop it first.', {
          severity: 'warning',
          duration: 'long',
          dismissible: true,
        })
        return
      }
      if (pending !== null || liveRef.current || runs.some((run) => run.status === 'Running')) {
        snackbar('A run is still live — stop it first.', {
          severity: 'warning',
          duration: 'long',
          dismissible: true,
        })
        return
      }
      const goal = task.trim()
      if (!goal) {
        snackbar('Batch task must not be empty.', {
          severity: 'error',
          duration: 'long',
          dismissible: true,
        })
        return
      }
      setBatchStarting(true)
      try {
        const data = await tuiStartBatch({
          count,
          task: goal,
          settings: { ...settings, volumePath: source.trim() },
        })
        setBatchOpen(false)
        // TUI parity: do not park the user in a modal. Clear to the new-task
        // view, then follow each member run in the chat as it launches.
        handleNewTask()
        setBatchDetail(null)
        setBatchDetailOpen(false)
        setBatchCancelling(false)
        setBatchProgress({
          batchId: data.batchId,
          status: 'running',
          startedAt: Date.now() / 1000,
          latestRunId: null,
        })
        setNavMode('batches')
        snackbar(`Batch ${data.batchId} started — the current member opens here as it launches.`, {
          severity: 'info',
          duration: 'long',
          dismissible: true,
        })
        void refreshRuns()
        void refreshBatches(true)
      } catch (error: unknown) {
        snackbar(`Batch not started: ${errorText(error)}`, {
          severity: 'error',
          duration: 'long',
          dismissible: true,
        })
      } finally {
        setBatchStarting(false)
      }
    },
    [settings, source, batchRunning, pending, runs, handleNewTask, refreshRuns, refreshBatches],
  )

  const handleOpenBatch = useCallback(async (batchId: string) => {
    try {
      const detail = await tuiBatchDetail(batchId)
      setBatchDetail(detail)
      setBatchDetailOpen(true)
      if (String(detail.status) === 'running') {
        setBatchProgress((prev) =>
          prev && prev.batchId === batchId
            ? batchProgressFromRecord(detail, prev)
            : batchProgressFromRecord(detail, null),
        )
        setNavMode('batches')
      }
    } catch (error: unknown) {
      snackbar(`Batch detail failed: ${errorText(error)}`, {
        severity: 'error',
        duration: 'long',
        dismissible: true,
      })
    }
  }, [])

  /* -- global keys: F1/F2/Ctrl+T/Ctrl+Q/Esc ----------------------------------- */

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null
      const inField =
        !!target && (target.tagName === 'INPUT' || target.tagName === 'TEXTAREA' || target.isContentEditable)
      if (event.key === 'F1') {
        event.preventDefault()
        setHelpOpen(true)
        return
      }
      if (event.key === 'F2') {
        event.preventDefault()
        setGlossaryOpen(true)
        return
      }
      if (event.ctrlKey && (event.key === 't' || event.key === 'T')) {
        event.preventDefault()
        toggleTheme()
        return
      }
      if (event.ctrlKey && (event.key === 'q' || event.key === 'Q')) {
        event.preventDefault()
        setQuitConfirm(true)
        return
      }
      if (event.key === 'Escape' && !inField) {
        // TUI parity: Esc closes a modal and stops nothing. Only when no
        // dialog is open does it stop the live run / cancel the batch queue.
        const modalOpen = !!document.querySelector('[role="dialog"], [aria-modal="true"]')
        if (!modalOpen && (activeRef.current || batchRunning)) handleStop()
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [handleStop, toggleTheme, batchRunning])

  /* -- derived view ---------------------------------------------------------- */

  const openZoom = useCallback((payload: ZoomPayload) => {
    setZoom(payload)
    setZoomOpen(true)
  }, [])

  const live = pending !== null || (transcript != null && transcript.status === 'running')
  const stopAvailable = pending === null && !!activeRunId && transcript?.status === 'running'

  type RunRow = { key: string; block: TuiBlock | null; group: TurnGroup | null; node: ReactNode }
  const rowItems: RunRow[] = []
  let openAnswer = false
  // Failed start with no minted run: plain chat rows like gsc-live-preview
  // (user turn + assistant red error text with retry), no InlineAlert.
  if (transcript?.runId === 'local-start-failed') {
    const userBlock = transcript.blocks.find((block) => block.kind === 'user')
    const errBlock = transcript.blocks.find((block) => block.kind === 'error')
    const taskText = userBlock?.title ?? transcript.task
    const errText = errBlock?.title || 'Could not start the task'
    const retryData = (userBlock?.data ?? {}) as { retryTask?: unknown; retryKind?: unknown }
    const retryTask = typeof retryData.retryTask === 'string' ? retryData.retryTask : transcript.task
    const retryKind =
      retryData.retryKind === 'task-a' || retryData.retryKind === 'task-b' ? retryData.retryKind : 'custom'
    rowItems.push({
      key: 'user',
      block: null,
      group: null,
      node: (
        <ChatMessage key="user" role="user">
          {taskText}
        </ChatMessage>
      ),
    })
    rowItems.push({
      key: 'failed-assistant',
      block: null,
      group: null,
      node: (
        <ErrorRow
          key="failed-assistant"
          message={errText}
          onRetry={() => void startCustomRun(retryTask, retryKind)}
        />
      ),
    })
  }
  const serverUser = transcript?.blocks.find((block) => block.kind === 'user')
  if (pendingTask?.text && !serverUser) {
    // The launcher has not minted the run record yet: paint the user's turn
    // now so submit never falls back to the idle empty state.
    const optimistic: TuiBlock = {
      kind: 'user',
      title: pendingTask.text,
      subtitle: '',
      body: '',
      status: null,
      ts: pendingTask.startedAt / 1000,
      data: {},
    }
    rowItems.push({
      key: 'user',
      block: optimistic,
      group: null,
      node: (
        <TuiBlockRow
          key="user"
          block={optimistic}
          initialThinking={false}
          onZoom={openZoom}
        />
      ),
    })
  }
  if (transcript && transcript.runId !== 'local-start-failed') {
    // A terminal run has no later merge: show an open assistant block as
    // closed instead of hiding it. Live blocks keep their open flag so a
    // streamed answer can still fold into the next tool call.
    const visibleBlocks = transcript.blocks
      .filter((block) => block.kind !== 'meta')
      .map((block) => {
        const open = (block.data as { open?: unknown } | null)?.open === true
        return block.kind === 'assistant' && open && transcript.status !== 'running'
          ? { ...block, data: { ...block.data, open: false } }
          : block
      })
    const initialThinkingIndex = visibleBlocks.findIndex((block) => block.kind === 'thinking')
    for (const row of buildTurnRows(visibleBlocks, transcript.status)) {
      if (row.kind === 'group') {
        rowItems.push({
          key: row.key,
          block: null,
          group: row.group,
          node: <TurnGroupRow key={row.key} group={row.group} onZoom={openZoom} />,
        })
        continue
      }
      const block = row.block
      const open = (block.data as { open?: unknown } | null)?.open === true
      // A streamed answer is still open until the call finishes. It can still
      // merge into the next tool call, so it must not be painted as an answer
      // row before it is closed.
      if (block.kind === 'assistant' && open && transcript.status === 'running') {
        openAnswer = true
        continue
      }
      if (row.key === 'user' && rowItems.some((item) => item.key === 'user')) continue
      rowItems.push({
        key: row.key,
        block,
        group: null,
        node: (
          <TuiBlockRow
            key={row.key}
            block={block}
            initialThinking={block.kind === 'thinking' && row.index === initialThinkingIndex}
            onZoom={openZoom}
            onRetryError={() => void startCustomRun(transcript.task || '', 'custom')}
          />
        ),
      })
    }
  }

  const rowIsSettled = useCallback((row: RunRow) => {
    if (row.group) return !row.group.running
    const block = row.block
    if (!block) return true
    if (block.kind === 'assistant') return (block.data as { open?: unknown } | null)?.open !== true
    return block.status !== 'running'
  }, [])
  const { visibleItems, waiting } = useChatSequencer<RunRow>({
    items: rowItems,
    getKey: (row) => row.key,
    seed: startedHere ? 'first' : 'all',
    resetKey: `${viewEpoch}:${transcript ? (startedHere ? 'live' : 'history') : 'empty'}`,
    isSettled: rowIsSettled,
  })
  const lastVisibleRow = visibleItems[visibleItems.length - 1] ?? null
  const lastVisibleBlock = lastVisibleRow?.block ?? null
  const lastVisibleGroup = lastVisibleRow?.group ?? null
  // A streaming/loading recorded row is the only thing allowed to animate:
  // never attach a second sweeping status below it.
  const visibleSettled = visibleItems.every(rowIsSettled)
  // One real tail status while the run is live. The recorded rows stay above
  // it; it is the only persistent animation and changes wording in place.
  let liveStage: string | null = null
  if (pending === 'stopping') {
    liveStage = 'Stopping the run'
  } else if (pending === 'starting' && !transcript) {
    liveStage = 'Starting the run'
  } else if (activeRunId && !transcript) {
    liveStage = 'Reading the run record'
  } else if (transcript?.status === 'running') {
    if (openAnswer) {
      liveStage = 'Writing the answer'
    } else if (lastVisibleGroup) {
      // The turn group's own header is the live status while it works; once
      // its work is done, the next thing is the model's answer.
      liveStage = lastVisibleGroup.running ? null : 'Waiting for the model'
    } else if (!lastVisibleBlock || lastVisibleBlock.kind === 'user') {
      liveStage = 'Taking the initial frame'
    } else if (lastVisibleBlock.kind === 'notices') {
      liveStage = 'Starting the agent'
    } else if (lastVisibleBlock.kind === 'warn') {
      liveStage = 'Waiting for the model'
    } else if (lastVisibleBlock.kind === 'thinking' || lastVisibleBlock.kind === 'compaction') {
      liveStage = lastVisibleBlock.status === 'running' ? null : 'Waiting for the model'
    } else if (lastVisibleBlock.kind === 'tool') {
      liveStage = lastVisibleBlock.status === 'running' ? null : 'Waiting for the next step'
    } else if (lastVisibleBlock.kind === 'image') {
      liveStage = 'Waiting for the model'
    } else if (lastVisibleBlock.kind === 'assistant') {
      liveStage = 'Waiting for the model'
    } else if (lastVisibleBlock.kind === 'finish') {
      liveStage = 'Writing the summary'
    } else if (lastVisibleBlock.kind === 'error') {
      liveStage = 'Waiting for the run to end'
    } else {
      liveStage = 'Waiting for the next step'
    }
  } else if (waiting > 0) {
    liveStage = 'Finishing the transcript'
  }
  if (!visibleSettled) liveStage = null

  // The tail status is the second component of a started run: let the user's
  // own turn (and the previous recorded row) finish entering before attaching
  // the live status below it. While it stays mounted, only its wording changes.
  // A queued recorded row goes first; a mounted status stays put and lets the
  // row insert above it, while a hidden status waits for the queue to empty so
  // two components never enter in one beat.
  const hasStage = liveStage !== null
  const stageCanShow = waiting === 0
  useEffect(() => {
    setStageVisible(false)
  }, [viewEpoch])
  useEffect(() => {
    if (!hasStage) {
      setStageVisible(false)
      return
    }
    if (!stageCanShow || stageVisible) return
    const timer = window.setTimeout(() => setStageVisible(true), chatEntryDelayMs())
    return () => window.clearTimeout(timer)
  }, [hasStage, stageCanShow, stageVisible, viewEpoch])

  const tail: ReactNode[] = []
  if (liveStage && stageVisible) {
    tail.push(
      <ChatMessage
        key="live-stage"
        role="assistant"
        thinking={
          <ChatThinking
            variant="status"
            streaming
            streamingLabel={liveStage}
            label={liveStage}
            details={[]}
          />
        }
      />,
    )
  }

  const slashOpen = draft.trimStart().startsWith('/')
  const slashMatches = slashOpen ? matchSlashCommands(draft.trim()) : []
  const slashList = slashOpen ? (
    <div className="gav-slash-menu" role="listbox" aria-label="Slash commands">
      {slashMatches.length === 0 ? (
        <div className="gav-slash-empty">No match — Enter sends the text as a task.</div>
      ) : (
        slashMatches.slice(0, 12).map((item, index) => (
          <button
            key={item.slash}
            type="button"
            role="option"
            aria-selected={index === slashSel}
            className={index === slashSel ? 'gav-slash-item gav-slash-item--active' : 'gav-slash-item'}
            onClick={() => {
              pushHistory(draft)
              setDraft('')
              void runSlash(item.action, draft.trim().split(/\s+/).slice(1).join(' '))
            }}
          >
            <span className="gav-slash-name">{item.slash}</span>
            <span className="gav-slash-tip">{item.tooltip}</span>
          </button>
        ))
      )}
    </div>
  ) : null

  const composerKeyDown = useCallback(
    (event: React.KeyboardEvent<HTMLTextAreaElement>) => {
      if (slashOpen) {
        if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
          event.preventDefault()
          setSlashSel((sel) => {
            const n = Math.min(slashMatches.length, 12)
            if (n === 0) return 0
            return event.key === 'ArrowDown' ? (sel + 1) % n : (sel - 1 + n) % n
          })
          return
        }
        if (event.key === 'Tab' && slashMatches.length > 0) {
          event.preventDefault()
          const pick = slashMatches[Math.min(slashSel, 11)]
          if (pick) setDraft(`${pick.slash} `)
          setSlashSel(0)
          return
        }
        if (event.key === 'Enter' && slashMatches.length > 0 && !event.shiftKey) {
          // Run the highlighted command instead of submitting raw text.
          const token = draft.trim().split(/\s+/)[0].toLowerCase()
          const exact = slashMatches.some((item) => item.slash === token)
          if (!exact) {
            event.preventDefault()
            const pick = slashMatches[Math.min(slashSel, 11)]
            if (pick) {
              const args = draft.trim().split(/\s+/).slice(1).join(' ')
              pushHistory(draft)
              setDraft('')
              setSlashSel(0)
              void runSlash(pick.action, args)
            }
            return
          }
        }
        if (event.key === 'Escape') {
          event.preventDefault()
          setDraft('')
          setSlashSel(0)
          return
        }
      } else if (!draft.includes('\n')) {
        // Shell-like history recall on single-line drafts (TUI Up/Down).
        if (event.key === 'ArrowUp' && history.length > 0) {
          event.preventDefault()
          const next = histIdx === null ? 0 : Math.min(histIdx + 1, history.length - 1)
          setHistIdx(next)
          setDraft(history[next])
          return
        }
        if (event.key === 'ArrowDown' && histIdx !== null) {
          event.preventDefault()
          if (histIdx === 0) {
            setHistIdx(null)
            setDraft('')
          } else {
            const next = histIdx - 1
            setHistIdx(next)
            setDraft(history[next])
          }
        }
      }
    },
    [slashOpen, slashMatches, slashSel, draft, history, histIdx, pushHistory, runSlash],
  )

  const navToggleLabel = navOpen ? 'Close navigation' : 'Open navigation'
  const asideToggleLabel = asideOpen ? 'Hide inspector' : 'Show inspector'
  const stageToggleLabel = stageOpen ? 'Collapse Live Preview stage' : 'Expand Live Preview stage'
  const chatPane = (
    <div className="gav-chat-pane" data-pane="chat">
      <FillColumn>
        {settings ? (
          <Chat label="Task conversation">
            <ChatThread
              label="Task conversation"
              empty={
                activeRunId && !transcript ? (
                  <LoadingSkeleton label="Loading run evidence" lines={8} />
                ) : (
                  <EmptyState
                    title="Start a run"
                    description="Describe the task, or type / for presets (/task-a, /task-b), history, batches, verify and exports. Every row is read from the recorded run evidence; model claims stay unverified."
                  />
                )
              }
            >
              {visibleItems.map((row) => row.node)}
              {tail}
            </ChatThread>
            <ChatScrollToBottom />
            <ChatComposer
            value={draft}
            onChange={(value) => {
              setDraft(value)
              setSlashSel(0)
              if (value.trim() === '') setHistIdx(null)
            }}
            onSubmit={handleSend}
            ariaLabel="Describe the task, or type / for commands"
            placeholder="Describe the task, or type / for commands"
            sendLabel="Start run"
            stopLabel="Stop"
            busy={live}
            stopping={pending === 'stopping'}
            onStop={stopAvailable || pending !== null ? handleStop : undefined}
            attachments={slashList}
            leading={
              <Tooltip content="Choose the local volume file">
                <IconButton
                  variant="ghost"
                  size="sm"
                  aria-label="Choose the local volume file"
                  disabled={picking}
                  aria-busy={picking || undefined}
                  onClick={() => void chooseSource()}
                >
                  <PlusIcon aria-hidden />
                </IconButton>
              </Tooltip>
            }
            endActions={
              <Tooltip content="Settings: model, volume, endpoints, budget">
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => setSettingsOpen(true)}
                >
                  {settings?.model ?? '…'}
                </Button>
              </Tooltip>
            }
            textareaProps={{ onKeyDown: composerKeyDown }}
            />
          </Chat>
        ) : (
          <BusyRegion busy fill label="Loading workspace" />
        )}
      </FillColumn>
    </div>
  )

  // Always mounted: collapsing the stage only hides it via CSS so the
  // preview iframe + Wasm raycaster stay alive but dormant.
  const stagePane = (
    <div className="gav-stage-pane" data-pane="stage">
      <LiveStage
        source={source}
        previewImport={previewImport}
        onPreviewImport={setPreviewImport}
        onSourceChange={handleStageSourceChange}
      />
    </div>
  )

  const latestImage = transcript
    ? [...transcript.blocks].reverse().find((block) => block.kind === 'image')
    : undefined

  const navModeTabs = (
    <Tabs
      tabs={[
        { id: 'batches', label: 'Batches' },
        { id: 'chats', label: 'Chats' },
      ]}
      activeId={navMode}
      onChange={(id) => setNavMode(id === 'batches' ? 'batches' : 'chats')}
      size="sm"
      ariaLabel="Sidebar view"
    />
  )
  const navFooter = (
    <div className="fynns-nav-drawer-footer-account">
      <div className="fynns-nav-drawer-footer-account-start">
        <Avatar size="sm" name="Local work" alt="Local work" />
        <span className="fynns-nav-drawer-footer-account-label">Local work</span>
      </div>
      <Tooltip content="Settings">
        <IconButton
          size="sm"
          aria-label="Settings"
          onClick={() => {
            void refreshArchived()
            setArchivedOpen(true)
          }}
        >
          <SettingsIcon size={16} aria-hidden />
        </IconButton>
      </Tooltip>
    </div>
  )

  const batchNav = (
    <NavigationDrawer
      variant="standard"
      ariaLabel="Batches"
      headline={navModeTabs}
      footer={navFooter}
    >
      <NavigationDrawerNewChat label="New batch" onClick={() => setBatchOpen(true)} />
      {batches.length === 0 ? (
        <EmptyState
          size="sm"
          title="No batches yet"
          description="New batch repeats one task with the same frozen config."
        />
      ) : (
        batches.map((batch) => {
          const config = (batch.config ?? {}) as Record<string, unknown>
          const task = String(config.task ?? '').trim() || batch.batchId
          return (
            <NavigationDrawerItem
              key={batch.batchId}
              label={task}
              active={batch.batchId === activeBatchId}
              running={batch.status === 'running'}
              runningLabel="Running"
              onClick={() => void handleOpenBatch(batch.batchId)}
            />
          )
        })
      )}
    </NavigationDrawer>
  )

  const chatNav = (
    <ChatSessionsDrawer
      ariaLabel="Chats"
      headline={navModeTabs}
      sessions={chatRuns.map((run) => ({
        id: run.runId,
        label: `${run.runId} — ${run.task.slice(0, 40)}`,
        running: run.status === 'Running',
      }))}
      activeSessionId={activeRunId}
      // Cold start: the run list is still being read. BusyRegion paints text
      // skeleton rows in place of the (wrong) "No chats yet" empty state.
      busy={!runsLoaded}
      labels={{
        newChat: 'New task',
        more: 'Chat options',
        rename: 'Rename',
        archive: 'Archive',
        delete: 'Delete',
        deleteAll: 'Delete all chats',
        sessionMenu: 'Chat options',
        listMenu: 'Chats',
        emptyTitle: 'No chats yet',
        emptyDescription: 'Send the first task from the composer, or /task-a for the audited preset.',
        loading: 'Loading chats',
        runningLabel: 'Running',
        revealMore: 'Show more chats',
      }}
      reveal={{ initial: 12, step: 12 }}
      renderMenu={renderRunMenu}
      footer={navFooter}
      onNewChat={() => {
        setNavMode('chats')
        handleNewTask()
      }}
      onSelect={(runId) => {
        setNavMode('chats')
        openRun(runId)
      }}
      onRename={(runId) => {
        const run = chatRuns.find((item) => item.runId === runId)
        if (!run) return
        setRenameTarget(run)
        setRenameDraft(run.task)
      }}
      onArchive={(runId) => void handleArchiveRun(runId)}
      onDelete={(runId) => {
        const run = chatRuns.find((item) => item.runId === runId)
        if (run) setDeleteTarget(run)
      }}
      onDeleteAll={() => setDeleteAllConfirm(true)}
    />
  )

  return (
    <>
      <ClippedNavShell
        className="gav-shell"
        navMode={navOpen ? 'drawer' : 'hidden'}
        onNavCrowded={() => setNavOpen(false)}
        topBar={
          <TopAppBar
            title="Agentic Volume"
            leading={
              <Tooltip content={navToggleLabel}>
                <IconButton
                  variant="ghost"
                  aria-label={navToggleLabel}
                  aria-pressed={navOpen}
                  onClick={() => setNavOpen((open) => !open)}
                >
                  {navOpen ? <PanelLeftIcon size={16} aria-hidden /> : <MenuIcon aria-hidden />}
                </IconButton>
              </Tooltip>
            }
            trailing={
              <div className="gav-topbar-trailing">
                {isNarrow ? (
                  <Tabs
                    tabs={[
                      { id: 'chat', label: 'Chat' },
                      { id: 'stage', label: 'Stage' },
                    ]}
                    activeId={narrowView}
                    onChange={setNarrowView}
                    size="sm"
                    ariaLabel="Workbench view"
                  />
                ) : null}
                {!isNarrow ? (
                  <>
                    <Tooltip content="Download .gsc + preview PNG to your downloads">
                      <IconButton
                        variant="ghost"
                        size="sm"
                        aria-label="Download .gsc and preview screenshot"
                        disabled={!stageOpen}
                        onClick={() => window.dispatchEvent(new CustomEvent(STAGE_EXPORT_EVENT))}
                      >
                        <DownloadIcon aria-hidden />
                      </IconButton>
                    </Tooltip>
                    <Tooltip content={stageToggleLabel}>
                      <Button variant="ghost" size="sm" aria-pressed={stageOpen} onClick={() => setStageOpen((v) => !v)}>
                        {stageOpen ? 'Collapse stage' : 'Expand stage'}
                      </Button>
                    </Tooltip>
                  </>
                ) : null}
                <Tooltip content={asideToggleLabel}>
                  <IconButton
                    variant="ghost"
                    aria-label={asideToggleLabel}
                    aria-pressed={asideOpen}
                    onClick={() => setAsideOpen((open) => !open)}
                  >
                    <PanelRightIcon size={16} aria-hidden />
                  </IconButton>
                </Tooltip>
              </div>
            }
          />
        }
        nav={
          navOpen ? (navMode === 'batches' ? batchNav : chatNav) : null
        }
      >
        <div className="fynns-destination-app-shell-body">
          <div className="fynns-destination-app-shell-canvas fynns-scroll" ref={canvasRef}>
            {isNarrow ? (
              <div className="gav-workbench gav-workbench--narrow">
                {!stageOpen || narrowView === 'chat' ? chatPane : stagePane}
              </div>
            ) : (
              <SplitPane
                start={chatPane}
                end={stagePane}
                size={stageOpen ? splitChatPx : rowWidth}
                onSizeChange={handleSplitSizeChange}
                minStart={chatMinPx}
                minEnd={stageMinPx}
                disableResize={!stageOpen}
                className={stageOpen ? undefined : 'gav-split--stage-closed'}
                label="Resize Chat and Live Preview (50/50 default)"
              />
            )}
          </div>
          <EndAside open={asideOpen}>
            <PageScroll>
              {transcript ? (
                <div className="fynns-unit-stack">
                  <Card title="Run">
                    <List>
                      <ListItem headline="State" trailingSupportingText={runStateLabel(transcript.status, pending)} />
                      <ListItem
                        headline="Observations"
                        trailingSupportingText={`${transcript.used} / ${transcript.budget}`}
                      />
                      <ListItem headline="Model" supportingText={transcript.model} />
                      <ListItem headline="Volume" supportingText={transcript.volumePath || source} lines={2} />
                      <ListItem headline="Task" supportingText={transcript.task} lines={3} />
                    </List>
                    <LinearProgress
                      value={transcript.budget > 0 ? transcript.used / transcript.budget : 0}
                      label={`Observation budget used: ${transcript.used} of ${transcript.budget}`}
                    />
                  </Card>
                  <Card title="Verification">
                    <BusyRegion busy={verify.running} label="Verifying evidence">
                      {verify.result ? (
                        <div className="fynns-unit-stack">
                          <CodeBlock label="verify summary" code={verify.result.summary.join('\n')} />
                          {verify.result.output ? (
                            <Collapsible title="Verifier output" chrome="plain">
                              <CodeBlock variant="plain" code={verify.result.output} />
                            </Collapsible>
                          ) : null}
                        </div>
                      ) : (
                        <EmptyState
                          size="sm"
                          title="Not verified yet"
                          description="Read-only check: execution / image transport / visual-quality UNVERIFIED."
                        />
                      )}
                    </BusyRegion>
                    <div className="fynns-control-cluster fynns-control-cluster--start-align">
                      <Button
                        variant="primary"
                        size="sm"
                        disabled={verify.running || !activeRunId}
                        onClick={() => void runSlash('verify', '')}
                      >
                        {verify.running ? 'Verifying…' : 'Verify evidence'}
                      </Button>
                    </div>
                  </Card>
                  <Card title="Evidence">
                    <div className="fynns-control-cluster fynns-control-cluster--start-align">
                      <Button variant="ghost" size="sm" disabled={!activeRunId} onClick={() => void runSlash('copy-chat', '')}>
                        Copy chat
                      </Button>
                      <Button variant="ghost" size="sm" disabled={!activeRunId} onClick={() => void runSlash('copy-json', '')}>
                        Copy JSON
                      </Button>
                      <Button variant="ghost" size="sm" disabled={!activeRunId} onClick={() => void runSlash('export-chat', '')}>
                        Download .log
                      </Button>
                      <Button
                        variant="ghost"
                        size="sm"
                        disabled={!activeRunId}
                        onClick={() => void runSlash('export-images', '')}
                      >
                        Frames .zip
                      </Button>
                      <Button variant="ghost" size="sm" disabled={!activeRunId} onClick={() => void runSlash('open-folder', '')}>
                        Open folder
                      </Button>
                    </div>
                    {latestImage ? (
                      <List>
                        <ListItem
                          headline="Latest frame"
                          supportingText={String((latestImage.data as { image?: unknown })?.image ?? '')}
                          lines={2}
                        />
                      </List>
                    ) : null}
                  </Card>
                  <Card title="Run files">
                    <ExpandableCodeBlock
                      label="transcript blocks (JSON)"
                      code={JSON.stringify(
                        transcript.blocks.map((block) => ({
                          kind: block.kind,
                          title: block.title,
                          subtitle: block.subtitle,
                          status: block.status,
                        })),
                        null,
                        2,
                      )}
                      previewLines={14}
                    />
                  </Card>
                </div>
              ) : (
                <EmptyState
                  size="sm"
                  title="No open run"
                  description="Run state, budget, verification and evidence appear here while a run is open."
                />
              )}
            </PageScroll>
          </EndAside>
        </div>
      </ClippedNavShell>

      {settings ? (
        <SettingsDialog
          open={settingsOpen}
          onOpenChange={handleSettingsOpenChange}
          settings={settings}
          volumes={volumes}
          onChange={handleSettingsChange}
        />
      ) : null}
      <VolumesDialog
        open={volumesOpen}
        onOpenChange={setVolumesOpen}
        volumes={volumes}
        current={source}
        loading={volumesLoading}
        onPick={handlePickVolume}
      />
      <HistoryDialog
        open={historyOpen}
        onOpenChange={setHistoryOpen}
        batches={batches}
        batchesLoading={batchesLoading}
        runs={chatRuns}
        activeRunId={activeRunId}
        loading={!runsLoaded}
        onOpen={(run) => {
          setNavMode('chats')
          openRun(run.runId)
        }}
        onOpenBatch={(batch) => {
          setHistoryOpen(false)
          setNavMode('batches')
          void handleOpenBatch(batch.batchId)
        }}
        onArchiveAll={() => setArchiveConfirm(true)}
        archiving={archiving}
      />
      <BatchDialog
        open={batchOpen}
        onOpenChange={setBatchOpen}
        taskPrefill={transcript?.task ?? ''}
        onStart={handleStartBatch}
        starting={batchStarting}
      />
      <BatchDetailDialog
        open={batchDetailOpen && batchDetail !== null}
        onOpenChange={(open) => {
          setBatchDetailOpen(open)
          if (!open) setBatchDetail(null)
        }}
        batch={batchDetail}
        onOpenMember={(runId) => {
          setBatchDetailOpen(false)
          setBatchDetail(null)
          openRun(runId)
        }}
        onCancel={async () => {
          if (!batchDetail) return
          setBatchCancelling(true)
          try {
            await tuiCancelBatch(batchDetail.batchId)
            const activeRun = runs.find((run) => run.runId === activeRunId)
            if (activeRunId && (transcript?.status === 'running' || activeRun?.status === 'Running')) {
              try {
                await tuiStopRun(activeRunId)
              } catch {
                // The member may already be recording its end.
              }
            }
            setBatchDetail(await tuiBatchDetail(batchDetail.batchId))
          } catch (error: unknown) {
            snackbar(`Batch cancel failed: ${errorText(error)}`, {
              severity: 'error',
              duration: 'long',
              dismissible: true,
            })
          } finally {
            setBatchCancelling(false)
          }
        }}
        cancelling={batchCancelling}
      />
      <GlossaryDialog open={glossaryOpen} onOpenChange={setGlossaryOpen} entries={glossary} />
      <HelpDialog open={helpOpen} onOpenChange={setHelpOpen} />
      <ArchivedChatsSettingsDialog
        open={archivedOpen}
        onOpenChange={setArchivedOpen}
        sessions={archivedRuns.map((run) => ({
          id: run.runId,
          label: `${run.runId} — ${run.task.slice(0, 40)}`,
          disabled: unarchiving !== null,
        }))}
        labels={{
          title: 'Settings',
          categories: 'Settings categories',
          general: 'General',
          generalSample: 'Dark theme',
          archived: 'Archived runs',
          unarchive: 'Unarchive',
          close: 'Close settings',
          archivedEmptyTitle: 'No archived runs',
          archivedEmptyDescription: 'Archive a run from its menu to hide it here.',
        }}
        onUnarchive={(runId) => void handleUnarchiveRun(runId)}
      />

      <ConfirmDialog
        open={archiveConfirm}
        onOpenChange={setArchiveConfirm}
        title="Archive all runs?"
        description="Every history run moves to runtime/archive (live runs are kept). Restore one from the drawer footer under Archived runs."
        confirmLabel="Archive all"
        danger
        loading={archiving}
        onConfirm={() => void handleArchiveAll()}
      />
      <Dialog
        open={renameTarget !== null}
        onOpenChange={(open) => {
          if (!open) setRenameTarget(null)
        }}
        title={renameTarget ? `Rename ${renameTarget.runId}` : 'Rename run'}
        description="Renames the task label in this run's config.json; the history list and the transcript title follow it."
        size="sm"
        feet={
          <div className="fynns-control-cluster fynns-control-cluster--end-align">
            <Button variant="ghost" size="sm" disabled={renameSaving} onClick={() => setRenameTarget(null)}>
              Cancel
            </Button>
            <Button
              variant="primary"
              size="sm"
              disabled={renameSaving || renameDraft.trim().length === 0}
              onClick={() => void handleRenameRun()}
            >
              {renameSaving ? 'Saving…' : 'Save name'}
            </Button>
          </div>
        }
      >
        <Input
          value={renameDraft}
          onChange={(event) => setRenameDraft(event.target.value)}
          spellCheck={false}
          autoComplete="off"
          maxLength={200}
          aria-label="Run name"
          placeholder="New run name"
          onKeyDown={(event) => {
            if (event.key === 'Enter') void handleRenameRun()
          }}
        />
      </Dialog>
      <ConfirmDialog
        open={deleteTarget !== null}
        onOpenChange={(open) => {
          if (!open) setDeleteTarget(null)
        }}
        title={deleteTarget ? `Delete ${deleteTarget.runId}?` : 'Delete run?'}
        description="The run folder and its frame images are deleted permanently. Live runs are kept — stop them first. There is no undelete."
        confirmLabel="Delete run"
        danger
        loading={deleting}
        onConfirm={() => void handleDeleteRun()}
      />
      <ConfirmDialog
        open={deleteAllConfirm}
        onOpenChange={setDeleteAllConfirm}
        title="Delete all runs?"
        description="Every history run folder and its frame images are deleted permanently (live runs are kept). There is no undelete."
        confirmLabel="Delete all"
        danger
        loading={deletingAll}
        onConfirm={() => void handleDeleteAllRuns()}
      />
      <ConfirmDialog
        open={quitConfirm}
        onOpenChange={setQuitConfirm}
        title="Stop and clear?"
        description="Stops the live run (evidence stays on disk) and clears the view. The backend keeps serving; the TUI hard-stop semantics for Ollama do not apply in the browser."
        confirmLabel="Stop and clear"
        danger
        onConfirm={() => {
          if (activeRunId) void tuiStopRun(activeRunId).catch(() => undefined)
          setQuitConfirm(false)
          handleNewTask()
        }}
      />

      <Dialog
        open={zoomOpen}
        onOpenChange={setZoomOpen}
        title="Renderer frame"
        description={zoom?.caption}
        size="lg"
        showCloseButton
        closeAriaLabel="Close frame preview"
      >
        {zoom ? <img className="gav-zoom-image" src={zoom.url} alt={zoom.alt} /> : null}
      </Dialog>

      <SnackbarHost />
    </>
  )
}

export default App
