import QtQuick
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Commons as Commons
import qs.Ui
import "Telemetry.js" as Telemetry

BarWidget {
    id: root
    moduleName: "io.github.kartikkabadi.omafan"
    property bool popupOpen: false
    property bool detailsOpen: false
    property bool dataValid: false
    property bool isAppleSilicon: true
    property bool hasFan: false
    property int fanCount: 0
    property var fanRpm: null
    property var fanTarget: null
    property int fanMin: 1200
    property int fanMax: 7200
    property bool fanControlEnabled: false
    property bool controlAvailable: false
    property string mode: "unknown"
    readonly property bool manualMode: mode === "manual"
    property var maxTemp: null
    property var powerWatts: null
    property string deviceModel: ""
    property var fans: []
    property var sensors: ({})
    property string readError: ""
    property string controlError: ""
    property string controlStatusError: ""
    property string controlNotice: ""
    property string warningText: ""
    property bool writeBusy: false
    property int writeSequence: 0
    property bool autoRetryNeedsRead: false
    property bool readBusy: false
    property int readSequence: 0
    property var pendingRequests: []
    property bool persistentControl: false
    property string savedMode: "unknown"
    property var savedRpm: null
    property var savedFans: []
    property string savedControlMode: "unknown"
    property bool savedPreferenceValid: false
    property bool perFanControl: false
    property bool separateMemory: false
    property var savedSeparateFans: []
    property bool separateRestoreAvailable: true
    property bool linkedMemory: false
    property var savedLinkedRpm: null
    property string manualControlChoice: ""
    property string pausedReason: ""
    property string preferenceError: ""
    property bool leaseRecovering: false
    readonly property string helperPath: decodeURIComponent(Qt.resolvedUrl("helper.sh").toString().replace(/^file:\/\//, ""))
    readonly property bool spinIcon: setting("spinIcon", true) === true
    readonly property int quietRpm: snapSpeed(fanMax * 0.25)
    readonly property int regularRpm: snapSpeed(fanMax * 0.5)
    readonly property int maxRpm: fanMax
    readonly property bool desiredManual: persistentControl && savedMode === "manual"
    readonly property bool preferenceConfirmed: dataValid && !writeBusy && pendingRequests.length === 0 && persistentControl && savedPreferenceValid && preferenceError === "" && (manualControlChoice === "" || manualControlChoice === savedControlMode)
    readonly property bool separateControls: manualControlChoice !== "" ? manualControlChoice === "individual" : desiredManual && savedControlMode === "individual"
    readonly property int linkedSelectedRpm: selectedLinkedSpeed()
    readonly property string modeText: strings.t(leaseRecovering ? "recovering" : mode === "manual" ? "manual" : mode === "auto" ? "auto" : "unknown")
    readonly property color foreground: bar ? bar.foreground : Commons.Color.foreground

    Commons.Translations { id: strings; directory: Qt.resolvedUrl("i18n") }
    ListModel { id: fanCards; dynamicRoles: true }
    function syncFanCards(rows) {
        // Update channel roles in place so periodic telemetry cannot destroy a
        // slider delegate while it owns an ongoing pointer or keyboard gesture.
        for (var i = 0; i < rows.length; i++) {
            var found = -1
            for (var j = i; j < fanCards.count; j++) if (fanCards.get(j).fanIndex === rows[i].index) { found = j; break }
            if (found < 0) fanCards.insert(i, {fanIndex: rows[i].index, fanData: rows[i]})
            else {
                if (found !== i) fanCards.move(found, i, 1)
                fanCards.setProperty(i, "fanData", rows[i])
            }
        }
        while (fanCards.count > rows.length) fanCards.remove(fanCards.count - 1)
    }
    function formatNumber(value, decimals) {
        return value === null || value === undefined || !isFinite(value) ? "—" : Commons.I18n.formatNumber(value, decimals || 0)
    }
    function formatRpm(value) { return formatNumber(value, 0) }
    function snapSpeed(value) {
        var clamped = Math.max(fanMin, Math.min(fanMax, Number(value) || fanMin))
        return Math.max(fanMin, Math.min(fanMax, fanMin + Math.round((clamped - fanMin) / 50) * 50))
    }
    function invalidate(message) {
        dataValid = false
        fanControlEnabled = false
        mode = "unknown"
        hasFan = false
        fanCount = 0
        fanRpm = null
        fanTarget = null
        maxTemp = null
        powerWatts = null
        fans = []
        syncFanCards([])
        sensors = ({})
        leaseRecovering = false
        manualControlChoice = ""
        readError = message
        warningText = ""
    }
    function applyData(raw) {
        try {
            var data = Telemetry.parse(raw)
            isAppleSilicon = data.isAppleSilicon
            deviceModel = data.deviceModel
            controlAvailable = data.controlAvailable
            controlStatusError = data.controlError
            controlNotice = data.controlNotice
            persistentControl = data.persistent
            savedMode = data.savedMode
            savedRpm = data.savedRpm
            savedFans = data.savedFans
            savedControlMode = data.savedControlMode
            savedPreferenceValid = data.preferenceValid
            perFanControl = data.perFanControl
            separateMemory = data.separateMemory
            savedSeparateFans = data.savedSeparateFans
            separateRestoreAvailable = data.separateRestoreAvailable
            linkedMemory = data.linkedMemory
            savedLinkedRpm = data.savedLinkedRpm
            pausedReason = data.pausedReason
            preferenceError = data.preferenceError
            if (data.error) {
                invalidate(data.error === "unsupported_hardware" ? strings.t("unsupported") : data.error === "macsmc_hwmon_missing" ? strings.t("driverMissing") : (data.errorMessage || strings.t("readFailed")))
                return
            }
            fanRpm = data.fanRpm
            fanTarget = data.fanTarget
            fanMin = data.fanMin === null ? 1200 : data.fanMin
            fanMax = data.fanMax === null ? 7200 : data.fanMax
            fanCount = data.fanCount
            fans = data.fans
            syncFanCards(data.fans)
            hasFan = data.hasFan && fanCount > 0
            mode = data.mode
            leaseRecovering = data.leaseRecovering
            fanControlEnabled = data.controlEnabled
            sensors = data.sensors
            maxTemp = data.maxTemp
            powerWatts = data.powerWatts
            dataValid = true
            readError = ""
            warningText = data.warnings.join("\n")
            freshness.restart()
        } catch (error) {
            invalidate(strings.t("readFailed"))
        }
    }
    function refresh(allowAutoRetry) {
        if (readBusy || writeBusy) return
        readBusy = true
        readProc.sequence = ++readSequence
        readProc.buffer = ""
        readProc.failed = false
        readProc.cancelled = false
        readProc.allowAutoRetry = allowAutoRetry === true
        readProc.running = true
        readDeadline.restart()
    }
    function togglePopup() { popupOpen = !popupOpen; if (popupOpen) refresh() }
    function close() { popupOpen = false }
    function fanByIndex(index) {
        for (var i = 0; i < fans.length; i++) if (fans[i].index === index) return fans[i]
        return null
    }
    function snapFanSpeed(fan, value) {
        if (!fan || fan.min === null || fan.max === null) return null
        var clamped = Math.max(fan.min, Math.min(fan.max, Number(value) || fan.min))
        return Math.max(fan.min, Math.min(fan.max, fan.min + Math.round((clamped - fan.min) / 50) * 50))
    }
    function clampFanSpeed(fan, value) {
        if (!fan || fan.min === null || fan.max === null || value === null || value === undefined) return null
        return Math.max(fan.min, Math.min(fan.max, value))
    }
    function fanSelected(fan) {
        for (var pending = pendingRequests.length - 1; pending >= 0; pending--) {
            var request = pendingRequests[pending]
            if (request.kind === "linked" && linkedBankValue() !== null) return clampFanSpeed(fan, linkedBankValue())
            if (request.kind === "separate") {
                for (var memory = 0; memory < savedSeparateFans.length; memory++) if (savedSeparateFans[memory].index === fan.index) return clampFanSpeed(fan, savedSeparateFans[memory].rpm)
            }
            if (request.kind === "shared") return clampFanSpeed(fan, Number(request.args[1]))
            if (request.kind === "fan" && Number(request.args[1]) === fan.index) return clampFanSpeed(fan, Number(request.args[2]))
        }
        if (writeBusy && writeProc.activeRequest && writeProc.activeRequest.kind === "fan" && Number(writeProc.activeRequest.args[1]) === fan.index)
            return clampFanSpeed(fan, Number(writeProc.activeRequest.args[2]))
        if (writeBusy && writeProc.activeRequest && writeProc.activeRequest.kind === "shared") return clampFanSpeed(fan, Number(writeProc.activeRequest.args[1]))
        if (writeBusy && writeProc.activeRequest && writeProc.activeRequest.kind === "linked" && linkedBankValue() !== null) return clampFanSpeed(fan, linkedBankValue())
        if (writeBusy && writeProc.activeRequest && writeProc.activeRequest.kind === "separate") {
            for (var activeMemory = 0; activeMemory < savedSeparateFans.length; activeMemory++) if (savedSeparateFans[activeMemory].index === fan.index) return clampFanSpeed(fan, savedSeparateFans[activeMemory].rpm)
        }
        if (savedControlMode === "individual") {
            for (var i = 0; i < savedFans.length; i++) if (savedFans[i].index === fan.index) return clampFanSpeed(fan, savedFans[i].rpm)
        }
        if (savedControlMode === "shared" && savedRpm !== null) return clampFanSpeed(fan, savedRpm)
        return clampFanSpeed(fan, fan.target !== null ? fan.target : fan.rpm !== null ? fan.rpm : fan.min)
    }
    function autoPending() {
        return (writeBusy && writeProc.activeRequest && writeProc.activeRequest.kind === "auto")
            || pendingRequests.some(function(request) { return request.kind === "auto" })
    }
    function highestDesiredSpeed() {
        var highest = fanMin
        var restoringSeparate = (writeBusy && writeProc.activeRequest && writeProc.activeRequest.kind === "separate") || pendingRequests.some(function(request) { return request.kind === "separate" })
        for (var i = 0; i < fans.length; i++) {
            var fan = fans[i]
            var values = [fan.target, fanSelected(fan)]
            if (restoringSeparate) for (var remembered = 0; remembered < savedSeparateFans.length; remembered++) if (savedSeparateFans[remembered].index === fan.index) values.push(savedSeparateFans[remembered].rpm)
            if (savedControlMode === "shared") values.push(savedRpm)
            else for (var j = 0; j < savedFans.length; j++) if (savedFans[j].index === fan.index) values.push(savedFans[j].rpm)
            for (var k = 0; k < values.length; k++) {
                var candidate = clampFanSpeed(fan, values[k])
                if (candidate !== null) highest = Math.max(highest, candidate)
            }
        }
        return Math.max(fanMin, Math.min(fanMax, highest))
    }
    function selectedLinkedSpeed() {
        for (var i = pendingRequests.length - 1; i >= 0; i--) {
            if (pendingRequests[i].kind === "shared") return Math.max(fanMin, Math.min(fanMax, Number(pendingRequests[i].args[1])))
            if (pendingRequests[i].kind === "linked") return clampLinkedBank()
        }
        if (writeBusy && writeProc.activeRequest && writeProc.activeRequest.kind === "shared") return Math.max(fanMin, Math.min(fanMax, Number(writeProc.activeRequest.args[1])))
        if (writeBusy && writeProc.activeRequest && writeProc.activeRequest.kind === "linked") return clampLinkedBank()
        return savedControlMode === "shared" && savedRpm !== null ? Math.max(fanMin, Math.min(fanMax, savedRpm)) : highestDesiredSpeed()
    }
    function linkedBankValue() {
        // An in-flight scalar update commits before a queued Linked restore.
        if (writeBusy && writeProc.activeRequest && writeProc.activeRequest.kind === "shared") return Number(writeProc.activeRequest.args[1])
        return savedLinkedRpm
    }
    function clampLinkedBank() {
        var value = linkedBankValue()
        return value === null ? highestDesiredSpeed() : Math.max(fanMin, Math.min(fanMax, value))
    }
    function restoreLinked() {
        if (!fanControlEnabled || !dataValid || autoPending()) return
        if (!linkedMemory) { setSpeed(highestDesiredSpeed()); return }
        manualControlChoice = "shared"
        enqueue({kind:"linked",key:"linked",args:["set-linked"]})
    }
    function chooseControlMode(choice) {
        if (!fanControlEnabled || !dataValid || autoPending() || (choice !== "shared" && choice !== "individual")) return
        if ((choice === "individual") === separateControls) return
        if (choice === "individual" && separateMemory && !separateRestoreAvailable) { controlError = strings.t("separateMemoryMismatch"); return }
        var transfer = highestDesiredSpeed()
        manualControlChoice = choice
        pendingRequests = pendingRequests.filter(function(request) { return request.kind === "auto" })
        if (choice === "shared") {
            if (linkedMemory) restoreLinked()
            else setSpeed(transfer)
        }
        else if (separateMemory) enqueue({kind:"separate",key:"separate",args:["set-separate"]})
        else if (fans.length) {
            var firstTarget = fanSelected(fans[0])
            if (firstTarget !== null) setFan(fans[0].index, firstTarget, true)
        }
    }
    function enqueue(request) {
        if (request.kind === "auto") pendingRequests = [request]
        else {
            if (autoPending()) return
            var queue = pendingRequests.filter(function(previous) { return request.kind !== "separate" && request.kind !== "linked" && previous.key !== request.key && (request.kind !== "shared" || (previous.kind !== "fan" && previous.kind !== "separate")) && (request.kind !== "fan" || (previous.kind !== "shared" && previous.kind !== "linked")) })
            queue.push(request)
            pendingRequests = queue
        }
        startPending()
    }
    function setSpeed(target) {
        var value = String(target)
        if (!/^(auto|[1-7][0-9]{3})$/.test(value)) return
        if (value === "auto") {
            if (!controlAvailable) return
            autoRetryNeedsRead = false
            manualControlChoice = ""
            enqueue({kind:"auto",key:"auto",args:["set", "auto"]})
        } else {
            if (!fanControlEnabled || !dataValid || autoPending()) return
            manualControlChoice = "shared"
            enqueue({kind:"shared",key:"shared",args:["set", value]})
        }
    }
    function setFan(index, rpm, preserveExact) {
        var channel = Telemetry.integer(index, 1, 64)
        var value = Telemetry.integer(rpm, 1000, 7999)
        var fan = fanByIndex(channel)
        if (!fanControlEnabled || !perFanControl || !dataValid || !fan || value === null || autoPending()) return
        value = preserveExact === true ? clampFanSpeed(fan, value) : snapFanSpeed(fan, value)
        manualControlChoice = "individual"
        enqueue({kind:"fan",key:"fan:" + channel,args:["set-fan", String(channel), String(value)]})
    }
    function enableManual() {
        if (!fanControlEnabled || autoPending()) return
        if (desiredManual) return
        if (manualControlChoice === "individual" && separateMemory) enqueue({kind:"separate",key:"separate",args:["set-separate"]})
        else if (manualControlChoice === "individual" && fans.length) setFan(fans[0].index, fanSelected(fans[0]), true)
        else restoreLinked()
    }
    function retainAutoRecovery() {
        pendingRequests = pendingRequests.filter(function(request) { return request.kind === "auto" })
    }
    function finishWriteFailure(sequence, request, message) {
        if (sequence !== writeSequence || !writeBusy) return
        manualControlChoice = ""
        controlError = message
        retainAutoRecovery()
        if (request && request.kind === "auto" && pendingRequests.length === 0) pendingRequests = [request]
        // Preserve firmware-recovery intent, but retry only on a subsequent
        // successful periodic read. A failing command must not form a busy loop.
        autoRetryNeedsRead = pendingRequests.some(function(pending) { return pending.kind === "auto" })
        writeBusy = false
        // A failed executable cannot supply status either; leave that retry to
        // the normal poll rather than immediately starting another dead read.
        if (writeProc.didStart) refresh()
    }
    function startPending() {
        if (writeBusy || pendingRequests.length === 0) return
        if (!dataValid || !fanControlEnabled || !perFanControl) {
            retainAutoRecovery()
            manualControlChoice = ""
        }
        if (pendingRequests.length === 0) return
        if (pendingRequests[0].kind === "auto" && autoRetryNeedsRead) return
        writeBusy = true
        writeProc.sequence = ++writeSequence
        if (readBusy) readProc.cancelled = true
        if (readProc.running) readProc.signal(9)
        var queue = pendingRequests.slice()
        writeProc.activeRequest = queue.shift()
        pendingRequests = queue
        writeProc.buffer = ""
        writeProc.output = ""
        writeProc.timedOut = false
        writeProc.didStart = false
        controlError = ""
        writeProc.running = true
        writeDeadline.restart()
    }
    Component.onCompleted: refresh()

    Timer { interval: root.popupOpen ? 1500 : 3000; running: true; repeat: true; onTriggered: root.refresh(true) }
    Timer { id: freshness; interval: 10000; onTriggered: root.invalidate(strings.t("stale")) }
    Timer {
        id: readDeadline
        interval: 5000
        onTriggered: {
            readProc.failed = true
            root.invalidate(strings.t("readTimeout"))
            if (readProc.running) readProc.signal(9)
            else root.readBusy = false
        }
    }
    Process {
        id: readProc
        property string buffer: ""
        property bool failed: false
        property bool cancelled: false
        property bool allowAutoRetry: false
        property int sequence: 0
        command: [root.helperPath, "get"]
        stdout: SplitParser {
            onRead: function(line) {
                if (readProc.buffer.length + line.length > 65536) { readProc.failed = true; return }
                readProc.buffer += line + "\n"
            }
        }
        onExited: function(code) {
            readDeadline.stop()
            var sequence = readProc.sequence
            Qt.callLater(function() {
                if (sequence !== root.readSequence || !root.readBusy) return
                if (readProc.cancelled) { root.readBusy = false; return }
                if (!readProc.failed) {
                    if (code !== 0 && readProc.buffer.trim() === "") root.invalidate(strings.t("readFailed"))
                    else root.applyData(readProc.buffer)
                } else if (root.readError === "") root.invalidate(strings.t("readFailed"))
                root.readBusy = false
                if (readProc.allowAutoRetry && root.dataValid && root.controlAvailable && root.autoRetryNeedsRead) {
                    root.autoRetryNeedsRead = false
                    root.startPending()
                }
            })
        }
    }
    Timer {
        id: writeDeadline
        interval: 5000
        onTriggered: {
            writeProc.timedOut = true
            root.controlError = strings.t("writeTimeout")
            root.manualControlChoice = ""
            root.retainAutoRecovery()
            if (writeProc.running) writeProc.signal(9)
            else root.finishWriteFailure(writeProc.sequence, writeProc.activeRequest, strings.t("writeTimeout"))
        }
    }
    Process {
        id: writeProc
        property var activeRequest: null
        property string buffer: ""
        property string output: ""
        property bool timedOut: false
        property bool didStart: false
        property int sequence: 0
        onStarted: didStart = true
        command: activeRequest ? [root.helperPath].concat(activeRequest.args) : []
        stdout: SplitParser { onRead: function(line) { if (writeProc.output.length < 16384) writeProc.output += line + "\n" } }
        stderr: SplitParser { onRead: function(line) { if (writeProc.buffer.length < 1024) writeProc.buffer += line.slice(0, 240) + "\n" } }
        onExited: function(code) {
            writeDeadline.stop()
            var sequence = writeProc.sequence
            var request = writeProc.activeRequest
            Qt.callLater(function() {
                if (sequence !== root.writeSequence || !root.writeBusy) return
                if (code !== 0 || writeProc.timedOut) {
                    var message = strings.t("writeTimeout")
                    if (!writeProc.timedOut) {
                        var detail = writeProc.buffer.trim()
                        try { detail = JSON.parse(writeProc.output).error || detail } catch (error) {}
                        message = Telemetry.boundedText(detail, 240) || strings.t("writeFailed")
                    }
                    root.finishWriteFailure(sequence, request, message)
                    return
                } else {
                    try {
                        var reply = JSON.parse(writeProc.output)
                        var checked = Telemetry.parse(JSON.stringify({is_apple_silicon:true,has_fan:root.fans.length > 0,fans:root.fans,
                            persistent:reply.persistent,saved_mode:reply.saved_mode,saved_rpm:reply.saved_rpm,
                            saved_fans:reply.saved_fans,saved_control_mode:reply.saved_control_mode,
                            separate_memory:reply.separate_memory,saved_separate_fans:reply.saved_separate_fans,
                            linked_memory:reply.linked_memory,saved_linked_rpm:reply.saved_linked_rpm}))
                        if (writeProc.activeRequest.kind !== "auto" && (reply.persistent !== true || !checked.preferenceValid)) throw new Error("Invalid saved fan targets")
                        if (request.kind === "fan" && (checked.savedControlMode !== "individual" || !checked.savedFans.some(function(fan) { return fan.index === Number(request.args[1]) && fan.rpm === Number(request.args[2]) }))) throw new Error("Fan target was not acknowledged")
                        if (request.kind === "shared" && (checked.savedControlMode !== "shared" || checked.savedRpm !== Number(request.args[1]))) throw new Error("Shared target was not acknowledged")
                        if (request.kind === "separate" && (!checked.separateMemory || checked.savedControlMode !== "individual" || !checked.separateMemoryValid)) throw new Error("Remembered separate profile was not acknowledged")
                        if (request.kind === "linked" && (!checked.linkedMemory || checked.savedControlMode !== "shared" || !checked.linkedMemoryValid)) throw new Error("Remembered linked speed was not acknowledged")
                        if (request.kind === "auto" && checked.preferenceValid && checked.savedMode !== "auto") throw new Error("Automatic mode was not acknowledged")
                        if (reply.persistent === true && checked.preferenceValid) {
                            root.persistentControl = true
                            root.savedMode = checked.savedMode
                            root.savedRpm = checked.savedRpm
                            root.savedFans = checked.savedFans
                            root.savedControlMode = checked.savedControlMode
                            root.savedPreferenceValid = true
                            root.separateMemory = checked.separateMemory
                            root.savedSeparateFans = checked.savedSeparateFans
                            root.separateRestoreAvailable = checked.separateRestoreAvailable
                            root.linkedMemory = checked.linkedMemory
                            root.savedLinkedRpm = checked.savedLinkedRpm
                        } else root.savedPreferenceValid = false // Auto recovery works with older brokers too.
                    } catch (error) {
                        root.finishWriteFailure(sequence, request, strings.t("writeFailed"))
                        return
                    }
                }
                root.writeBusy = false
                if (root.pendingRequests.length === 0 && root.manualControlChoice === root.savedControlMode) root.manualControlChoice = ""
                root.refresh()
                root.startPending()
            })
        }
    }
    implicitWidth: button.implicitWidth
    implicitHeight: button.implicitHeight
    BarIconButton {
        id: button
        objectName: "thermals-bar-button"
        activeFocusOnTab: true
        Accessible.role: Accessible.Button
        Accessible.focusable: true
        Accessible.name: strings.t("title")
        Accessible.checkable: true
        Accessible.checked: root.popupOpen
        Accessible.onPressAction: if (enabled && visible) root.togglePopup()
        Keys.onReturnPressed: root.togglePopup()
        Keys.onEnterPressed: root.togglePopup()
        Keys.onSpacePressed: root.togglePopup()
        anchors.fill: parent
        bar: root.bar
        slotSize: Style.bar.statusSlot
        foreground: !root.dataValid ? Commons.Color.muted : root.maxTemp !== null && root.maxTemp >= 80 ? Commons.Color.urgent : root.manualMode ? Commons.Color.accent : root.foreground
        tooltipText: Telemetry.escapeMarkup(strings.t("title") + ": " + (!root.dataValid ? (root.readError || strings.t("loading")) : root.hasFan ? strings.t("tooltip").arg(root.formatRpm(root.fanRpm)).arg(root.formatNumber(root.maxTemp, 1)) : strings.t("temperature").arg(root.formatNumber(root.maxTemp, 1))))
        onPressed: function(mouseButton) { if (mouseButton === Qt.LeftButton) root.togglePopup() }
        iconComponent: Component {
            FanIcon {
                objectName: "thermal-bar-icon"
                text: !root.dataValid ? "󰌺" : root.hasFan ? "󰈐" : "󰔏"
                color: button.foreground
                fontFamily: button.fontFamily
                fontSize: button.fontSize
                fan: root.dataValid && root.hasFan
                spinning: root.spinIcon && root.fanRpm > 0
            }
        }
    }

    PopupCard {
        id: popup
        anchorItem: button
        bar: root.bar
        owner: root
        open: root.popupOpen
        contentWidth: fittedContentWidth(Style.space(450))
        contentHeight: fittedContentHeight(mainCol.implicitHeight)
        onOpenChanged: if (open) Qt.callLater(function() { focusScope.forceActiveFocus() })
        FocusScope {
            id: focusScope
            anchors.fill: parent
            Keys.onEscapePressed: root.close()
            Flickable {
                id: scroll
                anchors.fill: parent
                clip: true
                contentWidth: width
                contentHeight: mainCol.implicitHeight
                boundsBehavior: Flickable.StopAtBounds
                Column {
                    id: mainCol
                    width: scroll.width
                    spacing: Style.space(14)

                    Row {
                        width: parent.width
                        spacing: Style.space(10)
                        BorderSurface {
                            id: headerIcon
                            width: Style.space(36); height: width
                            radius: Style.cornerRadius
                            color: Style.selectedFillFor(root.foreground, Commons.Color.accent)
                            ThermalText { anchors.centerIn: parent; text: "󰈐"; font.pixelSize: Style.font.subtitle; color: Commons.Color.accent }
                        }
                        Column {
                            width: parent.width - headerIcon.width - headerActions.width - parent.spacing * 2
                            anchors.verticalCenter: parent.verticalCenter
                            spacing: Style.space(3)
                            ThermalText { width: parent.width; text: strings.t("title"); font.pixelSize: Style.font.body; font.bold: true }
                            ThermalText { width: parent.width; text: root.deviceModel || strings.t("loading"); color: Commons.Color.muted; maximumLineCount: 2; elide: Text.ElideRight }
                        }
                        Row {
                            id: headerActions
                            spacing: Style.space(2)
                            AccessibleButton { objectName: "refresh-thermals"; text: "󰑐"; fontSize: Style.font.body; horizontalPadding: Style.space(7); tooltipText: strings.t("refresh"); Accessible.name: strings.t("refresh"); focusable: true; enabled: !root.readBusy; onClicked: root.refresh() }
                            AccessibleButton { objectName: "close-thermals"; text: "×"; fontSize: Style.font.body; horizontalPadding: Style.space(7); tooltipText: strings.t("close"); Accessible.name: strings.t("close"); focusable: true; onClicked: root.close() }
                        }
                    }

                    BorderSurface {
                        width: parent.width
                        radius: Style.cornerRadius
                        color: Style.normalFillFor(root.foreground, Commons.Color.accent)
                        borderSpec: Border.flat(Util.alpha(root.foreground, 0.10), 1)
                        height: stateRow.implicitHeight + Style.space(16)
                        Row {
                            id: stateRow
                            anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top
                            anchors.margins: Style.space(8)
                            spacing: Style.space(8)
                            Rectangle { width: Style.space(6); height: width; radius: width / 2; color: !root.dataValid ? Commons.Color.muted : root.manualMode ? Commons.Color.accent : root.desiredManual && root.mode !== "manual" ? Commons.Color.urgent : root.foreground; anchors.verticalCenter: parent.verticalCenter }
                            ThermalText { width: parent.width - stateBadge.implicitWidth - Style.space(22); text: !root.dataValid ? strings.t("unavailable") : root.writeBusy ? strings.t("saving") : root.desiredManual && root.mode !== "manual" && !root.leaseRecovering ? strings.t("paused") : root.modeText; font.bold: true }
                            ThermalText { id: stateBadge; text: root.preferenceConfirmed ? "✓ " + strings.t("saved") : strings.t("live"); color: Commons.Color.muted; anchors.verticalCenter: parent.verticalCenter }
                        }
                    }

                    BorderSurface {
                        width: parent.width
                        visible: root.readError !== "" || root.controlError !== "" || root.controlStatusError !== "" || root.preferenceError !== ""
                        height: errorText.implicitHeight + Style.space(20)
                        radius: Style.cornerRadius
                        color: Util.alpha(Commons.Color.urgent, 0.10)
                        borderSpec: Border.flat(Util.alpha(Commons.Color.urgent, 0.5), 1)
                        ThermalText { id: errorText; anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top; anchors.margins: Style.space(10); text: [root.readError, root.controlError, root.controlStatusError, root.preferenceError].filter(function(value) { return value !== "" }).join("\n"); color: Commons.Color.urgent }
                    }

                    Column {
                        width: parent.width
                        visible: root.hasFan || root.controlAvailable || root.desiredManual
                        spacing: Style.space(10)
                        ThermalText { width: parent.width; text: strings.t("coolingLabel"); font.pixelSize: Style.font.body; font.bold: true }
                        BorderSurface {
                            width: parent.width
                            height: modeButtons.implicitHeight + Style.space(8)
                            radius: Style.cornerRadius
                            color: Style.normalFillFor(root.foreground, Commons.Color.accent)
                            borderSpec: Border.flat(Util.alpha(root.foreground, 0.10), 1)
                            Row {
                                id: modeButtons
                                anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top
                                anchors.margins: Style.space(4)
                                spacing: Style.space(4)
                                AccessibleButton { objectName: "mode-auto"; checkable: true; width: (parent.width - parent.spacing) / 2; text: strings.t("autoButton"); iconText: "󰒓"; focusable: true; enabled: root.controlAvailable; selected: !root.desiredManual && root.mode === "auto"; onClicked: root.setSpeed("auto") }
                                AccessibleButton { objectName: "mode-manual"; checkable: true; width: (parent.width - parent.spacing) / 2; text: strings.t("manualButton"); iconText: "󰒺"; focusable: true; enabled: root.fanControlEnabled && !root.writeBusy; selected: root.desiredManual; onClicked: root.enableManual() }
                            }
                        }

                        Column {
                            width: parent.width
                            visible: root.desiredManual && root.fanCount > 1
                            spacing: Style.space(6)
                            ThermalText { width: parent.width; text: strings.t("fanControlStyle"); color: Commons.Color.muted }
                            Row {
                                width: parent.width
                                spacing: Style.space(6)
                                AccessibleButton { objectName: "layout-linked"; checkable: true; width: (parent.width - parent.spacing) / 2; text: strings.t("linked"); iconText: "󰌷"; focusable: true; selected: !root.separateControls; enabled: root.fanControlEnabled; onClicked: root.chooseControlMode("shared") }
                                AccessibleButton { objectName: "layout-separate"; checkable: true; width: (parent.width - parent.spacing) / 2; text: strings.t("separate"); iconText: "󰕮"; focusable: true; selected: root.separateControls; enabled: root.fanControlEnabled && (!root.separateMemory || root.separateRestoreAvailable); onClicked: root.chooseControlMode("individual") }
                            }
                        }
                        LinkedFanControls {
                            objectName: "linked-fan-controls"
                            width: parent.width
                            visible: root.desiredManual && !root.separateControls
                            bar: root.bar
                            minimum: root.fanMin; maximum: root.fanMax
                            targetRpm: root.linkedSelectedRpm
                            controlsEnabled: root.fanControlEnabled
                            title: strings.t(root.fanCount === 2 ? "targetBoth" : root.fanCount > 2 ? "targetAll" : "targetLabel")
                            note: strings.t("linkedNote")
                            minLabel: strings.t("minShort"); maxLabel: strings.t("maxShort")
                            presetLabels: [strings.t("quiet"), strings.t("balanced"), strings.t("maximum")]
                            formatValue: root.formatRpm
                            onTargetRequested: function(rpm) { root.setSpeed(rpm) }
                        }
                        ThermalText { width: parent.width; visible: root.desiredManual && root.preferenceConfirmed; text: "✓ " + strings.t(root.separateControls ? "rememberedIndependent" : "remembered"); color: Commons.Color.muted }
                        ThermalText { width: parent.width; visible: root.desiredManual && root.fanCount > 1 && !root.separateMemory; text: strings.t("separateMemoryUnsupported"); color: Commons.Color.muted }
                        ThermalText { width: parent.width; visible: root.desiredManual && !root.linkedMemory; text: strings.t("linkedMemoryUnsupported"); color: Commons.Color.muted }
                        ThermalText { width: parent.width; visible: root.desiredManual && root.separateMemory && !root.separateRestoreAvailable; text: strings.t("separateMemoryMismatch"); color: Commons.Color.muted }
                        ThermalText { width: parent.width; visible: !root.desiredManual && root.mode === "auto"; text: strings.t("automaticNote"); color: Commons.Color.muted }
                        ThermalText { width: parent.width; visible: !root.persistentControl || !root.controlAvailable; text: strings.t(root.persistentControl ? "controlUnavailable" : "setup"); color: Commons.Color.muted }
                        ThermalText { width: parent.width; visible: root.pausedReason !== ""; text: root.pausedReason; color: Commons.Color.urgent }
                    }


                    Grid {
                        id: fanGrid
                        width: parent.width
                        visible: root.dataValid && root.hasFan
                        columns: width < Style.space(330) || root.fans.length === 1 ? 1 : 2
                        spacing: Style.space(10)
                        Repeater {
                            model: fanCards
                            FanCard {
                                required property var fanData
                                property int fanIndex: fanData.index
                                objectName: "fan-control-" + fanIndex
                                bar: root.bar
                                manual: root.desiredManual && root.separateControls
                                controlsEnabled: root.fanControlEnabled
                                minimum: fanData.min !== null ? fanData.min : 1200
                                maximum: fanData.max !== null ? fanData.max : 7200
                                targetRpm: root.fanSelected(fanData)
                                targetLabel: strings.t("targetLabel")
                                minimumLabel: strings.t("minShort")
                                maximumLabel: strings.t("maxShort")
                                presetLabels: [strings.t("quiet"), strings.t("balanced"), strings.t("maximum")]
                                formatValue: root.formatRpm
                                onTargetRequested: function(rpm) { root.setFan(fanIndex, rpm) }
                                width: (fanGrid.width - fanGrid.spacing * (fanGrid.columns - 1)) / fanGrid.columns
                                nameText: fanData.label || strings.t("fanFallback").arg(root.formatRpm(fanData.index))
                                speedText: root.formatRpm(fanData.rpm)
                                fraction: fanData.rpm !== null && fanData.max !== null && fanData.max > 0 ? fanData.rpm / fanData.max : 0
                                percentText: fanData.rpm !== null && fanData.max !== null && fanData.max > 0 ? strings.t("percent").arg(root.formatNumber(Math.min(100, Math.round(fraction * 100)))) : "—"
                                targetText: strings.t("fanTarget").arg(root.formatRpm(fanData.target))
                                rangeText: strings.t("fanRange").arg(root.formatRpm(fanData.min)).arg(root.formatRpm(fanData.max))
                            }
                        }
                    }

                    Grid {
                        id: metrics
                        width: parent.width
                        visible: root.dataValid
                        columns: width < Style.space(260) ? 1 : 2
                        spacing: Style.space(10)
                        MetricCard { width: (metrics.width - metrics.spacing * (metrics.columns - 1)) / metrics.columns; labelText: strings.t("componentLabel"); valueText: root.formatNumber(root.maxTemp, 1); unitText: "°C"; iconText: "󰔏"; valueColor: root.maxTemp !== null && root.maxTemp >= 80 ? Commons.Color.urgent : root.foreground }
                        MetricCard { width: (metrics.width - metrics.spacing * (metrics.columns - 1)) / metrics.columns; labelText: strings.t("powerLabel"); valueText: root.formatNumber(root.powerWatts, 1); unitText: "W"; iconText: "󰚥" }
                    }

                    PanelSeparator { width: parent.width }
                    Column {
                        width: parent.width
                        spacing: Style.space(8)
                        AccessibleButton { objectName: "sensor-details"; checkable: true; width: parent.width; text: strings.t("sensorDetails"); iconText: root.detailsOpen ? "󰅃" : "󰅀"; leftAlign: true; focusable: true; selected: root.detailsOpen; onClicked: root.detailsOpen = !root.detailsOpen }
                        Grid {
                            id: sensorGrid
                            width: parent.width
                            visible: root.detailsOpen && root.dataValid
                            columns: width < Style.space(330) ? 1 : 2
                            spacing: Style.space(8)
                            Repeater {
                                model: ["nand", "battery", "regulator", "wifi"]
                                Column {
                                    required property string modelData
                                    width: (sensorGrid.width - sensorGrid.spacing * (sensorGrid.columns - 1)) / sensorGrid.columns
                                    spacing: Style.space(3)
                                    ThermalText { width: parent.width; text: strings.t(modelData + "Label"); color: Commons.Color.muted }
                                    ThermalText { width: parent.width; text: strings.t("temperature").arg(root.formatNumber(root.sensors[modelData], 1)); font.bold: true }
                                }
                            }
                        }
                        ThermalText { width: parent.width; visible: root.detailsOpen; text: strings.t("sensorNote"); color: Commons.Color.muted }
                        ThermalText { width: parent.width; visible: root.detailsOpen && root.warningText !== ""; text: root.warningText; color: Commons.Color.muted }
                        ThermalText { width: parent.width; visible: root.detailsOpen && root.controlNotice !== ""; text: root.controlNotice; color: Commons.Color.muted }
                    }
                }
            }
        }
    }

}
