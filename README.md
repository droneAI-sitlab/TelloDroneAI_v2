## Introduzione

`TelloDroneAI_v2` è una web app Flask pensata per controllare e monitorare un drone DJI Tello. Offre streaming video in tempo reale, acquisizione foto/video, elaborazione delle immagini (OpenCV), invio di frame a un server OCR, e generazione/esecuzione di comandi automatici tramite un modello LLM (Ollama) o comandi vocali offline (Vosk). L'interfaccia web permette controllo manuale, toggle per WiFi/stream/OCR e una vista log per il debug rapido.

Questa applicazione unisce controllo manuale e automazioni basate su visione e linguaggio per il drone DJI Tello. I principali obiettivi sono: fornire una interfaccia web reattiva per pilotare e monitorare il drone; raccogliere e processare frame video per riconoscimento testo (OCR) e successiva interpretazione semantica tramite un modello LLM; permettere controllo vocale offline e garantire un'esecuzione sicura e tracciabile dei comandi.

Funzionalità principali:
- Streaming video MJPEG in tempo reale dal Tello, ridimensionamento e ottimizzazione per banda bassa.
- Pipeline di elaborazione frame (resize, contrasto, encoding JPEG) con hook per analisi AI.
- Invio selettivo di frame ad un server OCR remoto e parsing delle risposte in entità testuali.
- Integrazione con Ollama per trasformare output OCR / descrizioni in comandi strutturati (FunctionGemma).
- Riconoscimento vocale offline tramite Vosk per comandi naturali in italiano.
- Coda comandi con priorità, validazione safety (limiti di distanza/angolo/velocità) e ritardi configurabili.
- Salvataggio locale di foto e clip video, con naming e rotazione dei file in `captures/`.
- Dashboard web con toggle per WiFi, stream e OCR, input testuale per comandi e log in tempo reale.

Flusso utente tipico:
1. Connetti il PC al WiFi del drone (toggle WiFi nella UI).
2. Avvia lo stream video per visualizzare i feed in tempo reale.
3. Interagisci con il drone:
   - Manualmente dalla UI (input testuale / bottoni), oppure
   - Vocale (Vosk) per inviare comandi naturali, oppure
   - Automatico: abilita OCR per inviare frame al server e lascia che l'LLM proponga comandi.
4. I comandi vengono accodati nella `Priority Queue`, validati e inviati al drone rispettando i vincoli di sicurezza e i cooldown.

Architettura tecnica (sintesi):
- Frontend: template HTML/JS che richiede lo stream e interagisce con le API Flask per toggle, comandi e log.
- Backend (`app.py`): orchestrazione, stato condiviso thread-safe, generator per MJPEG stream, worker per la coda comandi e polling batteria.
- Moduli core (`drone/`): lettura stream, processamento frame, invio OCR, client Ollama, executor comandi, utility WiFi e media capture.

Considerazioni operative e suggerimenti:
- L'app è progettata per ambiente locale o LAN; evitare di esporre l'endpoint di controllo senza autenticazione e rete protetta.
- Per ambienti di test, usare `OCR_ENABLED=False` per disabilitare chiamate esterne e provare il flusso video/comandi.
- Valuta di containerizzare OCR e Ollama (o usare server dedicati) per semplificare l'ambiente di esecuzione.

---

## Struttura del progetto

```text
TelloDroneAI_v2/
├── app.py                          ← Entry point Flask
├── requirements.txt                ← Dipendenze Python
├── .env                            ← Configurazione
├── README.md                       ← Questa documentazione
│
├── drone/                          ← Package moduli drone
│   ├── __init__.py
│   ├── wifi.py                   ← Connessione WiFi
│   ├── frame_reader.py           ← Acquisizione frame
│   ├── frame_processor.py        ← Pipeline OpenCV
│   ├── media_capture.py          ← Foto e registrazione Video
│   ├── ocr_sender.py             ← Endpoint OCR
│   ├── ollama_client.py          ← Integrazione LLM
│   └── command_executor.py       ← Motore comandi e Priority Queue
│
├── vosk-voice/                     ← Riconoscimento vocale
│   ├── voice_module.py           ← Core Vosk
│   └── model-it/                 ← Modello acustico italiano
│
├── tests/                          ← Test unitari e integrazione
│
├── templates/                      ← Template HTML
│   └── index.html                
│
└── static/                         ← Asset frontend
    ├── css/
    │   └── style.css             
    └── js/
        └── main.js               
```

---

## Note di sviluppo

- **Thread-safety:** L'app usa `threading.Lock()` per proteggere `app_state`. Tutte le operazioni su batteria, flag WiFi/stream, log sono sincronizzate.
- **Configurazione:** Tutti i parametri leggibili sono in `.env`. Non mescolare hardcoding in Python.
- **Modularità:** Il package `drone/` è disaccoppiato da Flask. Ogni modulo ha una responsabilità singola e può essere testato isolatamente.
- **OCR opzionale:** L'OCR è completamente opzionale. Se `OCR_ENABLED=False` o il server non risponde, il drone funziona normalmente.

---

## Riferimenti

- **DJI Tello SDK:** [djitellopy v2.5.0](https://github.com/damiafuentes/DJITelloPy)
- **Flask:** [https://flask.palletsprojects.com/](https://flask.palletsprojects.com/)
- **OpenCV:** [https://opencv.org/](https://opencv.org/)
- **Ollama:** [https://ollama.ai/](https://ollama.ai/)
- **Vosk:** [https://alphacephei.com/vosk/](https://alphacephei.com/vosk/)

---

## Come avviare

```bash
# Attiva il virtualenv
.\venv\Scripts\python.exe app.py

# oppure, se la policy PowerShell lo consente
.\venv\Scripts\Activate.ps1
python app.py
```
