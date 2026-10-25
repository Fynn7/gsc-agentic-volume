import { ClipboardIcon, CodeBlock, Dialog, FieldBlock, IconButton, Tooltip, snackbar } from '@fynns/ui'

/**
 * GSC language reference shown from the `GSC Code Editor` header.
 *
 * Content is extracted from the GSC Live Preview system prompt
 * (`llmPrompt.ts` + `gscCommandSignatures.ts`: output contract, command
 * reference, TF / method codebook, pose rules) and checked against this
 * repo's interpreter (`VirtualSampling/Utils/CommandInterpreter.cpp`,
 * `GLApp.cpp`, `Raycaster/RaycasterScript.inc`) and `docs/scripting.md`.
 */

const GSC_COMMANDS = `reset
setinteraction <bool>
setbackground <num> <num> <num> <num>
resize <int> <int>
screenshot [str]
setfpswindow <num>
clearlog
logfile <str>
logtime
logfps
logGLInfo <bool>
log ...
setdir <str>
quit
setvolume <int>
loadvolume <str>
setmethod <int>
setrate <num>
setsubdiv <int>
constantSampleCount <bool>
setLevel <int>
settfparams <bool> <num> <num>
settffile <str>
settfcode <str>
setalphathreshold <num>
setusennfilter <bool>
setuseortho <bool>
resetrotation
resetclipbounds
setclipbounds <num x6>
setrotation <num x16>
addrotationx <num>
addrotationy <num>
addrotationz <num>
settranslation <num> <num> <num>
settransformparams <str>
prefilter
dumpstate <str>
noop <ms>
set <name> <value...> / unset <name>
repeat <n> [as $i] ... endrepeat
if <cond> ... [else ...] endif`

const GSC_EXAMPLE = `# Smoothstep band, yaw 35° from the initial pose
setinteraction true
resize 512 512
setvolume 7
setmethod 0
settfparams false 0.1 0.12
resetrotation
addrotationx -20
addrotationy 35`

/**
 * The full instruction above, as Markdown source — the copy button in the
 * dialog head writes exactly this string to the clipboard.
 */
export const GSC_REFERENCE_MARKDOWN = `# GSC language reference

Graphics Script (.gsc) for the VirtualSampling Raycaster — one command per line, executed by the preview shell when you press Run.

## Script basics

A script is plain text processed line by line. Commands are whitespace-separated tokens; blank lines separate execution batches. \`#\` starts a comment, command names are case-sensitive, and there is no quoting mechanism for strings with spaces.

## Variables and control flow

\`set <name> <value>\` stores a string variable referenced as \`$name\` or \`\${name}\`; \`unset\` removes it. \`set\` accepts a simple integer expression with \`+ - * /\` (no parentheses). \`noop <ms>\` pauses execution, \`repeat <n> [as $i] … endrepeat\` loops, and \`if … [else …] endif\` branches on a truthy variable or a \`== != < <= > >=\` comparison.

## Command reference

\`<>\` required, \`[]\` optional, \`...\` extra tokens. Argument kinds: \`bool\` is \`true/false\`, \`int\` an integer, \`num\` a float, \`str\` a spaceless string.

\`\`\`gsc
${GSC_COMMANDS}
\`\`\`

## Render method and transfer function

\`setmethod <int>\` selects the render mode — landmark indices: 0 Linear, 13 Quadratic B-Spline, 39 Cubic B-Spline, 52 Cubic B-Spline prefiltered, 65 Catmull-Rom (see the Method dropdown for the full list). \`settfparams <bool> <center> <width>\`: the first argument selects the shape (\`true\` = Gaussian peak for a thin high-density structure, \`false\` = Smoothstep range for broader visibility); center and width are normalized to [0,1], smaller width is sharper / more selective.

## Pose

After \`setvolume\` rotation starts at identity; \`resetrotation\` returns to that pose. \`addrotationx/y/z\` take degrees and compose in the order written. An explicit degree rotate is \`resetrotation\` followed by only that axis line; named views reset first, then follow the volume's orientation notes.

## Running in this editor

Run (Ctrl/Cmd+Enter) resets the renderer, restores the current dataset, executes the script, then reads the panel state back from Wasm. GUI edits update the marked generated zone once the operation settles; hand-written lines outside the markers stay untouched. File-output and termination commands (\`setdir\`, \`screenshot\`, \`quit\`) belong to native batch runs.

\`\`\`gsc
${GSC_EXAMPLE}
\`\`\`
`

