/**
 * ================================================================
 *  TelloAI – Frontend JavaScript
 *  Handles: polling, toggle controls, messaging, mic, log rendering
 * ================================================================
 */

"use strict";

// ================================================================
//  CLIENT-SIDE STATE
// ================================================================

const ui = {
    /** Number of log entries already rendered (avoids full re-render) */
    lastLogCount: 0,

    /** Whether the microphone is currently recording */
    isRecording: false,

    /** Reference to the SpeechRecognition instance */
    recognition: null,
};


// ================================================================
//  INITIALISATION  –  runs after DOM is fully loaded
// ================================================================

document.addEventListener("DOMContentLoaded", () => {
    _startPolling();
    addLocalLog("system", "Dashboard connessa");
});


// ================================================================
//  POLLING  –  fetch status & logs from the Flask backend
// ================================================================

/**
 * Start two polling loops:
 *   - status every 2 s  (battery, toggle states)
 *   - logs   every 1 s  (new log entries)
 */
function _startPolling() {
    setInterval(_fetchStatus, 2000);
    setInterval(_fetchLogs,   1000);
}

async function _fetchStatus() {
    try {
        const res  = await fetch("/api/status");
        const data = await res.json();

        _updateBattery(data.battery);
        _syncTogglesUI(data);
    } catch {
        // Silent fail – server may be temporarily unavailable
    }
}

async function _fetchLogs() {
    try {
        const res  = await fetch("/api/logs");
        const data = await res.json();
        _renderLogs(data.logs);
    } catch {
        // Silent fail
    }
}


// ================================================================
//  BATTERY  –  update indicator bar and icon
// ================================================================

/**
 * @param {number} percent  0-100
 */
function _updateBattery(percent) {
    percent = Math.max(0, Math.min(100, Number(percent) || 0));

    const fill    = document.getElementById("battery-fill");
    const label   = document.getElementById("battery-percent");
    const icon    = document.getElementById("battery-icon");

    fill.style.width  = percent + "%";
    label.textContent = percent + "%";

    // ── Colour + icon based on level ──────────────────────────────
    if (percent > 60) {
        fill.style.background = "linear-gradient(90deg,#4caf7d,#2e7d52)";
        icon.className = "fas fa-battery-full battery-bar__icon";
    } else if (percent > 30) {
        fill.style.background = "linear-gradient(90deg,#ffa726,#ef6c00)";
        icon.className = "fas fa-battery-half battery-bar__icon";
    } else {
        fill.style.background = "linear-gradient(90deg,#ef5350,#c62828)";
        icon.className = "fas fa-battery-quarter battery-bar__icon";
    }
}


// ================================================================
//  TOGGLE SYNC  –  mirror server state into the UI checkboxes
// ================================================================

/**
 * Synchronise the two toggle switches with the server's current state.
 * @param {{ wifi_connected: boolean, stream_active: boolean }} data
 */
function _syncTogglesUI(data) {
    _setToggle("wifi",   data.wifi_connected);
    _setToggle("stream", data.stream_active);
}

/**
 * Update a single toggle checkbox and its status label.
 * @param {"wifi"|"sdk"|"stream"} name
 * @param {boolean} active
 */
function _setToggle(name, active) {
    const checkbox = document.getElementById(`${name}-toggle`);
    const status   = document.getElementById(`${name}-status`);

    if (checkbox) checkbox.checked = active;
    if (status) {
        status.textContent = active ? "ON" : "OFF";
        status.classList.toggle("on", active);
    }
}


// ================================================================
//  CONTROL HANDLERS  –  called by onchange of each toggle input
// ================================================================

/**
 * Toggle WiFi connection to the drone.
 * @param {HTMLInputElement} checkbox
 */
async function toggleWifi(checkbox) {
    const ok = await _apiPost("/api/toggle_wifi");
    if (!ok) {
        checkbox.checked = !checkbox.checked; // revert on failure
        return;
    }
    _setToggle("wifi", ok.connected);
    addLocalLog(ok.connected ? "success" : "warning", ok.message);
}

/**
 * Toggle the video stream.
 * @param {HTMLInputElement} checkbox
 */
async function toggleStream(checkbox) {
    const ok = await _apiPost("/api/toggle_stream");
    if (!ok) {
        checkbox.checked = !checkbox.checked;
        return;
    }
    _setToggle("stream", ok.active);
    addLocalLog(ok.active ? "success" : "warning", ok.message);

    // ── Show / hide video feed element ────────────────────────────
    const feed        = document.getElementById("video-feed");
    const placeholder = document.getElementById("video-placeholder");

    if (ok.active) {
        // Reload src to restart the MJPEG stream
        feed.src          = "/video_feed";
        feed.style.display    = "block";
        placeholder.style.display = "none";
    } else {
        feed.src              = "";
        feed.style.display    = "none";
        placeholder.style.display = "flex";
    }
}


// ================================================================
//  MESSAGING  –  text commands from the input field
// ================================================================

