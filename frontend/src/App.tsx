import { useCallback, useEffect, useLayoutEffect, useRef, useState, type ReactNode } from 'react'
import {
  BotIcon,
  BrainIcon,
  Button,
  Card,
  Chat,
  ChatComposer,
  ChatReveal,
  ChatScrollToBottom,
  ChatThread,
  CheckIcon,
  ClippedNavShell,
  CloseIcon,
  CodeBlock,
  Dialog,
  DownloadIcon,
  EmptyState,
  EndAside,
  ExpandableCodeBlock,
  FieldBlock,
  FieldStack,
  FileIcon,
  FillColumn,
  IconButton,
  Input,
  LinearProgress,
  List,
  ListItem,
  MenuIcon,
  NavigationDrawer,
  NavigationDrawerItem,
  NavigationDrawerNewChat,
  PageScroll,
  PanelLeftIcon,
  PanelRightIcon,
  PlusIcon,
  SnackbarHost,
  SplitPane,
  Tabs,
  TopAppBar,
  Tooltip,
  snackbar,
} from '@fynns/ui'
import {
  continueSession,
  errorText,
  fetchSession,
  importVolumeFile,
  pickVolumeFile,
  startSession,
  stopSession,
  type SessionData,
  type VolumeImport,
} from './api'
import {
  ActionRow,
  ErrorRow,
  FinishRow,
  FrameRow,
  LiveStatusRow,
  PauseControlRow,
  PauseRow,
  TaskMessage,
  type ZoomPayload,
} from './ChatRows'
import { LiveStage, STAGE_EXPORT_EVENT } from './preview/LiveStage'

const DEFAULT_MODEL = 'qwen3.5:9b'
const POLL_INTERVAL_MS = 1500
const STAGE_WIDTH_KEY = 'gav-stage-width'
const STAGE_OPEN_KEY = 'gav-stage-open'

type Pending = null | 'starting' | 'stopping' | 'continuing'
type TaskSnapshot = { goal: string; source: string; model: string }

function stateLabel(session: SessionData | null, pending: Pending): string {
  if (pending === 'starting') return 'Starting'
  if (pending === 'stopping') return 'Stop requested'
  if (pending === 'continuing') return 'Resuming'
  if (!session) return 'Idle'
  switch (session.state) {
    case 'created':
      return 'Starting'
    case 'running':
      return 'Running'
    case 'paused':
      return 'Paused'
    case 'budget_exhausted':
      return 'Budget exhausted'
    case 'finished':
      return 'Finished — claim unverified'
    case 'stopped':
      return 'Stopped'
    case 'failed':
      return 'Failed'
  }
}

