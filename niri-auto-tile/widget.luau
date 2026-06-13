-- Compact bar widget. Left click toggles automatic redistribution;
-- right click forces an immediate redistribution.

local status = noctalia.state.get("status") or {
  available = true,
  running = false,
  enabled = true,
  state = "starting",
  maxVisible = 4,
  perWorkspace = false,
  onlyAtMax = true,
  centerSingleWindow = true,
  lastError = "",
}
local sequence = 0

local function send(action)
  sequence = sequence + 1
  noctalia.state.set("command", {
    action = action,
    source = "widget",
    sequence = sequence,
  })
end

local function render()
  local glyph = barWidget.getConfig("glyph") or "layout-columns"
  local showCount = barWidget.getConfig("show_count") ~= false
  local count = tonumber(status.maxVisible) or 4

  barWidget.setGlyph(glyph)
  local text = showCount and tostring(count) or ""
  if status.state == "error" or status.state == "unavailable" then
    text = "!"
  elseif status.state == "starting" and status.running ~= true then
    text = "…"
  end
  barWidget.setText(text)
  barWidget.setVisible(true)

  local automation = status.enabled and "enabled" or "disabled"
  local daemon = status.running and "daemon running" or ("daemon " .. tostring(status.state or "stopped"))
  local mode = status.onlyAtMax and "only at limit" or "always redistribute"
  local workspaceMode = status.perWorkspace and "per-workspace limits" or "global limit"
  local singleMode = status.centerSingleWindow and "single window centered" or "single window unchanged"
  local tooltip = "Niri Auto-Tile: " .. automation ..
    "\n" .. daemon ..
    "\nLimit: " .. tostring(count) .. " columns · " .. mode .. " · " .. workspaceMode ..
    "\n" .. singleMode ..
    "\nLeft click: toggle · Right click: redistribute now"

  if status.lastError ~= nil and tostring(status.lastError) ~= "" then
    tooltip = tooltip .. "\nError: " .. tostring(status.lastError)
  end
  barWidget.setTooltip(tooltip)
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