/** Send the content of the input field to the backend. */
async function sendMessage() {
    const input   = document.getElementById("message-input");
    const message = input.value.trim();
    if (!message) return;

    input.value = "";   // clear immediately for better UX

    try {
        const res  = await fetch("/api/send_message", {
            method:  "POST",
            headers: { "Content-Type": "application/json" },
            body:    JSON.stringify({ message }),
        });
        const data = await res.json();

        if (!data.success) {
            addLocalLog("error", data.message);
        }
        // Successful entries are logged server-side and arrive via poll
    } catch (err) {
        addLocalLog("error", "Errore invio messaggio: " + err.message);
    }
}

/**
 * Allow pressing Enter to send the message.
 * @param {KeyboardEvent} event
 */
function handleInputKey(event) {
    if (event.key === "Enter") sendMessage();
}


// ================================================================
//  MICROPHONE  –  Web Speech API for voice input
// ================================================================

/** Toggle microphone recording on/off. */
function toggleMic() {
    const btn = document.getElementById("mic-btn");
    const SR  = window.SpeechRecognition || window.webkitSpeechRecognition;

    if (!SR) {
        addLocalLog("error", "Riconoscimento vocale non supportato da questo browser");
        return;
    }

    if (ui.isRecording) {
        // ── Stop recording ─────────────────────────────────────────
        ui.recognition?.stop();
        ui.isRecording = false;
        btn.classList.remove("recording");
        return;
    }

    // ── Start recording ───────────────────────────────────────────
    ui.recognition = new SR();
    ui.recognition.lang            = "it-IT";
    ui.recognition.interimResults  = false;
    ui.recognition.maxAlternatives = 1;

    ui.recognition.onstart = () => {
        ui.isRecording = true;
        btn.classList.add("recording");
        addLocalLog("info", "Microfono attivo, in ascolto…");
    };

    ui.recognition.onresult = (event) => {
        const transcript = event.results[0][0].transcript;
        document.getElementById("message-input").value = transcript;
        addLocalLog("info", "Riconosciuto: " + transcript);
    };

    ui.recognition.onerror = (event) => {
        addLocalLog("error", "Errore microfono: " + event.error);
    };

    ui.recognition.onend = () => {
        ui.isRecording = false;
        btn.classList.remove("recording");
    };

    ui.recognition.start();
}


// ================================================================
//  LOG RENDERING  –  display log entries in the log box
// ================================================================

/**
 * Re-render the log box only when new entries have arrived from the server.
 * @param {Array<{time:string, level:string, message:string}>} logs
 */
function _renderLogs(logs) {
    if (logs.length === ui.lastLogCount) return; // nothing new
    ui.lastLogCount = logs.length;

    const box = document.getElementById("log-box");
    box.innerHTML = "";

    logs.forEach(log => {
        box.appendChild(_buildLogEntry(log.time, log.level, log.message));
    });

    // Auto-scroll to latest entry
    box.scrollTop = box.scrollHeight;
}

/**
 * Immediately append a log entry in the client (without waiting for poll).
 * Useful for instant user feedback.
 * @param {string} level
 * @param {string} message
 */
function addLocalLog(level, message) {
    const now  = new Date();
    const time = now.toTimeString().slice(0, 8);
    const box  = document.getElementById("log-box");

    box.appendChild(_buildLogEntry(time, level, message));
    box.scrollTop = box.scrollHeight;

    // Prevent local counter drift when server logs arrive
    ui.lastLogCount = 0;
}

/**
 * Build a single log DOM element.
 * @param {string} time
 * @param {string} level
 * @param {string} message
 * @returns {HTMLDivElement}
 */
function _buildLogEntry(time, level, message) {
    const div  = document.createElement("div");
    div.className = `log-entry log-${level}`;

    const tspan = document.createElement("span");
    tspan.className   = "log-time";
    tspan.textContent = `[${time}]`;

    const mspan = document.createElement("span");
    mspan.className   = "log-msg";
    mspan.textContent = message;   // textContent prevents XSS

    div.appendChild(tspan);
    div.appendChild(mspan);
    return div;
}

/** Remove all log entries displayed in the UI. */
function clearLogs() {
    const box = document.getElementById("log-box");
    box.innerHTML = "";
    ui.lastLogCount = 0;
    addLocalLog("system", "Log svuotato");
}


// ================================================================
//  UTILITIES  –  generic helpers
// ================================================================

/**
 * POST to an API endpoint and return parsed JSON, or null on failure.
 * Errors are automatically added to the local log.
 * @param {string} url
 * @returns {Promise<object|null>}
 */
async function _apiPost(url) {
    try {
        const res  = await fetch(url, { method: "POST" });
        const data = await res.json();

        if (!data.success) {
            addLocalLog("error", data.message || "Errore sconosciuto");
            return null;
        }
        return data;
    } catch (err) {
        addLocalLog("error", `Errore di rete (${url}): ${err.message}`);
        return null;
    }
}
