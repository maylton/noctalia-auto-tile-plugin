### Niri Auto-Tile

This is a port of  the The original Noctalia V4 plugin created by pir0c0pter0. Automatically redistributes Niri column widths and optionally centers a single
tiled window.
The plugin listens to Niri's JSON event stream and automatically redistributes tiled column widths when windows are opened or closed. It also supports per-workspace limits, an “only at max” mode, a bar widget and a Control Center shortcut.

## Requirements
Python 3.

## Instalation

```bash
noctalia msg plugins source add maylton-plugins git \
  https://github.com/maylton/noctalia-v5-plugins

noctalia msg plugins update maylton-plugins

noctalia msg plugins enable maylton/niri-auto-tile
```
The same can be done in **Settings → Plugins → Add source**.

After enabling the plugin, add its widget to the bar. The plugin-level settings
are available from the gear icon on its row.

## Repository layout

```text
catalog.toml
niri-auto-tile/
  plugin.toml
  service.luau
  widget.luau
  shortcut.luau
  auto-tile.py
```

No installer is run by the plugin manager. All required runtime files must
therefore be committed inside the plugin directory, as they are here.

## Status

The Noctalia v5 plugin API is experimental and can introduce breaking changes.
This port was vibe-coded. Use at your own risk.
