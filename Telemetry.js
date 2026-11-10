.pragma library

function boundedText(value, limit) {
    return typeof value === "string" ? value.replace(/[\u0000-\u0008\u000b-\u001f\u007f]/g, "").slice(0, limit) : ""
}
function number(value, low, high) {
    return typeof value === "number" && isFinite(value) && value >= low && value <= high ? value : null
}
function integer(value, low, high) {
    var parsed = number(value, low, high)
    return parsed !== null && Math.floor(parsed) === parsed ? parsed : null
}
function escapeMarkup(value) {
    return String(value).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#39;")
}
function savedTargets(value) {
    if (!Array.isArray(value) || value.length > 64) return {valid: false, fans: []}
    var fans = [], seen = ({})
    for (var i = 0; i < value.length; i++) {
        var entry = value[i]
        if (!entry || typeof entry !== "object" || Array.isArray(entry)) return {valid: false, fans: []}
        var index = integer(entry.index, 1, 64), rpm = integer(entry.rpm, 1000, 7999)
        if (index === null || rpm === null || seen[index]) return {valid: false, fans: []}
        seen[index] = true
        fans.push({index: index, rpm: rpm})
    }
    return {valid: true, fans: fans}
}
function parse(raw) {
    if (typeof raw !== "string" || raw.length > 65536) throw new Error("Invalid telemetry size")
    var data = JSON.parse(raw)
    if (!data || typeof data !== "object" || Array.isArray(data)) throw new Error("Invalid telemetry object")
    if (typeof data.is_apple_silicon !== "boolean" || (typeof data.has_fan !== "boolean" && !data.error)) throw new Error("Missing hardware state")
    var mode = ["auto", "manual", "unknown"].indexOf(data.mode) >= 0 ? data.mode : "unknown"
    var fans = [], input = Array.isArray(data.fans) ? data.fans.slice(0, 32) : [], indices = ({})
    var completeFans = Array.isArray(data.fans) && data.fans.length <= 32
    for (var i = 0; i < input.length; i++) {
        var fan = input[i]
        if (!fan || typeof fan !== "object") { completeFans = false; continue }
        var index = number(fan.index, 1, 999)
        if (index === null || Math.floor(index) !== index || indices[index]) { completeFans = false; continue }
        indices[index] = true
        fans.push({index: index, label: boundedText(fan.label, 64), rpm: number(fan.rpm, 0, 100000),
            min: integer(fan.min, 1, 100000), max: integer(fan.max, 1, 100000), target: number(fan.target, 0, 100000)})
    }
    var sensors = ({}), keys = ["nand", "battery", "regulator", "wifi"]
    var sourceSensors = data.sensors && typeof data.sensors === "object" && !Array.isArray(data.sensors) ? data.sensors : ({})
    for (var j = 0; j < keys.length; j++) sensors[keys[j]] = number(sourceSensors[keys[j]], -100, 250)
    var warnings = Array.isArray(data.warnings) ? data.warnings.slice(0, 8).map(function(value) { return boundedText(value, 160) }).filter(function(value) { return value !== "" }) : []
    var min = integer(data.fan_min, 1000, 7999), max = integer(data.fan_max, 1000, 7999)
    var error = boundedText(data.error, 80)
    var saved = savedTargets(data.saved_fans)
    var separateMemory = data.separate_memory === true
    var memory = separateMemory ? savedTargets(data.saved_separate_fans) : {valid: true, fans: []}
    var savedMode = data.saved_mode === "auto" || data.saved_mode === "manual" ? data.saved_mode : "unknown"
    var savedControlMode = ["auto", "shared", "individual"].indexOf(data.saved_control_mode) >= 0 ? data.saved_control_mode : "unknown"
    var savedRpm = integer(data.saved_rpm, 1000, 7999)
    var exactMap = saved.fans.length === fans.length && fans.every(function(fan) { return saved.fans.some(function(target) { return target.index === fan.index }) })
    var preferenceValid = saved.valid && (savedMode === "auto" && savedControlMode === "auto" && saved.fans.length === 0 && data.saved_rpm === null
        || savedMode === "manual" && (savedControlMode === "shared" && saved.fans.length === 0 && savedRpm !== null
            || savedControlMode === "individual" && data.saved_rpm === null && exactMap && fans.length > 0))
    var memoryInventoryMatches = memory.fans.length === fans.length && fans.every(function(fan) { return memory.fans.some(function(target) { return target.index === fan.index }) })
    var memoryMatchesActive = savedControlMode !== "individual" || memory.fans.length === saved.fans.length && saved.fans.every(function(fan) { return memory.fans.some(function(target) { return target.index === fan.index && target.rpm === fan.rpm }) })
    var memoryValid = memory.valid && (!separateMemory || memoryMatchesActive)
    var linkedMemory = data.linked_memory === true
    var linkedRpm = linkedMemory ? integer(data.saved_linked_rpm, 1000, 7999) : null
    var linkedMemoryValid = !linkedMemory || (data.saved_linked_rpm === null || linkedRpm !== null)
        && (savedControlMode !== "shared" || linkedRpm === savedRpm)
    preferenceValid = preferenceValid && memoryValid && linkedMemoryValid
    return {isAppleSilicon: data.is_apple_silicon === true, hasFan: data.has_fan === true,
        deviceModel: boundedText(data.device_model, 128), fans: fans, fanCount: fans.length,
        fanRpm: number(data.fan_rpm, 0, 100000), fanTarget: number(data.fan_target, 0, 100000),
        fanMin: min, fanMax: max, mode: mode, sensors: sensors,
        maxTemp: number(data.max_temp, -100, 250), powerWatts: number(data.power_watts, 0, 100000),
        error: error, errorMessage: boundedText(data.error_message, 240),
        warnings: warnings,
        controlError: boundedText(data.control_error, 240),
        controlNotice: boundedText(data.control_notice, 240),
        generation: /^[0-9a-f]{32}$/.test(data.broker_generation) ? data.broker_generation : "",
        leaseOwned: data.lease_owned === true,
        leaseRecovering: data.lease_recovering === true,
        persistent: data.persistent === true,
        savedMode: savedMode,
        savedRpm: savedRpm,
        savedControlMode: savedControlMode,
        savedFans: saved.fans,
        savedFansValid: saved.valid,
        preferenceValid: preferenceValid,
        perFanControl: data.per_fan_control === true,
        separateMemory: separateMemory,
        savedSeparateFans: memory.fans,
        separateMemoryValid: memoryValid,
        separateRestoreAvailable: memoryValid && (memory.fans.length === 0 || memoryInventoryMatches),
        linkedMemory: linkedMemory,
        savedLinkedRpm: linkedRpm,
        linkedMemoryValid: linkedMemoryValid,
        savedOwnerUid: integer(data.saved_owner_uid, 0, 4294967295),
        pausedReason: boundedText(data.paused_reason, 240),
        preferenceError: boundedText(data.preference_error, 240),
        controlAvailable: data.control_available === true,
        controlEnabled: !error && preferenceValid && data.per_fan_control === true && data.persistent === true && data.lease_recovering !== true && completeFans && data.control_available === true && data.has_fan === true && data.fan_control_enabled === true && fans.length > 0 && min !== null && max !== null && min <= max
            && fans.every(function(fan) { return fan.index <= 64 && fan.min !== null && fan.max !== null && fan.min <= fan.max && fan.min >= 1000 && fan.max <= 7999 })}
}
