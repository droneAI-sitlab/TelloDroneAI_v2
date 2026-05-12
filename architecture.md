# Architettura TelloDroneAI_v2

Ecco uno schema architetturale completo del progetto `TelloDroneAI_v2` che mostra come i vari componenti comunicano tra loro, dall'interfaccia utente (browser) fino ai servizi di AI (Ollama/OCR) e al drone fisico.

```mermaid
graph TD
    %% ─── STILI E CLASSI ───
    classDef frontend fill:#1e88e5,stroke:#005cb2,stroke-width:2px,color:#fff;
    classDef backend fill:#43a047,stroke:#00712b,stroke-width:2px,color:#fff;
    classDef ai fill:#8e24aa,stroke:#5c007a,stroke-width:2px,color:#fff;
    classDef module fill:#e53935,stroke:#ab000d,stroke-width:2px,color:#fff;
    classDef hardware fill:#fdd835,stroke:#c6a700,stroke-width:2px,color:#000;

    %% ─── COMPONENTI FRONTEND ───
    subgraph Browser ["Web Dashboard (Frontend)"]
        UI["Interfaccia HTML/JS<br>Controlli e Telemetria"]:::frontend
        Video Feed["Stream Video<br>(Canvas / Img)"]:::frontend
        Chat["Chat Comandi Testuali"]:::frontend
        VoiceUI["Registrazione Mic<br>Comandi Vocali"]:::frontend
    end

    %% ─── COMPONENTI BACKEND (Flask) ───
    subgraph AppServer ["Server Flask (app.py)"]
        Router["Gestore Route API<br>(POST/GET)"]:::backend
        State[("Stato Globale Thread-Safe<br>(Batteria, Log, ecc...)")]:::backend
        Buffer["Coda Comandi"]:::backend
    end

    %% ─── MODULO VOCALE (Vosk) ───
    Vosk["Vosk Voice Module<br>(Riconoscimento Offline)"]:::ai

    %% ─── MODULO DRONE (Logica SDK) ───
    subgraph DroneModule ["Logica Drone (cartella /drone)"]
        Wifi["Modulo WiFi<br>(Configura rete)"]:::module
        CommandExec["Command Executor<br>(Valida e accoda comandi)"]:::module
        FrameReader["Frame Reader<br>(Wrapper djitellopy)"]:::module
        FrameProc["Frame Processor<br>(OpenCV pipeline)"]:::module
        OllamaClient["Ollama Client<br>(Parser NLP -> Comandi)"]:::module
        OCRSender["OCR Sender<br>(Invio immagini)"]:::module
    end

    %% ─── SERVER AI ESTERNI ───
    subgraph ServiziAI ["Modelli IA"]
        OllamaSrv["Ollama (FunctionGemma)<br>(Locale/Rete)"]:::ai
        RestOCR["Server RestOCR<br>(Remoto)"]:::ai
    end

    %% ─── HARDWARE DRONE ───
    Tello[["Drone Tello (Fisico)"]]:::hardware

    %% ─── RELAZIONI E FLUSSI D'INFORMAZIONE ───
    
    %% Flussi di Input Utente
    UI -- "Polling Stato" --> Router
    Chat -- "Testo Naturale/Esatto" --> Router
    VoiceUI -- "Audio Blob" --> Vosk
    Vosk -- "Testo Trascritto" --> Router
    
    %% Flusso NLP (Natural Language Processing)
    Router -- "Se NLP attivo, passa testo" --> OllamaClient
    OllamaClient -- "Prompt con Tools" --> OllamaSrv
    OllamaSrv -- "JSON Comandi Canonici" --> OllamaClient
    OllamaClient -- "Invia comandi parsati" --> Buffer
    Router -- "Se esatto, bypassa IA" --> Buffer
    Buffer --> CommandExec

    %% Interazione con il Drone
    Router -- "Attiva Connessione" --> Wifi
    Wifi -- "Connette Rete" --> Tello
    CommandExec -- "Comandi SDK (takeoff, move...)" --> FrameReader
    FrameReader -- "Comandi UDP" --> Tello
    Tello -- "Stato / Telemetria" --> State

    %% Flusso Video
    Tello -- "Video Stream UDP<br>(H264)" --> FrameReader
    FrameReader -- "Frame Grezzo" --> FrameProc
    FrameProc -- "Applica Contrasto/Resize" --> Router
    Router -- "MJPEG Stream" --> Video Feed
    
    %% Flusso OCR (Lettura etichette dal video)
    FrameProc -- "Frame Intermittente" --> OCRSender
    OCRSender -- "Foto JPEG" --> RestOCR
    RestOCR -- "Testo Letto" --> OCRSender
    OCRSender -- "Testo estratto" --> OllamaClient
```

### Spiegazione dei Blocchi Principali

1. **Il Frontend (Blu):** È l'interfaccia dell'utente che interagisce con i controlli manuali, scatta foto, manda input testuali e, via microfono, comandi vocali. Mostra in tempo reale il video renderizzato e la diagnostica ritornata.
2. **Il Server Flask (Verde):** `app.py` processa l'input HTTP/API. Gestisce uno stato applicativo centralizzato in memoria per tenere traccia se è connesso, se sta trasmettendo e il livello di batteria senza dover costantemente interrogare il drone e interfacciandosi con thread indipendenti (come quello per lo svuotamento o ritardo buffer dei comandi). 
3. **L'Intelligenza Artificiale (Viola):**
   * **Vosk** riceve l'audio del browser e genera stringhe (Testo).
   * **Ollama Client** prende le frasi digitate (o quelle parlate da Vosk) e le manda al tuo modello _FunctionGemma_ per tradurre il linguaggio naturale in istruzioni drone parsabili strutturate col formato corretto.
   * **OCR Sender** prende le immagini lette e le passa al server di `RestOCR`, il testo poi viene analizzato e rimandato, ad esempio, all'Ollama Client qualora si aspettino dei comandi che provengano dai cartelli/testi letti in camera.
4. **Il Modulo Drone (Rosso):** È il ponte verso l'hardware:
   * **Command Executor** preleva periodicamente la **Coda di Comandi** generata dalla chat o dall'AI e la esegue sul drone reale (con priorità) evitando un intasamento dei buffer.
   * **Frame Reader/Processor** sfrutta `djitellopy` e `OpenCV` per prendere lo stream H264 UDP del drone, convertilo, migliorarlo (contrasti) per poi distribuirlo sia all'utente (MJPEG) sia all'OCR.