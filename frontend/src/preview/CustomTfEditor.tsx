import { useEffect, useMemo, useRef, useState } from 'react'
import { Button, FieldHint, FieldStack, Tabs } from '@fynns/ui'
import { decodeCustomTf, defaultCustomChannels, encodeCustomTf, smoothstepChannel } from './tfCodec'

type ChannelKey = 'r' | 'g' | 'b' | 'a'

const CHANNELS: ChannelKey[] = ['r', 'g', 'b', 'a']

export function CustomTfEditor({
  encoding,
  tfStart,
  tfWidth,
  onChange,
}: {
  encoding: string
  tfStart: number
  tfWidth: number
  onChange: (encoding: string) => void
}) {
  const [channel, setChannel] = useState<ChannelKey>('a')
  const canvasRef = useRef<HTMLCanvasElement | null>(null)
  const draggingRef = useRef(false)

  const channels = useMemo(() => {
    if (!encoding) return defaultCustomChannels(256)
    try {
      const decoded = decodeCustomTf(encoding)
      // Normalize to 256 points for editing.
      if (decoded.r.length === 256) return decoded
      return defaultCustomChannels(256)
    } catch {
      return defaultCustomChannels(256)
    }
  }, [encoding])

  const [local, setLocal] = useState(channels)
  useEffect(() => {
    setLocal(channels)
  }, [channels])

  /** Resolve a core token at draw time so the editor follows the active theme. */
  const resolveToken = (canvas: HTMLCanvasElement, name: string, fallback: string) => {
    try {
      const v = getComputedStyle(canvas).getPropertyValue(name).trim()
      return v || fallback
    } catch {
      return fallback
    }
  }

  const draw = () => {
    const canvas = canvasRef.current
    if (!canvas) return
    const ctx = canvas.getContext('2d')
    if (!ctx) return
    const w = canvas.width
    const h = canvas.height
    // Transparent well: the hosting Card surface shows through in both themes.
    ctx.clearRect(0, 0, w, h)
    const frameStroke = resolveToken(canvas, '--fynns-color-border-strong', 'gray')
    const curveStroke = resolveToken(canvas, '--fynns-color-accent', 'gray')
    ctx.strokeStyle = frameStroke
    ctx.lineWidth = 1
    ctx.strokeRect(0.5, 0.5, w - 1, h - 1)
    // Grid
    ctx.save()
    ctx.globalAlpha = 0.5
    ctx.strokeStyle = frameStroke
    ctx.beginPath()
    for (let i = 1; i < 4; i++) {
      ctx.moveTo((w * i) / 4, 0)
      ctx.lineTo((w * i) / 4, h)
    }
    ctx.stroke()
    ctx.restore()
    const values = local[channel]
    ctx.strokeStyle = curveStroke
    ctx.lineWidth = 2
    ctx.beginPath()
    for (let i = 0; i < values.length; i++) {
      const x = (i / (values.length - 1)) * w
      const y = h - values[i] * h
      if (i === 0) ctx.moveTo(x, y)
      else ctx.lineTo(x, y)
    }
    ctx.stroke()
  }

  useEffect(() => {
    draw()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [local, channel])

  const localRef = useRef(local)
  localRef.current = local
  const lastYRef = useRef(0)

  const setFromPointer = (clientX: number) => {
    const canvas = canvasRef.current
    if (!canvas) return
    const rect = canvas.getBoundingClientRect()
    const fx = (clientX - rect.left) / rect.width
    const idx = Math.max(0, Math.min(255, Math.round(fx * 255)))
    // Vertical position from event is handled by pointermove Y.
    const fy = (lastYRef.current - rect.top) / rect.height
    const value = Math.max(0, Math.min(1, 1 - fy))
    // Compute from the latest committed state (not inside the updater, so the
    // encode side-effect runs exactly once per gesture event).
    const base = localRef.current
    const curve = [...base[channel]]
    // Smooth a small neighbourhood so single-pixel drags feel continuous.
    for (let d = -2; d <= 2; d++) {
      const j = idx + d
      if (j >= 0 && j < 256) curve[j] = value
    }
    const next = { ...base, [channel]: curve }
    setLocal(next)
    try {
      onChange(encodeCustomTf(next))
    } catch {
      /* keep editing */
    }
  }

  const handlePointerDown = (e: React.PointerEvent<HTMLCanvasElement>) => {
    draggingRef.current = true
    lastYRef.current = e.clientY
    e.currentTarget.setPointerCapture(e.pointerId)
    setFromPointer(e.clientX)
  }

  const handlePointerMove = (e: React.PointerEvent<HTMLCanvasElement>) => {
    if (!draggingRef.current) return
    lastYRef.current = e.clientY
    setFromPointer(e.clientX)
  }

  const handlePointerUp = () => {
    draggingRef.current = false
  }

  const applySmoothstep = () => {
    const curve = smoothstepChannel(256, tfStart, tfWidth)
    const next = { ...local, [channel]: curve }
    setLocal(next)
    try {
      onChange(encodeCustomTf(next))
    } catch {
      /* ignore */
    }
  }

  const resetAll = () => {
    const next = defaultCustomChannels(256)
    setLocal(next)
    try {
      onChange(encodeCustomTf(next))
    } catch {
      /* ignore */
    }
  }

  return (
    <FieldStack>
      <Tabs
        tabs={CHANNELS.map((c) => ({ id: c, label: c.toUpperCase() }))}
        activeId={channel}
        onChange={setChannel}
        size="sm"
        ariaLabel="Transfer function channel"
      />
      <canvas
        ref={canvasRef}
        width={256}
        height={120}
        className="gav-tf-canvas"
        aria-label={`Custom transfer function curve for channel ${channel.toUpperCase()}. Drag to edit.`}
        onPointerDown={handlePointerDown}
        onPointerMove={handlePointerMove}
        onPointerUp={handlePointerUp}
      />
      <div className="fynns-control-cluster fynns-control-cluster--start-align">
        <Button variant="ghost" size="sm" onClick={applySmoothstep}>
          Smoothstep preset
        </Button>
        <Button variant="ghost" size="sm" onClick={resetAll}>
          Reset all
        </Button>
      </div>
      <FieldHint>Left-drag edits the active channel. Applied live to the Wasm preview.</FieldHint>
    </FieldStack>
  )
}
