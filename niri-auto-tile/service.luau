-- Niri Auto-Tile service for Noctalia v5.
-- Supervises the Python daemon, mirrors manifest settings into a JSON runtime
-- file, and shares status/commands with the bar widget and shortcut entries.

local pluginDirectory = noctalia.pluginDir()
local scriptPath = pluginDirectory .. "/auto-tile.py"
local configPath = pluginDirectory .. "/runtime-config.json"
local pidPath = pluginDirectory .. "/auto-tile.pid"

local daemonSpawned = false
local daemonAlive = false
local daemonState = "starting"
local lastError = ""
local lastLog = ""
local healthCheckPending = false
local startupTicks = 0
local restartCooldown = 0
local lastFingerprint = ""
local lastConfiguredEnabled = noctalia.getConfig("enabled") ~= false
local runtimeEnabled = lastConfiguredEnabled

local function trim(value)
  return tostring(value or ""):gsub("^%s+", ""):gsub("%s+$", "")
end

local function shellQuote(value)
  local text = tostring(value or "")
  text = text:gsub("'", "'\"'\"'")
  return "'" .. text .. "'"
end

local function intConfig(key, fallback, minimum, maximum)
  local value = tonumber(noctalia.getConfig(key)) or fallback
  value = math.floor(value)
  if value < minimum then value = minimum end
  if value > maximum then value = maximum end
  return value
end

local function boolConfig(key, fallback)
  local value = noctalia.getConfig(key)
  if value == nil then return fallback end
  return value == true
end

local function workspaceLimits()
  local raw = noctalia.getConfig("workspace_config")
  if raw == nil or tostring(raw) == "" then return {} end
  local decoded, err = noctalia.json.decode(tostring(raw))
  if decoded == nil or type(decoded) ~= "table" then
    lastError = "Invalid workspace_config JSON: " .. tostring(err or "expected an object")
    return {}
  end
  return decoded
end

local function currentConfig()
  return {
    enabled = runtimeEnabled,
    maxVisible = intConfig("max_visible", 4, 1, 8),
    onlyAtMax = boolConfig("only_at_max", true),
    centerSingleWindow = boolConfig("center_single_window", true),
    perWorkspace = boolConfig("per_workspace", false),
    workspaceMaxVisible = workspaceLimits(),
    debounceMs = intConfig("debounce_ms", 300, 50, 2000),
    maxEventsPerSecond = intConfig("max_events_per_second", 20, 1, 100),
    debug = boolConfig("debug", false),
  }
end

local function publishStatus()
  local cfg = currentConfig()
  noctalia.state.set("status", {
    available = noctalia.commandExists("python3") and noctalia.commandExists("niri") and noctalia.fileExists(scriptPath),
    running = daemonAlive,
    state = daemonState,
    enabled = runtimeEnabled,
    configuredEnabled = lastConfiguredEnabled,
    maxVisible = cfg.maxVisible,
    perWorkspace = cfg.perWorkspace,
    onlyAtMax = cfg.onlyAtMax,
    centerSingleWindow = cfg.centerSingleWindow,
    lastError = lastError,
    lastLog = lastLog,
  })
end

local function signalDaemon(signalName)
  local command = "sh -lc " .. shellQuote(
    "if test -s " .. shellQuote(pidPath) .. "; then " ..
    "pid=$(cat " .. shellQuote(pidPath) .. "); " ..
    "kill -" .. signalName .. " \"$pid\" 2>/dev/null || true; " ..
    "fi"
  )
  noctalia.runAsync(command)
end

local function writeRuntimeConfig(reloadDaemon)
  local cfg = currentConfig()
  local encoded = noctalia.json.encode(cfg, true)
  if encoded == nil then
    lastError = "Could not encode runtime configuration"
    publishStatus()
    return false
  end

  local ok = noctalia.writeFile(configPath, encoded .. "\n")
  if ok == false then
    lastError = "Could not write " .. configPath
    publishStatus()
    return false
  end

  if reloadDaemon and daemonAlive then
    signalDaemon("USR1")
  end
  return true
end

local function parseDaemonLine(line)
  local message = trim(line)
  if message == "" then return end

  lastLog = message
  if message == "READY" then
    daemonAlive = true
    daemonState = runtimeEnabled and "running" or "disabled"
    lastError = ""
  elseif message:sub(1, 6) == "ERROR " then
    lastError = message:sub(7)
    daemonState = "error"
  elseif message:sub(1, 8) == "[ERROR] " then
    lastError = message:sub(9)
    daemonState = "error"
  elseif message:sub(1, 7) == "STATUS " then
    local payload = message:sub(8)
    local decoded = noctalia.json.decode(payload)
    if type(decoded) == "table" then
      if decoded.message ~= nil then lastLog = tostring(decoded.message) end
      if decoded.error ~= nil and tostring(decoded.error) ~= "" then
        lastError = tostring(decoded.error)
      end
    end
  elseif boolConfig("debug", false) then
    noctalia.log("[niri-auto-tile] " .. message)
  end
  publishStatus()