export function GscReferenceDialog({
  open,
  onOpenChange,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
}) {
  const handleCopyMarkdown = () => {
    void navigator.clipboard
      .writeText(GSC_REFERENCE_MARKDOWN)
      .then(() => {
        snackbar('GSC reference copied as Markdown.', { severity: 'info', duration: 'long', dismissible: true })
      })
      .catch(() => {
        snackbar('Copy failed: clipboard unavailable.', { severity: 'error', duration: 'long', dismissible: true })
      })
  }
  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      title="GSC language reference"
      description="Graphics Script (.gsc) for the VirtualSampling Raycaster — one command per line, executed by the preview shell when you press Run."
      size="lg"
      showCloseButton
      closeAriaLabel="Close GSC language reference"
      headActions={
        <Tooltip content="Copy as Markdown">
          <IconButton
            variant="ghost"
            size="sm"
            aria-label="Copy GSC reference as Markdown"
            onClick={handleCopyMarkdown}
          >
            <ClipboardIcon aria-hidden />
          </IconButton>
        </Tooltip>
      }
    >
      <FieldBlock label="Script basics">
        <p>
          A script is plain text processed line by line. Commands are whitespace-separated tokens; blank lines
          separate execution batches. <code>#</code> starts a comment, command names are case-sensitive, and there
          is no quoting mechanism for strings with spaces.
        </p>
      </FieldBlock>
      <FieldBlock label="Variables and control flow">
        <p>
          <code>set &lt;name&gt; &lt;value&gt;</code> stores a string variable referenced as <code>$name</code> or{' '}
          <code>${'{'}name{'}'}</code>; <code>unset</code> removes it. <code>set</code> accepts a simple integer
          expression with <code>+ - * /</code> (no parentheses). <code>noop &lt;ms&gt;</code> pauses execution,{' '}
          <code>repeat &lt;n&gt; [as $i] … endrepeat</code> loops, and <code>if … [else …] endif</code> branches on a
          truthy variable or a <code>== != &lt; &lt;= &gt; &gt;=</code> comparison.
        </p>
      </FieldBlock>
      <FieldBlock label="Command reference">
        <p>
          <code>&lt;&gt;</code> required, <code>[]</code> optional, <code>...</code> extra tokens. Argument kinds:{' '}
          <code>bool</code> is <code>true/false</code>, <code>int</code> an integer, <code>num</code> a float,{' '}
          <code>str</code> a spaceless string.
        </p>
        <CodeBlock variant="plain" code={GSC_COMMANDS} />
      </FieldBlock>
      <FieldBlock label="Render method and transfer function">
        <p>
          <code>setmethod &lt;int&gt;</code> selects the render mode — landmark indices: 0 Linear, 13 Quadratic
          B-Spline, 39 Cubic B-Spline, 52 Cubic B-Spline prefiltered, 65 Catmull-Rom (see the Method dropdown for the
          full list). <code>settfparams &lt;bool&gt; &lt;center&gt; &lt;width&gt;</code>: the first argument selects
          the shape (<code>true</code> = Gaussian peak for a thin high-density structure, <code>false</code> =
          Smoothstep range for broader visibility); center and width are normalized to [0,1], smaller width is
          sharper / more selective.
        </p>
      </FieldBlock>
      <FieldBlock label="Pose">
        <p>
          After <code>setvolume</code> rotation starts at identity; <code>resetrotation</code> returns to that pose.{' '}
          <code>addrotationx/y/z</code> take degrees and compose in the order written. An explicit degree rotate is{' '}
          <code>resetrotation</code> followed by only that axis line; named views reset first, then follow the
          volume&rsquo;s orientation notes.
        </p>
      </FieldBlock>
      <FieldBlock label="Running in this editor">
        <p>
          Run (Ctrl/Cmd+Enter) resets the renderer, restores the current dataset, executes the script, then reads
          the panel state back from Wasm. GUI edits update the marked generated zone once the operation settles;
          hand-written lines outside the markers stay untouched. File-output and termination commands (
          <code>setdir</code>, <code>screenshot</code>, <code>quit</code>) belong to native batch runs.
        </p>
        <CodeBlock variant="plain" code={GSC_EXAMPLE} />
      </FieldBlock>
    </Dialog>
  )
}
