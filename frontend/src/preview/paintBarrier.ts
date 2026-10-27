/**
 * Paint barrier for the synchronous Raycaster engine (mirrors `gsc-live-preview`).
 *
 * `Module.ccall` into the preview engine blocks the whole thread that runs it.
 * `wasmShellUrl()` puts the shell in a twin-origin iframe so that thread is
 * normally not this one, but the fallbacks (non-loopback host, a browser
 * without site isolation, `about:blank` boot frames) still share this process.
 * Committing the busy state and waiting for a real paint first means the label
 * and the spinner are on screen before the block, instead of the UI appearing
 * frozen with stale content.
 *
 * Order matters: `flushSync` is illegal inside a React lifecycle, so yield to a
 * normal task first (same sequence as gsc-live-preview's `useGscRunner`).
 */
import { flushSync } from 'react-dom'
import { afterNextPaint, yieldToMain } from '@fynns/ui'

/** Apply one React commit, then resolve after the browser has painted it. */
export async function commitThenPaint(commit: () => void): Promise<void> {
  await yieldToMain()
  flushSync(commit)
  await afterNextPaint()
}
