# AGENTS.md — app built on `@fynns/ui`

This app consumes the fynns design system from the sibling checkout
`../fynns_ui_design_core` (`@fynn7/ui-design-core`, `file:` link, Vite alias
`@fynns/ui`). Authority docs live **there**, not here:

| Need | Read (in `../fynns_ui_design_core`) |
| --- | --- |
| Install / greenfield skeleton / primitive-by-job table | `llm/CONSUME.md` |
| Props of a primitive | run `node ../fynns_ui_design_core/scripts/api.mjs <Name>` (`--list`, `--search <re>`, `--tokens <re>`) |
| A screen looks wrong (failure-mode index) | `llm/CONSUMER_TREATY.md` |
| Chat question / thinking variants / event order | `llm/CHAT_SEQUENCE.md`: `ChatQuestion` with Other/Input; `ChatThinking` status/disclosure in `ChatThinkingStack`; append one chronological list |
| Deleted APIs → replacements | `llm/BREAKING_PURGE.md` |
| Full design rules | `docs/DESIGN_SYSTEM.md` → one chapter at a time |

## Hard rules

Workspace/greenfield defaults are mandatory: read
`docs/design-system/workspace-chrome.md`, live `#layouts-demo-workspace-chrome`.
One TopAppBar ghost icon toggles the inspector in both states; no second close.
Global Home = top-bar HomeIcon, Back = ArrowLeftIcon, both ghost icon-only with
Tooltip/aria-label. First nav element = New resource menu, not a domain slogan.
Rename belongs to the targeted resource's context menu (right-click/Shift+F10),
never a canvas-toolbar rename Button. Routine inspector actions are one horizontal
ghost icon row (4dp); labeled/mixed rows use 8dp. At most one primary per group.
Dialog.feet: labeled ghost Cancel then primary Save. No all-cap UI chrome or
uppercase transforms. Unspecified error feedback defaults to error snackbar;
InlineAlert is second, for persistent recovery. Use its content-width centered
template and onDismiss/dismissAriaLabel ghost X; never embed labeled controls or
force full width. The installed export gate also checks workspace composition;
dynamic copy/props and cross-file behavior still require browser review.

Persistent in-flow details/actions use `SectionNotice` with string message,
optional string details and action descriptors; core owns disclosure and ghost
buttons. Never nest components or controls inside InlineAlert. Live `#section-notice`.
An InlineAlert recovery Button belongs in a sibling `.fynns-control-cluster`,
not the message. Preserve retry callbacks and disabled state; `onDismiss` is
only the X. Dialog Cancel explicitly uses ghost, Save stays primary. On gate
failure read the file/rule or JSON `issues[].code`: `workspace-contract` means
edit consumer JSX, never pull/skip-sync as its remedy. For simple legacy rows,
run `check-ui-exports.mjs --target <app> --fix` from core, review the diff, then
rerun the strict gate. Read-only checks never modify sources; complex cases
remain manual. Custom gate wrappers must preserve the actual diagnostic.

Canvas recipe: `#layouts-demo-canvas-workspace`. Use `.fynns-workspace-canvas`
and `.fynns-workspace-tools` with floating Toolbar, 16dp clearance, ghost X and
a top-bar restore icon. Place the canvas inside `.fynns-workspace-panes`
alongside EndAside, one full-height row directly
below TopAppBar. Only one floating editing toolbar per canvas. Document mode,
direction, arrange and export belong in one top-bar ghost icon DropdownMenu
(MoreHorizontalIcon + Tooltip/aria-label), never a second toolbar. No header
row may push the inspector below the canvas; opening menus preserves both bounds.
In-flow page tools use `.fynns-docked-tools-inset`;
never glue rounded docked bars to canvas/panes. Persistent actionable errors use
core `WorkspaceNoticeStack`: top/end 16dp, content width, 16dp stacking, stable
ids, independent ghost X/recovery and token-based entry/exit/reflow with reduced
motion. Never open another retry toolbar or reset business errors on dismiss.
Ordinary errors still prefer snackbar. Review clean design with
`llm/UI_REWARD_SIGNALS.md`: target 10/10 from screenshots and interaction evidence;
hard violations invalidate acceptance. Never award points for hiding useful UI.

Body/captions: `--fynns-line-height-body` (1.45). Media streams use
`.fynns-content-flow` → `.fynns-media-gallery` → `figure.fynns-media-figure`.
Core owns 8dp media→caption and 16dp gallery/block gaps. Never override these
locally or compress prose with tight/snug/compact. See
`docs/design-system/content-rhythm.md`; verify narrow widths and long captions.

1. Build UI only from `@fynns/ui` exports; style only with `var(--fynns-*)`.
   Never restyle `.fynns-*`, never hardcode hex / px, never add `@radix-ui/*`,
   `sonner`, native `<select>` / `<dialog>` / `alert()`.
