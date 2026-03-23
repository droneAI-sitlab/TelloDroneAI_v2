## Dataset Interpreter Test Tools

Questo set di script Python permette di verificare la corretta interpretazione del dataset Tello inviando ogni entry al modello FunctionGemma come fa l'applicazione principale.

---

## Quick Summary

**Cosa sono?** Tre script temporanei che testano se il dataset viene interpretato correttamente da FunctionGemma.

**Perché?** Verificare che ogni messaggio utente nel dataset venga elaborato e tradotto in comandi drone validi.

**Dataset:** `tello_dataset_FINAL.jsonl` contiene **639 entry** di scambi utente-modello.

**Come funzionano:**
1. Leggono una entry dal dataset (messaggio utente)
2. Inviano il messaggio a FunctionGemma via Ollama
3. Estraggono i comandi dalla risposta
4. Generano un report di successo/fallimento

**Tempi di esecuzione:**
- 10 entry: ~1-2 minuti
- 50 entry: ~5-8 minuti
- 100 entry: ~10-15 minuti
- 639 entry (tutte): ~45-60 minuti

**Risultati:** Eseguendo `test_dataset_interpreter.py --limit 3 --verbose` si vede che funziona:
```
Entry 1: "no" → streamoff [OK]
Entry 2: "fermo" → streamon [OK]
Entry 3: "blocca" → emergency [OK]
```

---

### File generati

1. **`test_dataset_interpreter.py`** - Test completo e dettagliato
2. **`test_dataset_fast.py`** - Test veloce con timeout per evitare blocchi
3. **`count_dataset_entries.py`** - Conta il numero di entry nel dataset

### Dataset Statistics

Il dataset `tello_dataset_FINAL.jsonl` contiene **639 entry**.

Ogni entry rappresenta uno scambio di messaggi con il modello FunctionGemma dove:
- Viene inviato un messaggio testuale dell'utente
- Il modello risponde con comandi di controllo del drone nel formato FunctionGemma
- Gli script estraggono e validano questi comandi

### Utilizzo

#### Test Completo (Dettagliato)

```bash
python test_dataset_interpreter.py [--limit N] [--verbose]
```

Opzioni:
- `--limit N` - Testa solo le prime N entry (default: tutte)
- `--verbose` - Output dettagliato per ogni entry
- `--dataset FILE` - Percorso del dataset (default: tello_dataset_FINAL.jsonl)

Esempio:
```bash
# Testa le prime 10 entry con output verboso
python test_dataset_interpreter.py --limit 10 --verbose

# Testa tutte le 639 entry (può richiedere ore!)
python test_dataset_interpreter.py
```

#### Test Veloce (Con Timeout)

```bash
python test_dataset_fast.py [--limit N] [--timeout SEC]
```

Opzioni:
- `--limit N` - Numero massimo di entry (default: 100)
- `--timeout SEC` - Timeout per ogni richiesta in secondi (default: 30)
- `--dataset FILE` - Percorso del dataset

Esempio:
```bash
# Testa 50 entry con timeout di 60 secondi Ogni richiesta
python test_dataset_fast.py --limit 50 --timeout 60
```

#### Contare le Entry

```bash
python count_dataset_entries.py [file]
```

Esempio:
```bash
python count_dataset_entries.py tello_dataset_FINAL.jsonl
```

### Output

I test generano output nel formato:

```
======================================================
 Test Dataset Interpreter - Tello Dataset
======================================================

Dataset: tello_dataset_FINAL.jsonl
Limite: 50
Verbose: False

[OK] [OK] [OK] [FAIL] [OK] ...

======================================================
 Report Finale
======================================================

Total entries:      50
Successful:         48 (96%)
Failed:             2 (4%)
Total commands:     127

Errori rilevati:
  [3] Errore FunctionGemma: Connection timeout
      Input: decolla veloce...

Status: PASS [OK]
```

### Interpretazione dei Risultati

#### Test Completo

- **[OK]** - Entry elaborata con successo
- **[FAIL]** - Errore durante l'elaborazione
- **[WARN]** - Avviso durante il parsing JSON

#### Test Veloce

- **[OK]** - Success
- **[T/O]** - Timeout (richiesta troppo lenta)
- **[API]** - Errore API (non è stato possibile contattare FunctionGemma)
- **[ERR]** - Altro errore

### Note Importanti

1. **Velocità**: 
   - Ogni richiesta a FunctionGemma richiede alcuni secondi
   - Testare 50 entry richiede ~2-3 minuti
   - Testare tutte le 639 entry richiede ~30-45 minuti

2. **Dipendenze**:
   - Ollama deve essere in esecuzione e raggiungibile via HTTP
   - Il modello FunctionGemma deve essere disponibile in Ollama
   - L'indirizzo e il modello vengono letti da `.env`

3. **Timeout**:
   - Il test fast è utile per identificare entry problematiche rapidamente
   - Il test completo fornisce dettagli completi ma è più lento

4. **Output su Windows**:
   - Gli script usano colori ANSI che potrebbero non essere visibili in alcuni terminali Windows
   - Se vedi caratteri strani, abilita UTF-8 in PowerShell: `chcp 65001`

### Debugging

Se incontri problemi:

1. Verifica che Ollama sia in esecuzione:
   ```bash
   python -c "from drone.ollama_client import ping_ollama_server; print(ping_ollama_server())"
   ```

2. Controlla le variabili d'ambiente in `.env`:
   - `OLLAMA_URL` (default: http://127.0.0.1:11434)
   - `OLLAMA_FUNCTIONGEMMA_MODEL` (default: functiongemma_tello_current)

3. Per log dettagliati, usa `--verbose`:
   ```bash
   python test_dataset_interpreter.py --limit 5 --verbose
   ```
