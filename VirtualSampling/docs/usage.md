# Usage and Controls

This guide summarizes how to interact with Raycaster in the browser and in the
native desktop build.

## Browser entry point

The easiest way to use Raycaster is the hosted web version:

[cgvis.de/vs](https://www.cgvis.de/vs)

The web page exposes the same core rendering configuration as the native
application and encodes the current state directly in the URL for easy sharing.

## Web interface overview

The browser UI combines a rendering canvas with a control panel.

Typical controls include:

- dataset selection
- rendering method selection
- sampling rate
- virtual sample count
- constant sample count
- transfer-function type
- transfer-function start and width
- orthographic projection
- early ray termination

The web UI is especially useful for sharing exact configurations because the
current scene is serialized into the query string. That includes dataset, method,
sampling parameters, transfer-function parameters, camera transform, and
background settings.

## Mouse interaction

### Standard mode

- Left drag rotates the volume around its center.
- Right drag adjusts the transfer function interactively.

### Transfer-function editor mode

- Left drag edits the active transfer-function channel directly.

## Native keyboard shortcuts

The following shortcuts are implemented in the current native application.

### Method and dataset navigation

- `0-9`, then `Enter`
  Jump directly to a rendering method by index.
- `V`
  Advance to the next dataset.
- `Shift+V`
  Move to the previous dataset.
- `N`
  Move to the previous rendering mode.
- `M`
  Move to the next rendering mode.
- `P`
  Toggle back to the previously used rendering mode.

### Sampling and filtering

- `C`
  Toggle constant sample count.
- `W`
  Increase sampling rate.
- `Q`
  Decrease sampling rate.
- `Left`
  Decrease the number of virtual samples.
- `Right`
  Increase the number of virtual samples.
- `F`
  Toggle nearest-neighbor filtering.
- `E`
  Toggle the alpha threshold used for early ray termination.
- `J`
  Trigger prefilter computation.

### Camera and view

- `R`
  Reset the view and rendering parameters.
- `Up`
  Zoom in.
- `Down`
  Zoom out.
- `Shift+Left`
  Pan left.
- `Shift+Right`
  Pan right.
- `Shift+Up`
  Pan up.
- `Shift+Down`
  Pan down.
- `O`
  Toggle orthographic projection.

## Transfer-function editing

- `T`
  Toggle transfer-function editor mode.
- `A`
  Switch the active transfer-function channel.
- `G`
  Toggle between Smoothstep and Gaussian transfer-function behavior.

In standard mode, right mouse drag adjusts the currently active built-in transfer
function. In editor mode, left mouse drag edits the transfer-function curve
directly.

## Resolution and clipping

- `D`
  Move to the next coarser dataset level.
- `Shift+D`
  Move back to the next finer dataset level.
- `F1` / `Shift+F1`
  Shrink or expand clipping in `x`.
- `F2` / `Shift+F2`
  Shrink or expand clipping in `y`.
- `F3` / `Shift+F3`
  Shrink or expand clipping in `z`.
- `F4` / `Shift+F4`
  Shift the clipping window in `x`.
- `F5` / `Shift+F5`
  Shift the clipping window in `y`.
- `F6` / `Shift+F6`
  Shift the clipping window in `z`.

## Reproducibility helpers

- `U`
  Print the current URL-style scene encoding to the console.
- `Esc`
  Close the native application window.

The printed scene encoding can be pasted back into the hosted web interface or
stored in scripts via `settransformparams`, `settfcode`, and related commands.

## Relationship between web and native usage

The browser UI and native application expose the same main configuration space:

- dataset and method selection
- sampling rate and virtual samples
- transfer-function settings
- camera transforms
- downsampling level

That makes it practical to explore a setup in the browser, copy the resulting
URL parameters, and then use the same configuration in native benchmarking
scripts.
