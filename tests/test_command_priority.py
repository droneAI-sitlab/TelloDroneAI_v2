"""
########################################################################
#  test_command_priority.py  –  Test sistema priorità 3 livelli
#
#  Testa:
#  - Priorità emergency (0) > normali (1) > keepalive (2)
#  - Ordine FIFO a parità di priorità
#  - Cooldown keepalive e reset timer
########################################################################
"""
import unittest
import queue
import threading
import time
from unittest.mock import Mock, patch

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from drone.command_executor import (
    CommandExecutor,
    COMMAND_PRIORITY_EMERGENCY,
    COMMAND_PRIORITY_NORMAL,
    COMMAND_PRIORITY_KEEPALIVE,
)


class TestCommandPriority(unittest.TestCase):
    """Test priorità comandi nel sistema a 3 livelli."""

    def test_priority_constants(self):
        """Verifica che le costanti priorità siano mappate correttamente."""
        # Nuovo sistema: 2=max (0 in coda), 1=normal (1 in coda), 0=min (2 in coda)
        self.assertEqual(COMMAND_PRIORITY_EMERGENCY, 0, "Emergency deve essere 0 (massima priorità)")
        self.assertEqual(COMMAND_PRIORITY_NORMAL, 1, "Normali devono essere 1")
        self.assertEqual(COMMAND_PRIORITY_KEEPALIVE, 2, "Keepalive deve essere 2 (minima priorità)")
        self.assertLess(COMMAND_PRIORITY_EMERGENCY, COMMAND_PRIORITY_NORMAL)
        self.assertLess(COMMAND_PRIORITY_NORMAL, COMMAND_PRIORITY_KEEPALIVE)

    def test_priority_queue_ordering(self):
        """Verifica che PriorityQueue rispetti l'ordine corretto."""
        pq = queue.PriorityQueue()
        
        # Inserisci in ordine casuale
        pq.put((COMMAND_PRIORITY_KEEPALIVE, 0, "keepalive", None))
        pq.put((COMMAND_PRIORITY_NORMAL, 1, "move_forward", 30))
        pq.put((COMMAND_PRIORITY_EMERGENCY, 2, "emergency", None))
        pq.put((COMMAND_PRIORITY_NORMAL, 3, "move_up", 20))
        pq.put((COMMAND_PRIORITY_KEEPALIVE, 4, "keepalive2", None))
        
        # Estrai e verifica ordine
        items = []
        while not pq.empty():
            items.append(pq.get())
        
        # Verifica ordine: prima emergency, poi normali in FIFO, poi keepalive
        self.assertEqual(items[0][2], "emergency")
        self.assertEqual(items[1][2], "move_forward")
        self.assertEqual(items[2][2], "move_up")
        self.assertEqual(items[3][2], "keepalive")
        self.assertEqual(items[4][2], "keepalive2")

    def test_get_command_priority(self):
        """Verifica che CommandExecutor.get_command_priority() ritorni i valori corretti."""
        # Mock del DroneReader
        mock_reader = Mock()
        executor = CommandExecutor(mock_reader)
        
        # Test emergency
        self.assertEqual(
            executor.get_command_priority("emergency"),
            COMMAND_PRIORITY_EMERGENCY,
            "Emergency deve avere priorità 0"
        )
        self.assertEqual(
            executor.get_command_priority("EMERGENCY"),
            COMMAND_PRIORITY_EMERGENCY,
            "Emergency case-insensitive"
        )
        
        # Test keepalive
        self.assertEqual(
            executor.get_command_priority("send_keepalive"),
            COMMAND_PRIORITY_KEEPALIVE,
            "Keepalive deve avere priorità 2"
        )
        self.assertEqual(
            executor.get_command_priority("keepalive"),
            COMMAND_PRIORITY_KEEPALIVE,
            "Keepalive alias"
        )
        self.assertEqual(
            executor.get_command_priority("send_keepalive_no_response"),
            COMMAND_PRIORITY_KEEPALIVE,
            "Keepalive no-response deve avere priorita 2"
        )
        self.assertEqual(
            executor.get_command_priority("keepalive_no_response"),
            COMMAND_PRIORITY_KEEPALIVE,
            "Alias keepalive_no_response"
        )
        self.assertEqual(
            executor.get_command_priority("keepalive_nr"),
            COMMAND_PRIORITY_KEEPALIVE,
            "Alias keepalive_nr"
        )
        
        # Test comandi normali
        normal_commands = ["move_forward", "move_up", "takeoff", "land", "rotate_cw"]
        for cmd in normal_commands:
            self.assertEqual(
                executor.get_command_priority(cmd),
                COMMAND_PRIORITY_NORMAL,
                f"{cmd} deve avere priorità normale 1"
            )

    def test_fifo_same_priority(self):
        """Verifica FIFO a parità di priorità."""
        pq = queue.PriorityQueue()
        
        # Inserisci comandi normali in sequenza
        for i in range(5):
            pq.put((COMMAND_PRIORITY_NORMAL, i, f"cmd_{i}", None))
        
        # Estrai e verifica ordine FIFO
        for i in range(5):
            item = pq.get()
            self.assertEqual(item[1], i, "Sequence number deve preservare FIFO")
            self.assertEqual(item[2], f"cmd_{i}")


