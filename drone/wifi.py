"""
########################################################################
#  drone/wifi.py  -  WiFi connection utilities
#
#  Gestisce la connessione OS-level alla rete WiFi del drone.
#  Solo Windows: usa i comandi 'netsh wlan'.
#
#  FLUSSO:
#    1. _get_wifi_interface()  -> rileva il nome dell'adattatore WiFi
#    2. _ensure_open_profile() -> crea/aggiorna profilo XML per rete
#                                 aperta (Tello non usa password)
#    3. connect()              -> disconnette, aggiunge il profilo,
#                                 si connette e fa polling SSID
########################################################################
"""

import os
import platform
import subprocess
import tempfile
import time


########################################################################
#  HELPERS INTERNI
########################################################################

def _is_windows() -> bool:
    return platform.system() == "Windows"


def _run(cmd: list, timeout: int = 8) -> subprocess.CompletedProcess:
    """
    Esegue un comando come sottoprocesso.
    encoding=utf-8 + errors=replace gestisce output misti su Windows italiani.
    """
    return subprocess.run(
        cmd,
        capture_output=True,
        timeout=timeout,
        encoding="utf-8",
        errors="replace",
    )


def _get_wifi_interface() -> str | None:
    """
    Rileva il nome del primo adattatore WiFi attivo.

    Strategia language-agnostic: la prima riga indentata con ':'
    nell'output di 'netsh wlan show interfaces' contiene sempre il
    nome dell'interfaccia (es. 'Wi-Fi'), indipendentemente dalla
    lingua del sistema operativo (italiano: 'Nome', inglese: 'Name').

    Returns:
        str  - nome interfaccia (es. 'Wi-Fi')
        None - nessun adattatore trovato o errore
    """
    try:
        result = _run(["netsh", "wlan", "show", "interfaces"])
        for line in result.stdout.splitlines():
            # La prima riga con rientro (4+ spazi) e ':' e' sempre il nome
            if line.startswith("    ") and ":" in line:
                value = line.split(":", 1)[1].strip()
                if value:
                    return value
    except Exception as exc:
        print(f"[wifi] Errore _get_wifi_interface: {exc}")
    return None


def _ensure_open_profile(ssid: str) -> bool:
    """
    Crea (o aggiorna) un profilo Windows per una rete WiFi aperta senza password.

    Il Tello usa sempre reti aperte. 'netsh wlan connect' richiede che il
    profilo esista gia in Windows: questa funzione lo genera da un template
    XML e lo installa, cosi la connessione funziona anche al primo utilizzo,
    senza dover configurare nulla manualmente.

    Args:
        ssid: SSID del drone

    Returns:
        True se il profilo e stato aggiunto con successo
    """
    # Template XML per rete aperta (authEncryption: open/none)
    profile_xml = (
        '<?xml version="1.0"?>\n'
        '<WLANProfile xmlns="http://www.microsoft.com/networking/WLAN/profile/v1">\n'
        f'    <name>{ssid}</name>\n'
        '    <SSIDConfig>\n'
        '        <SSID>\n'
        f'            <name>{ssid}</name>\n'
        '        </SSID>\n'
        '        <nonBroadcast>false</nonBroadcast>\n'
        '    </SSIDConfig>\n'
        '    <connectionType>ESS</connectionType>\n'
        '    <connectionMode>manual</connectionMode>\n'
        '    <MSM>\n'
        '        <security>\n'
        '            <authEncryption>\n'
        '                <authentication>open</authentication>\n'
        '                <encryption>none</encryption>\n'
        '                <useOneX>false</useOneX>\n'
        '            </authEncryption>\n'
        '        </security>\n'
        '    </MSM>\n'
        '</WLANProfile>\n'
    )

    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            suffix=".xml", mode="w", delete=False, encoding="utf-8"
        ) as tmp:
            tmp.write(profile_xml)
            tmp_path = tmp.name

        result = _run(["netsh", "wlan", "add", "profile", f"filename={tmp_path}"])
        if result.returncode != 0:
            msg = result.stdout.strip() or result.stderr.strip()
            print(f"[wifi] Avviso add profile: {msg}")
        return result.returncode == 0

    except Exception as exc:
        print(f"[wifi] Errore _ensure_open_profile: {exc}")
        return False
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)


########################################################################
#  FUNZIONI PUBBLICHE
########################################################################

def get_current_ssid() -> str | None:
    """
    Legge l'SSID della rete WiFi attualmente connessa.

    Returns:
        str  - SSID della rete attiva
        None - non connesso o errore
    """
    if not _is_windows():
        return None

    try:
        result = _run(["netsh", "wlan", "show", "interfaces"])
        for line in result.stdout.splitlines():
            stripped = line.strip()
            # Formato: "SSID                   : TELLO-XXXXXX"
            # Escludi la riga BSSID (MAC address)
            if stripped.upper().startswith("SSID") and "BSSID" not in stripped.upper():
                parts = stripped.split(":", 1)
                if len(parts) == 2:
                    return parts[1].strip()
    except Exception as exc:
        print(f"[wifi] Errore get_current_ssid: {exc}")

    return None