2. Default chrome `DestinationAppShell` → main `PageScroll` → `Card` / `List`
   / `EmptyState`. Modal = `Dialog`; feedback = `snackbar` + `<SnackbarHost />`;
   app startup / reopening = `AppLoadingScreen` (opaque theme background,
   centered logo sweep; configured project icon → initials Avatar → core mark).
   Icon discovery is automatic; pass existing JS brand assets through logoSrc.
   Never show whole-window text skeletons or translucent app chrome at startup.
   Content loading = large content-position BusyRegion skeletons; BusyScrim
   only for explicit in-app blocking content waits,
   `LoadingSkeleton` slots matching the upcoming UI/text. Default = the large,
   full-width thick text skeleton; six rows without fill, adaptive rows with
   fill and omitted lines. Whole-pane waits must not freeze six rows or clear
   padding: core owns the bare cold-start dialog-inset and retains canonical
   FillColumn / PageScroll / ChatThread insets. ChatThread.empty uses BusyRegion
   busy fill; avoid content-sized wrappers. Explicit block = image / iframe / canvas.
   Labels are accessible-only; no visible Loading copy, compact busy marks or
   small centered text emblems. Keep the large text skeleton.
   Actions prefer disabled + aria-busy with the content skeleton owning the wait.
   Rings / Button loading / explicit busy message+circular are permanently
   archived, deprecated and strongly discouraged. Real value = linear progress.
   See `docs/design-system/loading.md`; no local shimmer CSS.
   Chat = FillColumn → Chat; core owns main top + inline `dialog-inset`.
   Never reset `.fynns-fill-column-main`. Transient request errors use
   `snackbar(message, { severity: "error", duration: "long", dismissible: true })`.
   Default snackbar = theme surface, no icon; four severity variants match
   InlineAlert's rendered tones on app-bg as flat fills (12% semantic color,
   88% app canvas), on-surface text and matching icons. No frosted glass,
   backdrop blur or see-through content; never change InlineAlert.
   `icon: true` enables an icon on the default;
   `false` / `null` hides one. Persistent notices/recovery remain InlineAlert.
   SnackbarHost always portals a fixed overlay. Do not add an in-flow host,
   footer spacer or conditional app padding: showing/replacing/dismissing
   feedback must leave shell, Chat/composer, nav and preview bounds and thread
   scroll unchanged. Use core flat tonal fills rather than moving the layout.
3. Missing capability → say so and stop; it must land in `fynns_ui_design_core`
   first (never edit `node_modules/@fynn7/ui-design-core`).
4. Keep `npm run build` green; run `node ../fynns_ui_design_core/scripts/install-as-npm.mjs --target . --check` when imports fail.
5. Consumer app action Buttons default to a semantic leading icon plus visible
   text (`CloseIcon` + Cancel, `CheckIcon` + Apply, `RefreshIcon` + Refresh).
   Put both directly inside `Button`, set the icon `aria-hidden`, and never add
   local icon margin / gap / literal spaces; core owns the spacing. Use
   `IconButton` + Tooltip + `aria-label` only for compact self-evident chrome.
   Card/Collapsible header buttons (including InfoHint/menu triggers) use
   `size="sm"` (32dp); core clamps wrapped header buttons to this size.
   Status details use `InfoHint icon={<CheckIcon aria-hidden />}` for a
   completed/available result; eyes mean view/reveal only. Keep the shared
   16dp glyph, circular hover target and keyboard Tooltip; no tiny span/SVG clones.
   Action groups use Toolbar or `.fynns-control-cluster`: any labeled Button
   or Menu makes the entire row 8dp (including wrapped triggers); icon-only
   rows are 4dp. No private tight gaps or per-button margins. Outer Toolbar
   gap does not reach its action row. SplitButton stays joined internally.
   Drawer body sections use NavigationDrawerHeadline / Group, with 24dp first
   title edge clearance and 16dp internal Divider→title. Preserve core padding;
   omit redundant DestinationAppShell drawerHeadline. See form-rhythm.md.
6. Every ellipsized or line-clamped UI label must reveal its exact full text in
   a hover/focus Tooltip. Prefer string props that core wraps automatically;
   wrap custom `ReactNode` text with `OverflowTip` at the call site. This also
   applies in intentionally narrow drawers/sidebars. Never use native `title=`
   or hand-author `.fynns-control-row__label-text`; pass a string to
   `ControlRow label` whenever possible.
7. Before shipping dense rows, inspect their rendered geometry at the narrowest
   supported viewport and with the longest localized labels. Text, InfoHint
   target, Switch, panel edge, and adjacent rows must not overlap. Preference
   rows use `ControlStack columns={1}`: text + `InfoHint size="sm"` in
   `ControlRow label`, Switch alone in children. Allow core label wrapping and
   choose a fitting Dialog `size` (usually `md` for several long rows). Never
   hide a collision with clipping, absolute offsets, or local `.fynns-*` CSS.
8. ChatComposer buttons, menu triggers, mode toggles and Send/Stop always share
   **one horizontal action row**. Prevent pane/window compression below the
   complete row's minimum (controls + menu floors + gaps + shell/form/host
   insets); `--fynns-layout-chat-min-width` is only the baseline. Clamp drag,
   keyboard and restored pane sizes; enforce native window minima. In browsers,
   collapse nav/preview into core overlay/sheet before crowding chat. Never wrap,
   stack, clip or horizontally scroll actions. Read `llm/CHAT_COMPOSER_LAYOUT.md`.

## Token discipline (local models)

Chat tool summaries (`ChatActivity`) and reasoning (`ChatThinking`) keep the
chevron a shared short gap after the label text, independent of body width or
expansion. Do not stretch header/label or override core geometry; see
`llm/CHAT_SEQUENCE.md`, sandbox `#chat-chevron`.

- Look props up with `api.mjs`; do **not** Read files under
  `../fynns_ui_design_core/src` or `examples/sandbox` whole (`GlobalsPage.tsx`
  is 300 KB). If you must, Read ≤ 120 lines with an offset.
- One step per turn: read → edit → `npm run build` (or `npx tsc --noEmit`).
- Grep only inside this app or `../fynns_ui_design_core/src/primitives` with a
  `--glob`; take the first few hits.