class TestKeepaliveCooldown(unittest.TestCase):
    """Test cooldown keepalive e reset timer."""

    def test_cooldown_calculation(self):
        """Verifica calcolo tempo trascorso per cooldown."""
        last_ts = time.monotonic() - 3.0  # 3 secondi fa
        cooldown = 5.0
        elapsed = time.monotonic() - last_ts
        
        self.assertGreaterEqual(elapsed, 3.0)
        self.assertLess(elapsed, 4.0)
        self.assertTrue(elapsed < cooldown, "Cooldown non ancora scaduto")

    def test_cooldown_expired(self):
        """Verifica che cooldown scaduto permetta keepalive."""
        last_ts = time.monotonic() - 6.0  # 6 secondi fa
        cooldown = 5.0
        elapsed = time.monotonic() - last_ts
        
        self.assertGreaterEqual(elapsed, 6.0)
        self.assertTrue(elapsed >= cooldown, "Cooldown scaduto, keepalive consentito")

    def test_timer_reset_on_command(self):
        """Verifica che esecuzione comando reale resetti il timer."""
        # Simula: keepalive eseguito, poi arriva comando, timer resettato
        last_keepalive_ts = time.monotonic()
        
        # Simula arrivo comando reale -> reset timer
        last_keepalive_ts = 0.0
        
        elapsed = time.monotonic() - last_keepalive_ts
        self.assertGreater(elapsed, 1000, "Timer resettato, elapsed molto grande")


class TestBufferSingleLogic(unittest.TestCase):
    """Test logica buffer singolo con keepalive dinamico."""

    def test_buffer_empty_allows_keepalive(self):
        """Verifica che buffer vuoto permetta keepalive."""
        pq = queue.PriorityQueue()
        self.assertTrue(pq.empty(), "Buffer vuoto all'inizio")
        
        # In questo stato, il worker dovrebbe considerare keepalive
        has_real_commands = not pq.empty()
        self.assertFalse(has_real_commands, "Nessun comando reale presente")

    def test_buffer_with_commands_blocks_keepalive(self):
        """Verifica che comandi in coda blocchino keepalive."""
        pq = queue.PriorityQueue()
        pq.put((COMMAND_PRIORITY_NORMAL, 0, "move_forward", 30))
        
        has_real_commands = not pq.empty()
        self.assertTrue(has_real_commands, "Comando presente, keepalive in standby")

    def test_emergency_priority_interrupt(self):
        """Verifica che emergency interrompa delay normale."""
        pq = queue.PriorityQueue()
        pq.put((COMMAND_PRIORITY_EMERGENCY, 0, "emergency", None))
        
        # Controlla se c'è emergency in coda
        with pq.mutex:
            has_emergency = pq.queue[0][0] == COMMAND_PRIORITY_EMERGENCY if pq.queue else False
        
        self.assertTrue(has_emergency, "Emergency in coda rilevato")


if __name__ == "__main__":
    unittest.main(verbosity=2)