def is_connected_to(ssid: str) -> bool:
    """
    Verifica se il PC e gia connesso all'SSID specificato.

    Args:
        ssid: SSID da verificare (es. 'TELLO-9C50BA')

    Returns:
        True se l'SSID attivo corrisponde esattamente
    """
    current = get_current_ssid()
    return current is not None and current == ssid


def connect(ssid: str, timeout: int = 15) -> bool:
    """
    Connette il PC alla rete WiFi del drone.

    Passi eseguiti:
      1. Se gia connesso -> ritorna True subito
      2. Rileva interfaccia WiFi attiva
      3. Crea/aggiorna il profilo Windows per rete aperta Tello
      4. Disconnette dalla rete corrente
      5. Lancia 'netsh wlan connect' con nome interfaccia esplicito
      6. Polling fino a connessione confermata o timeout

    Args:
        ssid:    SSID del drone (deve corrispondere a DRONE_WIFI_SSID in .env)
        timeout: Secondi massimi di attesa

    Returns:
        True  - connessione riuscita
        False - timeout o errore
    """
    # -- 1. Gia connessi? -----------------------------------------------
    if is_connected_to(ssid):
        print(f"[wifi] Gia connesso a '{ssid}'")
        return True

    if not _is_windows():
        print("[wifi] Connessione automatica supportata solo su Windows")
        return False

    print(f"[wifi] Avvio connessione a '{ssid}' ...")

    # -- 2. Rileva interfaccia WiFi -------------------------------------
    interface = _get_wifi_interface()
    if interface is None:
        # Fallback: procedi senza specificare l'interfaccia (funziona se
        # il PC ha un solo adattatore WiFi, che e' il caso piu comune)
        print("[wifi] Adattatore non rilevato, procedo senza interface=")
    else:
        print(f"[wifi] Adattatore WiFi: '{interface}'")

    # -- 3. Installa profilo per rete aperta (Tello) -------------------
    _ensure_open_profile(ssid)

    try:
        # -- 4. Disconnetti dalla rete corrente ------------------------
        disc_cmd = ["netsh", "wlan", "disconnect"]
        if interface:
            disc_cmd.append(f"interface={interface}")
        _run(disc_cmd)
        time.sleep(1)

        # -- 5. Avvia connessione --------------------------------------
        conn_cmd = ["netsh", "wlan", "connect", f"name={ssid}"]
        if interface:
            conn_cmd.append(f"interface={interface}")
        result = _run(conn_cmd)
        out = result.stdout.strip() or "(nessun output)"
        print(f"[wifi] netsh connect: {out}")

        # -- 6. Polling finche connesso o timeout ----------------------
        deadline = time.time() + timeout
        while time.time() < deadline:
            if is_connected_to(ssid):
                print(f"[wifi] Connesso a '{ssid}'")
                return True
            time.sleep(1)

        current = get_current_ssid()
        print(f"[wifi] Timeout ({timeout}s) - SSID corrente: '{current}'")
        return False

    except Exception as exc:
        print(f"[wifi] Errore durante connect: {exc}")
        return False


def disconnect(target_ssid: str | None = None, timeout: int = 8) -> bool:
    """
    Disconnette l'adattatore WiFi dalla rete corrente.

    Args:
        target_ssid: se valorizzato, disconnette solo se connessi a quell'SSID.
        timeout: secondi massimi per confermare il distacco.

    Returns:
        True se la disconnessione risulta completata o non necessaria.
    """
    if not _is_windows():
        print("[wifi] Disconnessione automatica supportata solo su Windows")
        return False

    current = get_current_ssid()
    if target_ssid and current != target_ssid:
        print(
            "[wifi] Nessuna disconnessione necessaria "
            f"(SSID corrente: '{current}', target: '{target_ssid}')"
        )
        return True

    interface = _get_wifi_interface()

    try:
        cmd = ["netsh", "wlan", "disconnect"]
        if interface:
            cmd.append(f"interface={interface}")

        result = _run(cmd)
        out = result.stdout.strip() or result.stderr.strip() or "(nessun output)"
        print(f"[wifi] netsh disconnect: {out}")

        deadline = time.time() + max(1, timeout)
        while time.time() < deadline:
            current = get_current_ssid()
            if current is None:
                return True
            if target_ssid is not None and current != target_ssid:
                return True
            time.sleep(0.3)

        current = get_current_ssid()
        print(f"[wifi] Timeout disconnessione ({timeout}s) - SSID corrente: '{current}'")
        return False

    except Exception as exc:
        print(f"[wifi] Errore durante disconnect: {exc}")
        return False
