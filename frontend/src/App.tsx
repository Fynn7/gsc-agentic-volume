import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react'
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
  TopAppBar,
  Tooltip,
  snackbar,
} from '@fynns/ui'
import {
  continueSession,
  errorText,
  fetchSession,
  startSession,
  stopSession,
  type SessionData,
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

const DEFAULT_MODEL = 'qwen3.5:9b'
const POLL_INTERVAL_MS = 1500

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

function App() {
  const [navOpen, setNavOpen] = useState(true)
  const [asideOpen, setAsideOpen] = useState(false)

  const [source, setSource] = useState('')
  const [model, setModel] = useState(DEFAULT_MODEL)
  const [draft, setDraft] = useState('')

  const [sourceOpen, setSourceOpen] = useState(false)
  const [sourceDraft, setSourceDraft] = useState('')
  const [modelOpen, setModelOpen] = useState(false)
  const [modelDraft, setModelDraft] = useState('')

  const [sessionId, setSessionId] = useState<string | null>(null)
  const [session, setSession] = useState<SessionData | null>(null)
  const [task, setTask] = useState<TaskSnapshot | null>(null)
  const [pending, setPending] = useState<Pending>(null)

  const [zoom, setZoom] = useState<ZoomPayload | null>(null)
  const [zoomOpen, setZoomOpen] = useState(false)
  const pollFailed = useRef(false)

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

  const handleSend = useCallback(
    (value: string) => {
      const goal = value.trim()
      if (!goal) return
      const cleanSource = source.trim()
      const cleanModel = model.trim()
      if (!cleanSource) {
        setSourceDraft(source)
        setSourceOpen(true)
        snackbar('Set the local volume header path before starting.', {
          severity: 'error',
          duration: 'long',
          dismissible: true,
        })
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
      void startSession({ source: cleanSource, model: cleanModel, goal })
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
    [source, model],
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
                  description="Set the volume and send the first task from the composer."
                />
              )}
            </NavigationDrawer>
          ) : null
        }
      >
        <div className="fynns-destination-app-shell-body">
          <div className="fynns-destination-app-shell-canvas fynns-scroll">
            <FillColumn>
              <Chat label="Task conversation">
                <ChatThread
                  label="Task conversation"
                  empty={
                    <EmptyState
                      icon={<BrainIcon size={28} aria-hidden />}
                      title="Start an exploration task"
                      description="Set the local volume header path and the installed vision model with the buttons under the composer, describe what to look for, then send. Every screenshot in this conversation is a real capture from the native renderer; model claims stay unverified."
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
                      <Tooltip content="Set the local volume header path">
                        <IconButton
                          variant="ghost"
                          size="sm"
                          aria-label="Set the local volume header path"
                          onClick={() => {
                            setSourceDraft(source)
                            setSourceOpen(true)
                          }}
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
        open={sourceOpen}
        onOpenChange={setSourceOpen}
        title="Volume source"
        description="Absolute path to the local volume header on this machine. The file is read locally by the import step and never uploaded."
        size="md"
        feet={
          <div className="fynns-control-cluster fynns-control-cluster--end-align">
            <Button variant="ghost" onClick={() => setSourceOpen(false)}>
              <CloseIcon aria-hidden />
              Cancel
            </Button>
            <Button
              variant="primary"
              disabled={!sourceDraft.trim()}
              onClick={() => {
                setSource(sourceDraft.trim())
                setSourceOpen(false)
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
            label="Local volume header path"
            htmlFor="gav-volume-source"
            description="For example D:\data\volume.mhd or D:\data\volume.dat."
          >
            <Input
              id="gav-volume-source"
              value={sourceDraft}
              onChange={(event) => setSourceDraft(event.target.value)}
              placeholder="D:\data\volume.mhd"
              spellCheck={false}
              autoComplete="off"
            />
          </FieldBlock>
        </FieldStack>
      </Dialog>

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