/** Provable run state from the recorded event sequence. */
function liveLabel(session: SessionData | null): string {
  const last = session && session.events.length > 0 ? session.events[session.events.length - 1] : null
  if (!last) return 'Preparing the session — importing the volume and capturing the initial frame'
  if (last.kind === 'initial_render' || last.kind === 'render') {
    return 'Model is choosing the next action from the latest frame'
  }
  if (last.kind === 'model_action') {
    return last.action?.tool === 'render_scene'
      ? 'Native renderer is executing the accepted decision'
      : 'Closing the run'
  }
  return 'Waiting for the next recorded event'
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
  return 60
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

function App() {
  const [navOpen, setNavOpen] = useState(true)
  const [asideOpen, setAsideOpen] = useState(false)

  const [source, setSource] = useState('')
  const [model, setModel] = useState(DEFAULT_MODEL)
  const [draft, setDraft] = useState('')
  const [picking, setPicking] = useState(false)
  const [previewImport, setPreviewImport] = useState<VolumeImport | null>(null)

  const [modelOpen, setModelOpen] = useState(false)
  const [modelDraft, setModelDraft] = useState('')

  const [sessionId, setSessionId] = useState<string | null>(null)
  const [session, setSession] = useState<SessionData | null>(null)
  const [task, setTask] = useState<TaskSnapshot | null>(null)
  const [pending, setPending] = useState<Pending>(null)

  const [zoom, setZoom] = useState<ZoomPayload | null>(null)
  const [zoomOpen, setZoomOpen] = useState(false)
  const pollFailed = useRef(false)

  // Live Preview workbench: desktop Chat 40% / Stage 60% in a core SplitPane;
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

  // Chat floor follows the shell's protected chat width (23rem); the stage
  // keeps a 320px floor. SplitPane enforces both while dragging.
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

  useEffect(() => {
    if (!sessionId) return
    let cancelled = false
    let inFlight = false
    const poll = async () => {
      if (inFlight) return
      inFlight = true
      try {
        const data = await fetchSession(sessionId)
        if (cancelled || data.id !== sessionId) return
        pollFailed.current = false
        setSession(data)
        setPending((current) => {
          if (current === 'starting') return null
          if (current === 'stopping' && data.state !== 'running') return null
          if (current === 'continuing' && data.state !== 'paused') return null
          return current
        })
      } catch (error) {
        if (cancelled) return
        // One snackbar per contiguous failure streak; prior results stay on screen.
        if (!pollFailed.current) {
          pollFailed.current = true
          snackbar(`Could not refresh the session: ${errorText(error)}`, {
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
    const timer = window.setInterval(() => void poll(), POLL_INTERVAL_MS)
    return () => {
      cancelled = true
      window.clearInterval(timer)
    }
  }, [sessionId])

  const openZoom = useCallback((payload: ZoomPayload) => {
    setZoom(payload)
    setZoomOpen(true)
  }, [])

  const chooseSource = useCallback(async () => {
    setPicking(true)
    try {
      const picked = await pickVolumeFile()
      if (!picked.cancelled && picked.path) {
        setSource(picked.path)
        // Immediately import for Live Preview so the agent can reuse the snapshot.
        try {
          const record = await importVolumeFile(picked.path)
          setPreviewImport(record)
        } catch (importError: unknown) {
          snackbar(`Preview import failed: ${errorText(importError)}`, {
            severity: 'error',
            duration: 'long',
            dismissible: true,
          })
        }
      }
    } catch (error: unknown) {
      snackbar(
        `Could not open the file browser: ${errorText(error)}. The picker runs in the local harness — start it with scripts/dev.ps1 and retry.`,
        {
          severity: 'error',
          duration: 'long',
          dismissible: true,
        },
      )
    } finally {
      setPicking(false)
    }
  }, [])

  const handleSend = useCallback(
    (value: string) => {
      const goal = value.trim()
      if (!goal) return
      const cleanSource = source.trim()
      const cleanModel = model.trim()
      if (!cleanSource) {
        snackbar('Choose the local volume file first.', {
          severity: 'error',
          duration: 'long',
          dismissible: true,
        })
        void chooseSource()
        return
      }
      if (!cleanModel) {
        setModelDraft(model)
        setModelOpen(true)
        snackbar('Set the installed vision model before starting.', {
          severity: 'error',
          duration: 'long',
          dismissible: true,
        })
        return
      }
      setPending('starting')
      // Reuse the preview import snapshot when it matches the agent source,
      // avoiding reconversion and keeping session bytes identical.
      const importId =
        previewImport && previewImport.source === cleanSource ? previewImport.importId : undefined
      void startSession({ source: cleanSource, model: cleanModel, goal, ...(importId ? { importId } : {}) })
        .then(({ id }) => {
          setTask({ goal, source: cleanSource, model: cleanModel })
          setDraft('')
          setSession(null)
          setSessionId(id)
        })
        .catch((error: unknown) => {
          setPending(null)
          snackbar(`Could not start the task: ${errorText(error)}`, {
            severity: 'error',
            duration: 'long',
            dismissible: true,
          })
        })
    },
    [source, model, chooseSource, previewImport],
  )

  const handleStop = useCallback(() => {
    if (!sessionId) return
    setPending('stopping')
    void stopSession(sessionId).catch((error: unknown) => {
      setPending(null)
      snackbar(`Stop was not accepted: ${errorText(error)}`, {
        severity: 'error',
        duration: 'long',
        dismissible: true,
      })
    })
  }, [sessionId])

  const handleContinue = useCallback(() => {
    if (!sessionId) return
    setPending('continuing')
    void continueSession(sessionId).catch((error: unknown) => {
      setPending(null)
      snackbar(`Could not resume the session: ${errorText(error)}`, {
        severity: 'error',
        duration: 'long',
        dismissible: true,
      })
    })
  }, [sessionId])

  const handleNewTask = useCallback(() => {
    setSession(null)
    setSessionId(null)
    setPending(null)
    setTask(null)
    setDraft('')
  }, [])

  const editable = session === null && pending === null
  const runningish = pending !== null || session?.state === 'created' || session?.state === 'running'
  const stopAvailable = pending === null && (session?.state === 'created' || session?.state === 'running')
  const newTaskDisabled = pending !== null || session?.state === 'created' || session?.state === 'running'

  const rows: ReactNode[] = []
  if (task) {
    rows.push(<TaskMessage key="task" goal={task.goal} source={task.source} model={task.model} />)
  }
  if (session) {
    const decisionBySeq = new Map<number, number>()
    let decisions = 0
    for (const event of session.events) {
      if (event.kind === 'model_action') {
        decisions += 1
        decisionBySeq.set(event.seq, decisions)
      }
    }
    for (const event of session.events) {
      const key = `event-${event.seq}`
      switch (event.kind) {
        case 'initial_render':
        case 'render':
          rows.push(
            <ChatReveal key={key}>
              <FrameRow event={event} onZoom={openZoom} />
            </ChatReveal>,
          )
          break
        case 'model_action':
          rows.push(
            <ChatReveal key={key}>
              <ActionRow event={event} decision={decisionBySeq.get(event.seq) ?? 0} budget={session.budget} />
            </ChatReveal>,
          )
          break
        case 'pause':
          rows.push(
            <ChatReveal key={key}>
              <PauseRow event={event} budget={session.budget} />
            </ChatReveal>,
          )
          break
        case 'finish':
          rows.push(
            <ChatReveal key={key}>
              <FinishRow event={event} />
            </ChatReveal>,
          )
          break
        case 'error':
          rows.push(
            <ChatReveal key={key}>
              <ErrorRow event={event} stopped={session.state === 'stopped'} />
            </ChatReveal>,
          )
          break
      }
    }
  }

  const tail: ReactNode[] = []
  if (pending === 'starting') {
    tail.push(<LiveStatusRow key="live" label="Starting the session — importing the volume and capturing the initial frame" />)
  } else if (pending === 'stopping') {
    tail.push(<LiveStatusRow key="live" label="Stop requested — waiting for the current step to end" />)
  } else if (pending === 'continuing') {
    tail.push(<LiveStatusRow key="live" label="Resuming — the model continues from the latest frame" />)
  } else if (session?.state === 'running' || session?.state === 'created') {
    tail.push(<LiveStatusRow key="live" label={liveLabel(session)} />)
  }
  if (session?.state === 'paused' && session.used < session.budget) {
    tail.push(<PauseControlRow key="pause-control" busy={pending === 'continuing'} onContinue={handleContinue} />)
  }

  const navToggleLabel = navOpen ? 'Close navigation' : 'Open navigation'
  const asideToggleLabel = asideOpen ? 'Hide inspector' : 'Show inspector'
  const stageToggleLabel = stageOpen ? 'Collapse Live Preview stage' : 'Expand Live Preview stage'
  const chatPane = (
    <div className="gav-chat-pane" data-pane="chat">
      <FillColumn>
        <Chat label="Task conversation">
          <ChatThread
            label="Task conversation"
            empty={
              <EmptyState
                icon={<BrainIcon size={28} aria-hidden />}
                title="Start an exploration task"
                description="Choose the local volume file and the installed vision model with the buttons under the composer, describe what to look for, then send. Every screenshot in this conversation is a real capture from the native renderer; model claims stay unverified."
              />
            }
          >
            {rows}
            {tail}
          </ChatThread>
          <ChatScrollToBottom />
          <ChatComposer
            value={draft}
            onChange={setDraft}
            onSubmit={handleSend}
            ariaLabel="Describe the visualization task"
            placeholder="Describe what you want to see in the local volume"
            sendLabel="Start exploration"
            stopLabel="Stop"
            disabled={!editable}
            busy={runningish}
            onStop={stopAvailable ? handleStop : undefined}
            leading={
              editable ? (
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
              ) : undefined
            }
            endActions={
              editable ? (
                <Tooltip content="Change the installed vision model">
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => {
                      setModelDraft(model)
                      setModelOpen(true)
                    }}
                  >
                    <BotIcon aria-hidden />
                    {model}
                  </Button>
                </Tooltip>
              ) : undefined
            }
          />
        </Chat>
      </FillColumn>
    </div>
  )

  const stagePane = stageOpen ? (
    <div className="gav-stage-pane" data-pane="stage">
      <LiveStage
        source={source}
        previewImport={previewImport}
        onPreviewImport={setPreviewImport}
        onSourceChange={setSource}
      />
    </div>
  ) : null

  return (
    <>
      <ClippedNavShell
        className="gav-shell"
        navMode={navOpen ? 'drawer' : 'hidden'}
        onNavCrowded={() => setNavOpen(false)}
        topBar={
          <TopAppBar
            title="Agentic Volume Visualization"
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
          navOpen ? (
            <NavigationDrawer variant="standard" ariaLabel="Tasks">
              <NavigationDrawerNewChat label="New task" disabled={newTaskDisabled} onClick={handleNewTask} />
              {task ? (
                <NavigationDrawerItem icon={<FileIcon aria-hidden />} label={task.goal} active />
              ) : (
                <EmptyState
                  size="sm"
                  title="No task yet"
                  description="Choose the volume file and send the first task from the composer."
                />
              )}
            </NavigationDrawer>
          ) : null
        }
      >
        <div className="fynns-destination-app-shell-body">
          <div className="fynns-destination-app-shell-canvas fynns-scroll" ref={canvasRef}>
            {isNarrow ? (
              <div className="gav-workbench gav-workbench--narrow">
                {narrowView === 'chat' ? chatPane : stagePane ?? chatPane}
              </div>
            ) : stageOpen && stagePane ? (
              <SplitPane
                start={chatPane}
                end={stagePane}
                size={splitChatPx}
                onSizeChange={handleSplitSizeChange}
                minStart={chatMinPx}
                minEnd={stageMinPx}
                label="Resize Chat and Live Preview (40/60 default)"
              />
            ) : (
              <div className="gav-workbench">
                {chatPane}
              </div>
            )}
          </div>
          <EndAside open={asideOpen}>
            <PageScroll>
              {session ? (
                <div className="fynns-unit-stack">
                  <Card title="Session">
                    <List>
                      <ListItem headline="State" trailingSupportingText={stateLabel(session, pending)} />
                      <ListItem headline="Decisions" trailingSupportingText={`${session.used} / ${session.budget}`} />
                      <ListItem
                        headline="Scene revision"
                        trailingSupportingText={String(session.scene?.revision ?? 0)}
                      />
                      <ListItem headline="Volume source" supportingText={session.source} lines={2} />
                      <ListItem headline="Model" supportingText={session.model} />
                      <ListItem headline="Task" supportingText={session.goal} lines={3} />
                    </List>
                    <LinearProgress
                      value={session.budget > 0 ? session.used / session.budget : 0}
                      label={`Decision budget used: ${session.used} of ${session.budget}`}
                    />
                  </Card>
                  <Card title="Scene">
                    {session.scene ? (
                      <CodeBlock
                        label={`scene.json — revision ${session.scene.revision}`}
                        code={JSON.stringify(session.scene, null, 2)}
                      />
                    ) : (
                      <EmptyState
                        size="sm"
                        title="No scene yet"
                        description="The SceneSpec is recorded with the first render."
                      />
                    )}
                  </Card>
                  <Card title="Volume facts">
                    {session.volume ? (
                      <ExpandableCodeBlock
                        label="volume.json"
                        code={JSON.stringify(session.volume, null, 2)}
                        previewLines={14}
                      />
                    ) : (
                      <EmptyState
                        size="sm"
                        title="Volume not imported yet"
                        description="Numeric facts appear after the import step."
                      />
                    )}
                  </Card>
                </div>
              ) : (
                <EmptyState
                  size="sm"
                  title="No active task"
                  description="Session state, budget, the full SceneSpec and numeric volume facts appear here while a task runs."
                />
              )}
            </PageScroll>
          </EndAside>
        </div>
      </ClippedNavShell>

      <Dialog
        open={modelOpen}
        onOpenChange={setModelOpen}
        title="Vision model"
        description="Ollama model installed on this machine that reports vision capability. The app only talks to the local Ollama endpoint at 127.0.0.1:11434."
        size="md"
        feet={
          <div className="fynns-control-cluster fynns-control-cluster--end-align">
            <Button variant="ghost" onClick={() => setModelOpen(false)}>
              <CloseIcon aria-hidden />
              Cancel
            </Button>
            <Button
              variant="primary"
              disabled={!modelDraft.trim()}
              onClick={() => {
                setModel(modelDraft.trim())
                setModelOpen(false)
              }}
            >
              <CheckIcon aria-hidden />
              Apply
            </Button>
          </div>
        }
      >
        <FieldStack>
          <FieldBlock
            label="Ollama model"
            htmlFor="gav-vision-model"
            description="The model is checked against the local Ollama inventory before the first decision."
          >
            <Input
              id="gav-vision-model"
              value={modelDraft}
              onChange={(event) => setModelDraft(event.target.value)}
              placeholder={DEFAULT_MODEL}
              spellCheck={false}
              autoComplete="off"
            />
          </FieldBlock>
        </FieldStack>
      </Dialog>

      <Dialog
        open={zoomOpen}
        onOpenChange={setZoomOpen}
        title="Actual renderer frame"
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
