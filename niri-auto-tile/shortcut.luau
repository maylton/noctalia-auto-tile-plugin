-- Control-center tile for Niri Auto-Tile.

local status = noctalia.state.get("status") or {
  available = true,
  running = false,
  enabled = true,
  state = "starting",
}
local sequence = 0

local function send(action)
  sequence = sequence + 1
  noctalia.state.set("command", {
    action = action,
    source = "shortcut",
    sequence = sequence,
  })
end

local function render()
  local active = status.enabled == true
  local suffix = active and "On" or "Off"
  if status.available == false then suffix = "Unavailable" end

  shortcut.setLabel("Auto-Tile " .. suffix)
  shortcut.setIcon("layout-columns")
  shortcut.setActive(active)
  shortcut.setEnabled(status.available ~= false)
end

noctalia.state.watch("status", function(value)
  if type(value) == "table" then
    status = value
    render()
  end
end)

noctalia.setUpdateInterval(1000)

function update()
  render()
end

function onClick()
  send("toggle")
end

function onRightClick()
  send("redistribute")
end

function onIpc(event, payload)
  if event == "toggle" or event == "enable" or event == "disable" or
     event == "redistribute" or event == "restart" then
    send(event)
  end
end

render()
