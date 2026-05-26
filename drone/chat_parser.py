import re
from typing import Optional, Tuple

def parse_direct_command(message: str) -> Tuple[Optional[str], Optional[int]]:
    """
    Tenta di interpretare un messaggio di testo libero come un comando diretto per il drone.

    Cerca uno schema del tipo "comando [argomento numerico]".

    Args:
        message: La stringa digitata dall'utente (già sanificata).

    Returns:
        (command_name, argument) se il messaggio fa match con la regex, altrimenti (None, None).
    """
    match = re.match(r"^(.*?)(?:\s+(-?\d+))?$", message)
    if not match:
        return None, None

    direct_command = match.group(1).strip()
    direct_arg_raw = match.group(2)
    direct_argument = int(direct_arg_raw) if direct_arg_raw is not None else None

    return direct_command, direct_argument