end

local function launchDaemon()
  if daemonSpawned then return end

  if not noctalia.commandExists("python3") then
    daemonState = "unavailable"
    lastError = "python3 was not found"
    publishStatus()
    return
  end
  if not noctalia.commandExists("niri") then
    daemonState = "unavailable"
    lastError = "niri was not found"
    publishStatus()
    return
  end
  if not noctalia.fileExists(scriptPath) then
    daemonState = "unavailable"
    lastError = "auto-tile.py is missing"
    publishStatus()
    return
  end

  writeRuntimeConfig(false)
  daemonSpawned = true
  daemonAlive = false
  daemonState = "starting"
  startupTicks = 0
  lastError = ""
  publishStatus()

  local command = "python3 -u " .. shellQuote(scriptPath) ..
    " --config-file " .. shellQuote(configPath) ..
    " --pid-file " .. shellQuote(pidPath) .. " 2>&1"

  local started = noctalia.runStream(command, function(line)
    parseDaemonLine(line)
  end)

  if started == false then
    daemonSpawned = false
    daemonAlive = false
    daemonState = "error"
    lastError = "Noctalia refused to start the daemon stream"
    publishStatus()
  end
end

local function checkDaemonHealth()
  if healthCheckPending then return end
  healthCheckPending = true

  local probe = "sh -lc " .. shellQuote(
    "if test -s " .. shellQuote(pidPath) .. "; then " ..
    "pid=$(cat " .. shellQuote(pidPath) .. "); " ..
    "if kill -0 \"$pid\" 2>/dev/null; then echo ALIVE; else echo DEAD; fi; " ..
    "else echo DEAD; fi"
  )

  local accepted = noctalia.runAsync(probe, function(processResult)
    healthCheckPending = false
    local output = ""
    if type(processResult) == "table" then
      output = processResult.stdout or ""
      if processResult.timedOut == true then
        lastError = "Daemon health check timed out"
      end
    end
    local result = trim(output)
    if result == "ALIVE" then
      daemonAlive = true
      daemonSpawned = true
      daemonState = runtimeEnabled and "running" or "disabled"
      startupTicks = 0
    else
      daemonAlive = false
      startupTicks = startupTicks + 1
      if daemonSpawned and startupTicks >= 3 then
        daemonSpawned = false
        daemonState = "stopped"
        restartCooldown = 2
      end
    end
    publishStatus()
  end)

  if accepted == false then
    healthCheckPending = false
    lastError = "Noctalia refused to queue the daemon health check"
    publishStatus()
  end
end

local function applyCommand(action)
  if action == "toggle" then
    runtimeEnabled = not runtimeEnabled
    daemonState = runtimeEnabled and (daemonAlive and "running" or "starting") or "disabled"
    writeRuntimeConfig(true)
  elseif action == "enable" then
    runtimeEnabled = true
    daemonState = daemonAlive and "running" or "starting"
    writeRuntimeConfig(true)
  elseif action == "disable" then
    runtimeEnabled = false
    daemonState = "disabled"
    writeRuntimeConfig(true)
  elseif action == "redistribute" then
    signalDaemon("USR2")
  elseif action == "restart" then
    signalDaemon("TERM")
    daemonAlive = false
    daemonSpawned = false
    daemonState = "restarting"
    restartCooldown = 1
  end
  publishStatus()
end

noctalia.state.watch("command", function(command)
  if type(command) == "table" and command.action ~= nil then
    applyCommand(tostring(command.action))
  end
end)

noctalia.setUpdateInterval(1000)

function update()
  local configuredEnabled = boolConfig("enabled", true)
  if configuredEnabled ~= lastConfiguredEnabled then
    lastConfiguredEnabled = configuredEnabled
    runtimeEnabled = configuredEnabled
  end

  local cfg = currentConfig()
  local fingerprint = table.concat({
    tostring(cfg.enabled),
    tostring(cfg.maxVisible),
    tostring(cfg.onlyAtMax),
    tostring(cfg.centerSingleWindow),
    tostring(cfg.perWorkspace),
    tostring(noctalia.getConfig("workspace_config") or "{}"),
    tostring(cfg.debounceMs),
    tostring(cfg.maxEventsPerSecond),
    tostring(cfg.debug),
  }, "|")

  if fingerprint ~= lastFingerprint then
    local shouldReload = lastFingerprint ~= ""
    lastFingerprint = fingerprint
    writeRuntimeConfig(shouldReload)
  end

  checkDaemonHealth()

  if not daemonSpawned then
    if restartCooldown > 0 then
      restartCooldown = restartCooldown - 1
    else
      launchDaemon()
    end
  end

  publishStatus()
end

function onIpc(event, payload)
  if event == "toggle" or event == "enable" or event == "disable" or
     event == "redistribute" or event == "restart" then
    applyCommand(event)
  elseif event == "set-columns" then
    lastError = "set-columns is not persistent in v5; change Maximum visible columns in plugin settings"
    publishStatus()
  elseif event == "status" then
    publishStatus()
  end
end

launchDaemon()
