"""Plain-language wording for the conversation and the event log.

The Live timeline is read by people who never saw this pipeline, so every row
has to say what happened and with which concrete numbers. The rules stay short
on purpose: one fact per line, no paragraphs, no jargon that is not explained
in the same line.

Pure functions only - no files, no state - so the wording can be checked
without a run, a model or a renderer.
"""
from __future__ import annotations

# The marker in front of every word the model itself wrote. The screen
# never writes such words, so the reader always knows whose voice it is.
MODEL_MARK = "\u00bb"

# What each renderer tool does, in words a newcomer can act on.
TOOL_MEANING = {
    "inspect_scene": "read the current scene, its numbers and its PNG (changes nothing)",
    "render_scene": "render a new frame from the volume",
    "inspect_slice": "read one raw voxel slice (changes nothing)",
    "finish_task": "save an already rendered frame as the final result",
}

# The step, in three words. No parentheses, no explanation: the facts that
# follow the verb on the row say what the call does.
TOOL_ACTION = {
    "inspect_scene": "Read the scene",
    "render_scene": "Render a picture",
    "inspect_slice": "Read one slice",
    "finish_task": "Save the result",
}
DEFAULT_ACTION = "Call the renderer"

# What the call hands back.
TOOL_OUTPUT = {
    "inspect_scene": "camera, opacity window, crop box, current picture",
    "render_scene": "a new PNG of the volume",
    "inspect_slice": "a grayscale PNG of one voxel layer",
    "finish_task": "the chosen frame, as the result",
}

# What the call changes.
TOOL_EFFECT = {
    "inspect_scene": "nothing changes",
    "render_scene": "the scene changes",
    "inspect_slice": "nothing changes",
    "finish_task": "nothing changes",
}

# Parameter name -> short human label, in the order they are shown.
PARAM_MEANING = (
    ("rotationXYZ", "camera rotation"),
    ("translation", "camera shift"),
    ("tfStart", "opacity start"),
    ("tfWidth", "opacity width"),
    ("crop", "crop"),
)
_PARAM_KEYS = tuple(key for key, _label in PARAM_MEANING)


def brief(text, limit=200):
    """One line, capped, with an ellipsis when something was cut."""
    text = " ".join(str(text if text is not None else "").split())
    if len(text) <= limit:
        return text
    return text[:max(1, limit - 3)].rstrip() + "..."


def number(value):
    """Compact number: 148491.0 -> '148491', 0.1 -> '0.1'."""
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, float):
        if value == int(value):
            return str(int(value))
        return ("%.4f" % value).rstrip("0").rstrip(".")
    return str(value)


