# Niri Auto-Tile for Noctalia v5

A Noctalia v5 port of the original `niri-auto-tile` plugin by
[pir0c0pter0](https://github.com/pir0c0pter0/niri-auto-tile).

It listens to Niri's JSON event stream and automatically:

- redistributes tiled column widths;
- applies global or per-workspace limits;
- optionally waits until the configured column limit is reached;
- centers a workspace containing exactly one tiled window;
- exposes a bar widget and a Control Center shortcut.

## Requirements

- Noctalia v5 alpha with plugin support
- Niri
- Python 3
- POSIX shell utilities (`sh`, `kill`, `cat`)

No third-party Python modules are required.

## Install from a Noctalia source

After adding the repository containing this plugin as a Git source, enable:

```bash
noctalia msg plugins enable maylton/niri-auto-tile
```

Then add `Niri Auto-Tile` to the bar and optionally add its shortcut to the
Control Center.

## Controls

- Left click: enable or disable automation for the current session.
- Right click: force redistribution immediately.

Persistent options are under **Settings → Plugins → Niri Auto-Tile**.

## Diagnostics

The widget displays `…` while starting and `!` when an error is reported.
To inspect the local runtime copy, run `diagnose.sh` from the materialized
plugin directory shown by Noctalia's plugin debug information.

## Credits

- Original v4 plugin and concept: pir0c0pter0
- Noctalia v5 port: Maylton Fernandes and contributors
