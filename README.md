# Maylton's Noctalia v5 plugins

A Git source for experimental Noctalia v5 plugins.

## Available plugin

### Niri Auto-Tile

Automatically redistributes Niri column widths and optionally centers a single
tiled window. Requires Niri and Python 3.

## Add this source to Noctalia

Replace `<GITHUB_USER>` with the account that hosts this repository:

```bash
noctalia msg plugins source add maylton-plugins git https://github.com/<GITHUB_USER>/noctalia-v5-plugins
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