def count(value):
    """A token counter that never prints 'None' for a missing number."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return number(value)
    return str(value) if value is not None else "?"


def vector(value):
    """'30,0,0' for a camera vector, '0.1' for a scalar."""
    if isinstance(value, (list, tuple)):
        return ",".join(number(item) for item in value)
    return number(value)


def tail_path(path, limit=110):
    """Path with the head cut when it is too long - the tail names the file."""
    text = str(path or "")
    if len(text) <= limit:
        return text
    parts = text.replace("/", "\\").split("\\")
    return "..." + "\\".join(parts[-3:])


def parameter_lines(parameters):
    """'camera rotation 30,0,0' lines for the parameters a call changes."""
    lines = []
    if not isinstance(parameters, dict):
        return lines
    for key, label in PARAM_MEANING:
        if key not in parameters:
            continue
        value = parameters[key]
        if key == "crop" and isinstance(value, (list, tuple)) and len(value) == 6:
            text = "crop x %s-%s y %s-%s z %s-%s" % tuple(number(v) for v in value)
        else:
            text = "%s %s" % (label, vector(value))
        lines.append(text)
    for key in parameters:
        if key not in _PARAM_KEYS:
            lines.append("%s %s" % (key, brief(parameters[key], 40)))
    return lines


def tool_action(name, arguments=None):
    """The step, in three words: what the agent is doing right now."""
    return TOOL_ACTION.get(str(name or ""), DEFAULT_ACTION)


def tool_input(name, arguments=None):
    """What goes into the call: the numbers it changes, or its target."""
    arguments = arguments if isinstance(arguments, dict) else {}
    name = str(name or "")
    if name == "inspect_slice":
        return ["axis %s, index %s" % (arguments.get("axis", "?"),
                                       arguments.get("index", "?"))]
    if name == "finish_task":
        return ["frame revision %s" % arguments.get("frameRevision", "?")]
    return parameter_lines(arguments.get("parameters"))


def tool_facts(name, arguments=None):
    """Input, output and effect of one call, as one line of facts."""
    arguments = arguments if isinstance(arguments, dict) else {}
    facts = []
    numbers = tool_input(name, arguments)
    if numbers:
        facts.append(", ".join(numbers[:2]))
    output = TOOL_OUTPUT.get(str(name or ""))
    if output:
        facts.append(output)
    effect = TOOL_EFFECT.get(str(name or ""))
    if effect:
        facts.append(effect)
    return " · ".join(facts)


def effect_lines(parameters):
    """What the changed numbers mean, in words.

    The numbers alone ("camera rotation 30,45,0") only mean something to
    someone who knows the renderer. Each line says what the number does.
    """
    if not isinstance(parameters, dict):
        return []
    lines = []
    rotation = parameters.get("rotationXYZ")
    if isinstance(rotation, (list, tuple)) and len(rotation) == 3:
        lines.append("the camera turns %s° around X, then %s° around Y, "
                     "then %s° around Z" % tuple(number(v) for v in rotation))
    shift = parameters.get("translation")
    if isinstance(shift, (list, tuple)) and len(shift) == 3:
        lines.append("the camera slides by %s,%s,%s" % tuple(number(v) for v in shift))
    start = parameters.get("tfStart")
    width = parameters.get("tfWidth")
    if start is not None:
        top = start
        if width is not None:
            try:
                top = start + width
            except TypeError:
                top = width
        lines.append("density below %s stays invisible, above %s is fully bright"
                     % (number(start), number(top)))
    crop = parameters.get("crop")
    if isinstance(crop, (list, tuple)) and len(crop) == 6:
        lines.append("only the voxels x %s-%s, y %s-%s, z %s-%s are shown"
                     % tuple(number(v) for v in crop))
    return lines


def notices_label(count):
    """How many harness notices there are (without the key hint)."""
    count = int(count or 0)
    if count <= 1:
        return "1 harness notice"
    return "%d harness notices" % count


def notice_headline(count):
    """One line for the harness notices (they do not stop the run)."""
    count = int(count or 0)
    hint = "Ctrl+H reads it" if count <= 1 else "Ctrl+H reads them"
    return "%s · %s" % (notices_label(count), hint)


# The words the screen uses, explained for someone who never saw this system.
GLOSSARY = (
    ("observation", "one look at the volume: a rendered picture or a raw slice. "
                    "Every observation costs one step of the budget."),
    ("budget", "how many looks the agent may take. The top line shows how many "
               "are already used."),
    ("frame", "one picture of the volume that the renderer wrote to disk."),
    ("revision", "the version number of the scene. Every change makes a new one."),
    ("lit px", "pixels of the picture that are not black - how much of the "
               "volume is visible at all."),
    ("camera rotation", "the direction the camera looks from, in degrees around "
                        "X, then Y, then Z."),
    ("camera shift", "where the camera sits, relative to the volume."),
    ("opacity start / width", "the density window: below the start nothing is "
                              "drawn, above start+width everything is bright."),
    ("crop", "the part of the volume that is shown (voxel bounds, not a "
             "percentage)."),
    ("inspect_scene", "the tool that reads the current scene: its camera, its "
                      "opacity window, its crop box and its picture. It changes "
                      "nothing."),
    ("render_scene", "the tool that draws a new picture from the volume with "
                     "the settings you name."),
    ("inspect_slice", "the tool that reads one raw voxel layer as a grayscale "
                      "picture. It changes nothing."),
    ("finish_task", "the tool that saves an already rendered picture as the "
                    "result of the run."),
    ("finish_exploration", "the older name of finish_task: it ends the run "
                           "without rendering and cannot produce a new "
                           "picture."),
    ("model call", "one request to the model. Technical detail (Ctrl+H)."),
    ("HTTP 200", "the model answered without an error. Technical detail (Ctrl+H)."),
    ("harness", "the program that runs the model and hands it the renderer tools."),
    ("renderer", "the program that draws a picture from the volume."),
    ("loopback", "a service on this computer only (127.0.0.1), not the internet."),
    ("run id", "the name of one attempt. Its log, its pictures and its evidence "
               "live in the folder of that name."),
)


# -- model turns ---------------------------------------------------------

def model_turn_started(turn):
    return {
        "title": "MODEL #%d  generating its reply" % turn,
        "detail": "the model is choosing the next action: a renderer tool call "
                  "or the final answer.",
    }


def model_turn_done(turn, usage):
    usage = usage or {}
    tokens = "in %s, cached %s, out %s tokens" % (
        count(usage.get("input_tokens")),
        count(usage.get("cached_input_tokens")),
        count(usage.get("output_tokens")))
    if turn:
        title = "MODEL #%d  reply received (%s)" % (turn, tokens)
    else:
        title = "MODEL  reply received (turn count unknown; %s)" % tokens
    return {
        "title": title,
        "detail": "the reply is complete; the rows below show what it asked for.",
    }


def model_message(text):
    return {
        "title": 'MODEL  answer: "%s"' % brief(text, 90),
        "detail": "the agent's own words (a model claim, not a verified result):\n%s"
                  % brief(text, 480),
    }


def harness_warning(message):
    return {
        "title": "WARN  harness: %s" % brief(message, 90),
        "detail": "the codex harness reported this notice:\n%s" % brief(message, 320),
    }


# -- renderer tool calls -------------------------------------------------

def _tool_subject(name, arguments):
    """(headline, extra detail lines) for one tool call."""
    if name == "inspect_slice":
        axis = arguments.get("axis", "?")
        index = arguments.get("index", "?")
        return ("read slice %s=%s (changes nothing)" % (axis, count(index)),
                ["axis %s, index %s" % (axis, count(index))])
    if name == "finish_task":
        revision = arguments.get("frameRevision", "?")
        return ("save frame revision %s as the result" % count(revision),
                ["frame revision: %s" % count(revision)])
    if name == "render_scene":
        changed = parameter_lines(arguments.get("parameters"))
        if changed:
            return (", ".join(changed[:2]), ["changed: %s" % ", ".join(changed)])
        return (TOOL_MEANING["render_scene"], [])
    meaning = TOOL_MEANING.get(name, "call the renderer")
    return (meaning, [])


# The renderer's own wording, plus what it means for whoever reads the log.
ERROR_HINT = {
    "Invalid bounded numeric vector": "one of the numbers was outside the allowed range",
    "Stale scene revision": "the scene had already changed since the agent read it",
    "Observation budget exhausted": "the run had used up all its observations",
    "Empty parameter patch": "the call changed nothing",
}


def model_words(name, arguments, limit=300):
    """The words the model wrote for this call: its reason, its summary.

    One shape, used by the log and by the conversation, so the two can
    never drift apart.
    """
    arguments = arguments if isinstance(arguments, dict) else {}
    lines = []
    reason = arguments.get("reason")
    if reason:
        lines.append("%s %s" % (MODEL_MARK, brief(reason, limit)))
    if name == "finish_task" and arguments.get("summary"):
        lines.append("%s %s" % (MODEL_MARK, brief(arguments["summary"], limit)))
    return lines


def tool_call(name, arguments):
    arguments = arguments if isinstance(arguments, dict) else {}
    head, extra = _tool_subject(name, arguments)
    title = "TOOL  %s  %s" % (name, head)
    reason = arguments.get("reason")
    if reason:
        title += ' - "%s"' % brief(reason, 45)
    lines = ["%s: %s" % (name, TOOL_MEANING.get(name, "call the renderer"))]
    lines.extend(extra)
    lines.extend(model_words(name, arguments, 220))
    if "expectedRevision" in arguments:
        lines.append("the agent expected scene revision %s"
                     % count(arguments.get("expectedRevision")))
    return {"title": title, "detail": "\n".join(lines),
            "changes": parameter_lines(arguments.get("parameters"))}


def tool_return(name, seconds):
    if isinstance(seconds, (int, float)) and not isinstance(seconds, bool):
        elapsed = "returned in %ds" % int(seconds + 0.5)
    else:
        elapsed = "returned"
    return {
        "title": "TOOL  %s  %s" % (name, elapsed),
        "detail": "the call is over; its result (or the error above) went back to the model.",
    }


def tool_error(name, message, changes=None):
    lines = ["the renderer rejected this call:"]
    if changes:
        lines.append("the rejected call: %s" % ", ".join(changes))
    hint = ERROR_HINT.get(str(message or "").strip())
    if hint:
        lines.append(hint)
    lines.append(str(message or ""))
    return {
        "title": "TOOL  %s  FAILED: %s" % (name, brief(message, 90)),
        "detail": "\n".join(lines),
    }


# -- frames ---------------------------------------------------------------

def image_event(kind, result):
    """Title and detail for a frame that the renderer wrote to disk.

    The frame is named after the scene revision, which is also the name of
    the folder on disk (frame-<revision>).
    """
    result = result or {}
    scene = result.get("scene") or {}
    image = result.get("image")
    if kind == "slice":
        axis = result.get("axis", "?")
        index = count(result.get("index", "?"))
        return {
            "title": "IMAGE  slice %s=%s  saved (grayscale voxels, not a render)"
                     % (axis, index),
            "detail": "a raw grayscale slice of the volume, written to disk:\n%s"
                      % tail_path(image),
        }
    revision = scene.get("revision", "?")
    lit = result.get("nonblackPixels")
    output = scene.get("output") or []
    size = ("%sx%s" % (output[0], output[1])) if len(output) == 2 else "?"
    label = "initial frame %s" % revision if kind == "initial_render" else "new frame %s" % revision
    title = "IMAGE  %s  %s lit px  %s saved" % (label, count(lit), size)
    lines = ["%s written to disk (scene revision %s)" % (label, revision)]
    if isinstance(lit, (int, float)) and len(output) == 2:
        total = output[0] * output[1]
        lines.append("lit pixels: %s of %s (%.1f%%)   output %s"
                     % (count(lit), count(total), (100.0 * lit / total) if total else 0.0, size))
    else:
        lines.append("lit pixels: %s   output %s" % (count(lit), size))
    camera = scene.get("camera") or {}
    tf = scene.get("tf") or {}
    lines.append("camera: rotation %s shift %s   opacity: %s start %s width %s" % (
        vector(camera.get("rotationXYZ", "?")), vector(camera.get("translation", "?")),
        tf.get("type", "?"), number(tf.get("start", "?")), number(tf.get("width", "?"))))
    crop = scene.get("crop")
    if isinstance(crop, (list, tuple)) and len(crop) == 6:
        lines.append("crop: x %s-%s y %s-%s z %s-%s" % tuple(number(v) for v in crop))
    lines.append("file: %s" % tail_path(image))
    return {"title": title, "detail": "\n".join(lines)}


# -- end of run -----------------------------------------------------------

def finish_event(record):
    revision = count(record.get("selectedRevision", "?"))
    verification = record.get("externalTaskVerification", "?")
    return {
        "title": "FINISH  picked frame revision %s (task check: %s)" % (revision, verification),
        "detail": "the agent stopped here and chose frame revision %s as the result\n"
                  "task check: %s - this is the model's own claim, not an independent verification\n"
                  "the agent's claim: %s"
                  % (revision, verification, brief(record.get("modelClaim", ""), 300)),
    }


def error_event(message):
    return {
        "title": "ERROR  %s" % brief(message, 90),
        "detail": "the run reported this error:\n%s" % brief(message, 300),
    }


def model_action(action):
    action = action if isinstance(action, dict) else {}
    tool = action.get("tool", "?")
    title = "MODEL  decided: %s" % tool
    reason = action.get("reason")
    if reason:
        title += ' - "%s"' % brief(reason, 45)
    return {
        "title": title,
        "detail": "the agent's decision and why:\ntool: %s\nreason: %s"
                  % (tool, brief(reason, 300)),
    }


def paused_event(record):
    remaining = count(record.get("remaining", "?"))
    return {
        "title": "MODEL  paused, %s observations left" % remaining,
        "detail": "the agent stopped early; %s further observations (renders or slices) are allowed."
                  % remaining,
    }


# -- model service calls -------------------------------------------------

def infer_request(index, model, summary):
    title = "INFER  call #%d  Sent to model" % index
    if model:
        title += " (%s)" % model
    return {
        "title": title,
        "detail": "what was sent to the model (image bytes are never shown):\n%s" % summary,
    }


def infer_response(meta):
    index = meta.get("index", "?")
    status = meta.get("status", "?")
    seconds = meta.get("seconds")
    elapsed = ("%ds" % int(seconds + 0.5)) if isinstance(seconds, (int, float)) else "?"
    method = meta.get("method", "?")
    path = meta.get("path", "?")
    failed = bool(meta.get("error")) or (isinstance(status, int) and status >= 400)
    if failed:
        return {
            "title": "INFER  call #%s  FAILED (HTTP %s)" % (index, status),
            "detail": "%s %s failed: %s" % (method, path, brief(meta.get("error", ""), 200)),
        }
    return {
        "title": "INFER  call #%s  answer HTTP %s in %s" % (index, status, elapsed),
        "detail": "%s %s -> HTTP %s in %s" % (method, path, status, elapsed),
    }
