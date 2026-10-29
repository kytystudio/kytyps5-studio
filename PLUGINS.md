# Add-ons (plugins)

Studio can load optional add-ons that you install yourself. Studio ships none and does not know what an add-on does.
**An add-on is trusted code**: its script runs in the app window and its backend runs inside Studio with your user's
permissions. Install only add-ons you trust and can read.

## Installing

Put the add-on's folder into `%LOCALAPPDATA%\KytyPS5Studio\plugins\<id>\` (Settings > Plugins > *Open add-ons folder*)
and restart Studio. Each add-on can be switched off in Settings > Plugins (applied after a restart). A broken add-on is
reported there and never stops Studio.

## Layout

```
plugins/<id>/
  plugin.json     required
  <script>.js     optional, runs in the app window
  <styles>.css    optional
  <backend>.py    optional, runs in Studio's process
```

`plugin.json`: `id` (must equal the folder name: lowercase letters, digits and `-`), `name`, `version`, `author`,
`description`, and optionally `script`, `styles`, `backend` (plain file names in the same folder).
See `examples/hello-plugin`.

## Script API (`window.KytyStudio`)

| | |
|---|---|
| `registerTab({id, label, render, visible?, onState?})` | adds a tab beside Games; `render()` returns a DOM element |
| `registerSection({label, rows})` | adds a Settings category; `rows()` returns an array of elements |
| `row({label, desc, value, onClick})` | a Settings row; without `onClick` it is a plain info row |
| `plugin(id).call(route, body)` | calls a route of that add-on's backend |
| `api(path, body?)` | Studio's own HTTP API (same session token) |
| `h`, `$`, `$$`, `toast`, `fail`, `openKeyboard`, `confirm`, `renderView`, `switchTab`, `refreshState`, `state` | UI helpers |

Mark focusable elements with `data-f` so the controller can reach them.

## Backend API

A backend is a Python file executed once at start-up with one extra name, `studio`:

- `studio.route(name, fn)` or `@studio.route(name)`: exposes `fn(body: dict) -> JSON-able` at `/api/plugins/<id>/<name>`
- `studio.load(default)` / `studio.save(obj)`: a small JSON state file in the add-on's own data folder
- `studio.data_dir`, `studio.log(...)`

Backends do not receive Studio's internals; they are still ordinary Python with full access to your PC.

## Logs

Everything a plugin logs with `studio.log(...)`, its backend load errors, route exceptions (with traceback) and
errors thrown by its script are written to `%LOCALAPPDATA%\KytyPS5Studio\logs\plugin-<id>.log`
(Settings > Plugins > *Open logs folder*). A log over 1 MB is rotated to `plugin-<id>.old.log`.
