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

    /** WebSocket connection to Vosk backend */
    voiceSocket: null,

    /** AudioContext for capturing microphone audio */
    audioContext: null,

    /** MediaStream from getUserMedia */
    micStream: null,

    /** ScriptProcessorNode for audio processing */
    audioProcessor: null,

    /** Prevent overlapping /api/send_message calls */
    isSendingMessage: false,

    /** Lightweight dedupe guard for accidental double-submit */
    lastSentMessage: "",
    lastSentAt: 0,
};


// ================================================================
//  INITIALISATION  –  runs after DOM is fully loaded
// ================================================================

document.addEventListener("DOMContentLoaded", async () => {
    // Pulizia d'emergenza all'avvio: chiudi eventuali connessioni dangling al drone
    await _performCleanup();
    
    _startPolling();
    addLocalLog("system", "Dashboard connessa");
});


/**
 * Cleanup d'emergenza: chiama l'endpoint /api/cleanup per liberare
 * il drone da eventuali connessioni dangling della sessione precedente.
 */
async function _performCleanup() {
    try {
        const res = await fetch("/api/cleanup", { method: "POST" });
        if (res.ok) {
            const data = await res.json();
            if (data.success) {
                console.log("[cleanup] Drone liberato");
            }
        }
    } catch (err) {
        // Errore non critico: il drone potrebbe non essere connesso
        console.warn("[cleanup] Errore durante cleanup avvio:", err);
    }
}


// ================================================================
//  POLLING  –  fetch status & logs from the Flask backend
// ================================================================

/**
 * Start two polling loops:
 *   - status every 2 s  (battery, toggle states)
 *   - logs   every 1 s  (new log entries)
 *   - fps    every 0.5s (stream FPS)
 */
