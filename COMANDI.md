ssari# Riferimento Comandi TelloDroneAI

Questa tabella elenca tutti i comandi supportati dal sistema. Quando `FUNCTIONGEMMA_ENABLED=false`, usare esclusivamente questi comandi (case-insensitive).

> **Nota:** Gli argomenti tra parentesi quadre `[...]` sono opzionali. Se omessi, viene usato il valore di default.

---

## Comandi di Volo

### Decollo / Atterraggio

| Comando (EN) | Alias (IT) | Argomento | Default | Range | Descrizione |
|--------------|------------|-----------|---------|-------|-------------|
| `takeoff` | `decolla` | - | - | - | Decolla |
| `land` | `atterra` | - | - | - | Atterra |
| `emergency` | `emergenza`, `stop` | - | - | - | Arresto motori immediato (emergenza) |

---

### Movimenti Traslationali

| Comando (EN) | Alias (IT) | Argomento | Default | Range | Descrizione |
|--------------|------------|-----------|---------|-------|-------------|
| `move_forward` | `avanti`, `forward` | `[distanza]` | 30 cm | 20-500 cm | Avanza |
| `move_back` | `indietro`, `dietro`, `back`, `backward` | `[distanza]` | 30 cm | 20-500 cm | Arretra |
| `move_left` | `sinistra`, `left` | `[distanza]` | 30 cm | 20-500 cm | Trasla a sinistra |
| `move_right` | `destra`, `right` | `[distanza]` | 30 cm | 20-500 cm | Trasla a destra |
| `move_up` | `su`, `sali`, `up` | `[distanza]` | 30 cm | 20-500 cm | Sali |
| `move_down` | `giu`, `scendi`, `down` | `[distanza]` | 30 cm | 20-500 cm | Scendi |

---

### Rotazioni

| Comando (EN) | Alias (IT) | Argomento | Default | Range | Descrizione |
|--------------|------------|-----------|---------|-------|-------------|
| `rotate_cw` | `ruota_destra` | `[angolo]` | 90° | 1-360° | Ruota in senso orario |
| `rotate_ccw` | `ruota_sinistra` | `[angolo]` | 90° | 1-360° | Ruota in senso antiorario |

---

### Capriole (Flip)

| Comando (EN) | Alias | Argomento | Descrizione |
|--------------|-------|-----------|-------------|
| `flip_forward` | - | - | Capriola in avanti |
| `flip_back` | - | - | Capriola indietro |
| `flip_left` | - | - | Capriola a sinistra |
| `flip_right` | - | - | Capriola a destra |

---

### Velocità

| Comando (EN) | Alias (IT) | Argomento | Default | Range | Descrizione |
|--------------|------------|-----------|---------|-------|-------------|
| `set_speed` | `velocita` | `[velocita]` | 30 cm/s | 10-100 cm/s | Imposta velocità massima |

---

## Comandi di Telemetria

| Comando (EN) | Alias | Argomento | Descrizione |
|--------------|-------|-----------|-------------|
| `send_keepalive` | `keepalive` | - | Invia heartbeat per mantenere la sessione attiva |
| `send_keepalive_no_response` | `keepalive_no_response`, `keepalive_nr` | - | Invia heartbeat senza attendere risposta SDK |
| `get_battery` | `battery` | - | Legge la batteria del drone (0-100%) |

---

## Comandi Media

| Comando (EN) | Alias (IT) | Argomento | Descrizione |
|--------------|------------|-----------|-------------|
| `take_photo` | `foto`, `photo`, `scatta_foto` | - | Scatta una foto dalla camera del drone |
| `start_video_recording` | `avvia_video`, `start_video` | - | Avvia registrazione video |
| `stop_video_recording` | `stop_video`, `ferma_video` | - | Ferma registrazione video e salva il file |

---

## Esempi di Utilizzo

### Modalità FUNCTIONGEMMA_ENABLED=false (comandi diretti)

```bash
# Decollo
takeoff
decolla          # alias italiano

# Movimento con argomento
move_forward 50
avanti 50        # alias italiano

# Movimento con default (30 cm)
move_forward
avanti           # default: 30 cm

# Rotazione
rotate_cw 45
ruota_destra 45  # alias italiano

# Atterraggio
land
atterra          # alias italiano

# Foto
take_photo
foto             # alias italiano
```

### Modalità FUNCTIONGEMMA_ENABLED=true (interpretazione AI)

Con FunctionGemma abilitato, puoi scrivere comandi in linguaggio naturale:
- "fai decollare il drone"
- "vai avanti di 50 centimetri"
- "ruota a destra"
- "scatta una foto"

---

## Limiti SDK Tello

| Tipo | Minimo | Massimo | Unità |
|------|--------|---------|-------|
| Movimento (forward/back/left/right/up/down) | 20 | 500 | cm |
| Rotazione (cw/ccw) | 1 | 360 | gradi |
| Velocità | 10 | 100 | cm/s |

---

## Note Tecniche

- Tutti i comandi sono **case-insensitive** (es: `TAKEOFF`, `takeoff`, `Takeoff` sono equivalenti)
- Gli alias possono contenere spazi o underscore (es: `move forward`, `move_forward` → `move_forward`)
- I comandi vengono eseguiti in sequenza tramite un buffer FIFO con delay configurabile
- Per il controllo preciso del drone, usare i nomi canonici (colonna "Comando (EN)")