function _startPolling() {
    setInterval(_fetchStatus, 2000);
    setInterval(_fetchLogs,   1000);
    setInterval(_fetchFps,    500);
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

async function _fetchFps() {
    try {
        const res  = await fetch("/api/fps");
        const data = await res.json();
        const fpsDisplay = document.getElementById("fps-display");
        if (fpsDisplay) {
            fpsDisplay.textContent = data.fps;
        }
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

    const now = Date.now();
    const duplicateWindowMs = 800;
    if (ui.isSendingMessage) return;
    if (ui.lastSentMessage === message && (now - ui.lastSentAt) < duplicateWindowMs) {
        return;
    }

    ui.isSendingMessage = true;
    ui.lastSentMessage = message;
    ui.lastSentAt = now;

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
    } finally {
        ui.isSendingMessage = false;
    }
}

/**
 * Allow pressing Enter to send the message.
 * @param {KeyboardEvent} event
 */
function handleInputKey(event) {
    if (event.key !== "Enter") return;
    event.preventDefault();
    if (event.repeat) return;
    sendMessage();
}


// ================================================================
//  MICROPHONE  –  Vosk WebSocket for voice input
// ================================================================

/** Toggle microphone recording on/off. */
function toggleMic() {
    const btn = document.getElementById("mic-btn");

    if (ui.isRecording) {
        // ── Stop recording ─────────────────────────────────────────
        _stopVoiceRecognition();
        btn.classList.remove("recording");
        return;
    }

    // ── Start recording ───────────────────────────────────────────
    _startVoiceRecognition();
}

/**
 * Start voice recognition via WebSocket to Vosk backend.
 */
async function _startVoiceRecognition() {
    const btn = document.getElementById("mic-btn");

    try {
        // Richiedi accesso al microfono
        ui.micStream = await navigator.mediaDevices.getUserMedia({ 
            audio: {
                echoCancellation: true,
                noiseSuppression: true,
                sampleRate: 16000
            } 
        });

        // Crea AudioContext a 16kHz (richiesto da Vosk)
        ui.audioContext = new (window.AudioContext || window.webkitAudioContext)({ sampleRate: 16000 });
        const source = ui.audioContext.createMediaStreamSource(ui.micStream);

        // Crea ScriptProcessor per catturare e convertire l'audio
        ui.audioProcessor = ui.audioContext.createScriptProcessor(4096, 1, 1);
        
        ui.audioProcessor.onaudioprocess = (e) => {
            if (!ui.voiceSocket || ui.voiceSocket.readyState !== WebSocket.OPEN) return;
            
            // Converti Float32 a Int16 PCM (formato richiesto da Vosk)
            const inputData = e.inputBuffer.getChannelData(0);
            const pcmData = new Int16Array(inputData.length);
            
            for (let i = 0; i < inputData.length; i++) {
                // Clampa e converte in 16-bit PCM
                const sample = Math.max(-1, Math.min(1, inputData[i]));
                pcmData[i] = sample < 0 ? sample * 0x8000 : sample * 0x7FFF;
            }
            
            // Invia i dati PCM al backend via WebSocket
            ui.voiceSocket.send(pcmData.buffer);
        };

        source.connect(ui.audioProcessor);
        ui.audioProcessor.connect(ui.audioContext.destination);

        // Connetti WebSocket al backend Vosk
        const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
        const wsUrl = `${protocol}//${window.location.host}/voice/ws`;
        
        ui.voiceSocket = new WebSocket(wsUrl);

        ui.voiceSocket.onopen = () => {
            ui.isRecording = true;
            btn.classList.add("recording");
            addLocalLog("info", "🎤 Microfono attivo, in ascolto...");
            console.log("[voice] WebSocket connesso, registrazione avviata");
        };

        ui.voiceSocket.onmessage = (event) => {
            try {
                const data = JSON.parse(event.data);
                
                if (data.error) {
                    addLocalLog("error", "Errore riconoscimento: " + data.error);
                    _stopVoiceRecognition();
                    return;
                }

                if (data.is_final && data.text) {
                    // Trascrizione finale: popola l'input e (opzionale) invia
                    const input = document.getElementById("message-input");
                    input.value = data.text;
                    addLocalLog("info", "📝 Riconosciuto: " + data.text);
                    
                    // Auto-invia il messaggio dopo la trascrizione
                    // Commenta la riga seguente se preferisci conferma manuale
                    sendMessage();
                } else if (data.text) {
                    // Feedback parziale (opzionale)
                    console.log("[voice] Parziale:", data.text);
                }
            } catch (err) {
                console.error("[voice] Errore parsing messaggio:", err);
            }
        };

        ui.voiceSocket.onerror = (error) => {
            addLocalLog("error", "Errore WebSocket vocale");
            console.error("[voice] WebSocket error:", error);
            _stopVoiceRecognition();
        };

        ui.voiceSocket.onclose = () => {
            console.log("[voice] WebSocket chiuso");
            _stopVoiceRecognition();
        };

    } catch (err) {
        addLocalLog("error", "Errore accesso microfono: " + err.message);
        console.error("[voice] Errore:", err);
        _stopVoiceRecognition();
    }
}

/**
 * Stop voice recognition and cleanup resources.
 */
function _stopVoiceRecognition() {
    const btn = document.getElementById("mic-btn");
    
    // Chiudi WebSocket
    if (ui.voiceSocket) {
        try {
            ui.voiceSocket.close();
        } catch (e) {}
        ui.voiceSocket = null;
    }

    // Ferma stream microfono
    if (ui.micStream) {
        ui.micStream.getTracks().forEach(track => track.stop());
        ui.micStream = null;
    }

    // Disconnetti e chiudi AudioContext
    if (ui.audioProcessor) {
        try {
            ui.audioProcessor.disconnect();
        } catch (e) {}
        ui.audioProcessor = null;
    }

    if (ui.audioContext) {
        try {
            ui.audioContext.close();
        } catch (e) {}
        ui.audioContext = null;
    }

    // Aggiorna UI
    ui.isRecording = false;
    btn.classList.remove("recording");
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
async function clearLogs() {
    let ok = await _apiPost("/api/logs/clear");
    if (!ok) {
        ok = await _apiPost("/api/logs?action=clear");
    }
    if (!ok) return;

    const box = document.getElementById("log-box");
    box.innerHTML = "";
    ui.lastLogCount = -1;
    await _fetchLogs();
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


// ================================================================
//  SETTINGS MODAL  –  configuration management
// ================================================================

/**
 * Open the settings modal and load the current .env configuration.
 */
async function openSettingsModal() {
    const modal = document.getElementById("settings-modal");
    const container = document.getElementById("settings-form-container");
    
    // Show modal with loading state
    modal.style.display = "flex";
    container.innerHTML = '<div class="loading-spinner"><i class="fas fa-spinner fa-spin"></i> Caricamento impostazioni…</div>';
    
    try {
        const res = await fetch("/api/config");
        const data = await res.json();
        const config = data.config || {};
        const descriptions = data.descriptions || {};
        
        // Build form with all config variables
        container.innerHTML = "";
        
        const sortedKeys = Object.keys(config).sort();
        sortedKeys.forEach(key => {
            const group = document.createElement("div");
            group.className = "settings-form-group";
            
            const label = document.createElement("label");
            label.className = "settings-form-label";
            label.title = descriptions[key] || ""; // Tooltip con descrizione
            
            const labelText = document.createElement("span");
            labelText.textContent = key;
            label.appendChild(labelText);
            
            // Add help icon if description exists
            if (descriptions[key]) {
                const helpIcon = document.createElement("i");
                helpIcon.className = "fas fa-info-circle settings-help-icon";
                helpIcon.title = descriptions[key];
                label.appendChild(helpIcon);
            }
            
            const input = document.createElement("input");
            input.type = "text";
            input.className = "settings-form-input";
            input.id = `config-${key}`;
            input.value = config[key];
            input.placeholder = `Valore per ${key}`;
            input.title = descriptions[key] || ""; // Tooltip sull'input
            
            // Add data attributes for validation
            input.dataset.configKey = key;
            
            // Add validation event listeners
            input.addEventListener("change", (e) => _validateConfigField(e.target, descriptions[key]));
            
            group.appendChild(label);
            group.appendChild(input);
            container.appendChild(group);
        });
        
        addLocalLog("info", "Impostazioni caricate");
    } catch (err) {
        container.innerHTML = `<div style="color: #c62828; padding: 20px;">Errore caricamento configurazione: ${err.message}</div>`;
        addLocalLog("error", "Errore caricamento impostazioni: " + err.message);
    }
}

/**
 * Validate a single configuration field based on business rules.
 * @param {HTMLInputElement} input
 * @param {string} description
 */
function _validateConfigField(input, description) {
    const key = input.dataset.configKey;
    const value = input.value.trim();
    
    // Remove previous error state
    input.classList.remove("settings-form-input--error");
    
    // LOG_MAX_ENTRIES: between 0 and 100
    if (key === "LOG_MAX_ENTRIES") {
        const num = parseInt(value, 10);
        if (isNaN(num) || num < 0 || num > 100) {
            input.classList.add("settings-form-input--error");
            input.title = "❌ Deve essere un numero tra 0 e 100";
            return false;
        }
        input.title = description || "";
    }
    
    // OCR_INTERVAL_SECONDS: minimum 1 second
    if (key === "OCR_INTERVAL_SECONDS") {
        const num = parseFloat(value);
        if (isNaN(num) || num < 1.0) {
            input.classList.add("settings-form-input--error");
            input.title = "❌ Deve essere minimo 1.0 secondo";
            return false;
        }
        input.title = description || "";
    }
    
    return true;
}

/**
 * Close the settings modal.
 */
function closeSettingsModal() {
    const modal = document.getElementById("settings-modal");
    modal.style.display = "none";
}

/**
 * Save the settings form and update .env file.
 */
async function saveSettings() {
    const modal = document.getElementById("settings-modal");
    const container = document.getElementById("settings-form-container");
    
    // Validate all fields before saving
    const inputs = container.querySelectorAll(".settings-form-input");
    let hasErrors = false;
    
    inputs.forEach(input => {
        if (!_validateConfigField(input, input.title)) {
            hasErrors = true;
        }
    });
    
    if (hasErrors) {
        addLocalLog("error", "❌ Correggi gli errori di validazione (campi rossi)");
        return;
    }
    
    // Collect all form input values
    const config = {};
    inputs.forEach(input => {
        const key = input.id.replace("config-", "");
        config[key] = input.value;
    });
    
    try {
        const res = await fetch("/api/config", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ config }),
        });
        const data = await res.json();
        
        if (data.success) {
            const applied = Array.isArray(data.applied_keys) ? data.applied_keys : [];
            const restartRequired = Array.isArray(data.restart_required_keys)
                ? data.restart_required_keys
                : [];

            addLocalLog("success", data.message || "Configurazione salvata");

            if (applied.length > 0) {
                addLocalLog("system", `Applicate a caldo: ${applied.join(", ")}`);
            }

            if (restartRequired.length > 0) {
                addLocalLog("warning", `Richiedono riavvio server: ${restartRequired.join(", ")}`);
            }

            closeSettingsModal();
            _fetchStatus();
            _fetchLogs();
        } else {
            addLocalLog("error", data.message || "Errore salvataggio configurazione");
        }
    } catch (err) {
        addLocalLog("error", "Errore salvataggio impostazioni: " + err.message);
    }
}

/**
 * Close modal when clicking the overlay.
 */
document.addEventListener("DOMContentLoaded", () => {
    const modal = document.getElementById("settings-modal");
    if (modal) {
        modal.addEventListener("click", (event) => {
            // Only close if clicking the overlay itself, not the content
            if (event.target === modal.querySelector(".settings-modal__overlay")) {
                closeSettingsModal();
            }
        });
    }
});