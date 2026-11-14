"""Offline regression tests: no network connections or drone commands."""
import importlib
import queue
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np
from dotenv import dotenv_values
from drone.command_executor import CommandExecutor
from drone.frame_processor import FrameProcessor
from drone.frame_reader import DroneReader
from drone.media_capture import DroneMediaCapture
from drone.ocr_sender import OCRSender
from drone.ollama_client import parse_model_output_to_executor_commands
from drone import wifi


class ExecutorTests(unittest.TestCase):
    def setUp(self):
        self.reader = Mock()
        self.media = Mock()
        self.executor = CommandExecutor(self.reader, self.media)

    def test_invalid_arguments_never_reach_sdk(self):
        for value in ('bad', [], {}, True, 30.5, float('inf'), float('nan')):
            with self.subTest(value=value):
                self.assertFalse(self.executor.run('avanti', value)[0])
        self.reader.get_tello.return_value.move_forward.assert_not_called()

    def test_ocr_does_not_strip_minus_or_truncate_distance(self):
        for text in ("avanti -30", "avanti 1000", "avanti 30.5"):
            self.assertFalse(self.executor.run_from_text(text)[0])
        self.reader.get_tello.return_value.move_forward.assert_not_called()

    def test_limits_and_aliases(self):
        self.assertEqual(self.executor.validate('AVANTI', '20')[2:], ('move_forward', 20))
        for value in (19, 501, -30):
            self.assertFalse(self.executor.validate('avanti', value)[0])
        self.assertFalse(self.executor.validate(None)[0])

    def test_media_commands_are_reachable(self):
        for name in ('take_photo', 'start_video_recording', 'stop_video_recording'):
            self.assertTrue(self.executor.run(name)[0])
            getattr(self.media, name).assert_called_once()

    def test_battery_fallback_registered(self):
        self.reader.get_tello.return_value.get_battery.return_value = 78
        self.assertEqual(self.executor.run('get_battery'), (True, 'ok – get_battery 78%'))

    def test_model_invalid_values_are_not_default_moves(self):
        for value in ('bad', '30.5', 'true', 'Infinity'):
            self.assertEqual(parse_model_output_to_executor_commands(f'call:move_forward{{cm:{value}}}'), [])

    def test_model_multiple_calls_preserve_order(self):
        self.assertEqual(parse_model_output_to_executor_commands('call:move_forward{cm:30}\ncall:land{}'), [('move_forward', 30), ('land', None)])


class MediaTests(unittest.TestCase):
    def test_frame_is_writable_and_independent(self):
        frame = np.zeros((2, 2, 3), dtype=np.uint8)
        frame.flags.writeable = False
        out = FrameProcessor(2, 2, enable_contrast=False).process_to_frame(frame)
        out[0, 0, 0] = 255
        self.assertEqual(frame[0, 0, 0], 0)
        self.assertTrue(out.flags.c_contiguous)

    def test_connection_applies_configured_ports(self):
        reader = DroneReader('unused', 11112, 9999)
        with patch('drone.frame_reader.Tello') as tello:
            tello.return_value.vs_udp_port = 11111
            self.assertTrue(reader.connect_drone())
            self.assertEqual(tello.return_value.address, ('unused', 9999))
            tello.return_value.change_vs_udp.assert_called_once_with(11112)

    def test_stop_clears_sdk_reader_cache(self):
        reader = DroneReader('unused', 11111)
        reader._tello = Mock()
        old = reader._frame_read = Mock()
        reader._tello.background_frame_read = old
        reader.stop_stream()
        old.stop.assert_called_once()
        self.assertIsNone(reader._tello.background_frame_read)

    def test_rgb_from_sdk_is_converted_to_bgr(self):
        reader = DroneReader('unused', 11111)
        reader._stream_ready = True
        reader._frame_read = Mock(frame=np.array([[[255, 0, 0]]], dtype=np.uint8))
        self.assertEqual(reader.get_frame().tolist(), [[[0, 0, 255]]])

    def test_recorder_releases_writer_on_processing_error(self):
        capture = DroneMediaCapture(Mock(), Mock())
        writer = capture._video_writer = Mock()
        capture._processor.process_to_frame.side_effect = RuntimeError('decoder error')
        with self.assertRaises(RuntimeError):
            capture._recording_loop()
        writer.release.assert_called_once()
        self.assertIsNone(capture._video_writer)

    def test_timed_out_stop_does_not_release_live_writer(self):
        capture = DroneMediaCapture(Mock(), Mock())
        capture._recording_thread = Mock()
        writer = capture._video_writer = Mock()
        with self.assertRaises(RuntimeError):
            capture.stop_video_recording()
        writer.release.assert_not_called()
        self.assertTrue(capture._stop_event.is_set())

    def test_capture_names_do_not_collide_within_second(self):
        capture = DroneMediaCapture(Mock(), Mock())
        self.assertNotEqual(capture._build_output_path('photo', 'jpg'), capture._build_output_path('photo', 'jpg'))


class OCRTests(unittest.TestCase):
    def setUp(self):
        with patch('threading.Thread.start'):
            self.sender = OCRSender()

    def test_results_keep_reading_order_and_ignore_bad_entries(self):
        result = self.sender._filter_results([
            {'text': 'avanti', 'confidence': .7},
            {'text': '30', 'confidence': '0.9'},
            {'text': 'bad', 'confidence': None},
            {'text': 'bad', 'confidence': float('nan')},
            {'text': 'bad', 'confidence': 2}, None,
        ])
        self.assertEqual([item['text'] for item in result], ['avanti', '30'])
        self.assertEqual(len(self.sender._format_results(result)), 2)

    def test_results_consumed_once_across_viewers(self):
        self.sender.last_words = ['land']
        self.sender.last_result_id = 1
        self.assertEqual(self.sender.consume_words(), ['land'])
        self.assertEqual(self.sender.consume_words(), [])

    def test_replaced_frames_have_balanced_queue_tasks(self):
        self.sender._interval = 0
        frame = np.zeros((2, 2, 3), dtype=np.uint8)
        self.sender.send_frame(frame)
        self.sender.send_frame(frame)
        self.assertEqual(self.sender._frame_queue.qsize(), 1)
        self.assertEqual(self.sender._frame_queue.unfinished_tasks, 1)


class AppTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Do not start SDK/background services or load a large voice model.
        with patch('threading.Thread.start'), patch('atexit.register'), patch('vosk.Model'):
            cls.module = importlib.import_module('app')
        cls.module.app.testing = True

    def setUp(self):
        self.client = self.module.app.test_client()
        self.module._drain_command_buffer()

    def test_readonly_routes_render(self):
        for path in ('/', '/api/status', '/api/commands', '/api/config', '/api/logs', '/api/media/status'):
            self.assertEqual(self.client.get(path).status_code, 200, path)

    def test_non_object_json_rejected(self):
        for value in ([], ['x'], 'text', 42, False):
            self.assertEqual(self.client.post('/api/command', json=value).status_code, 400)
        self.assertEqual(self.client.post('/api/command', data='{bad', content_type='application/json').status_code, 400)

    def test_bad_command_rejected_before_queue(self):
        for payload in ({'command': 'unknown'}, {'command': 'avanti', 'argument': 3.5}, {'command': 'avanti', 'argument': 501}):
            self.assertEqual(self.client.post('/api/command', json=payload).status_code, 400)
        self.assertTrue(self.module.command_buffer.empty())

    def test_chat_emergency_bypasses_full_queue_and_discards_batch_tail(self):
        with patch.object(self.module, 'command_buffer', queue.PriorityQueue(maxsize=1)):
            self.module.enqueue_executor_commands([('avanti', 30)], 'web')
            with patch.object(self.module.threading, 'Thread') as thread:
                self.assertEqual(self.module.enqueue_executor_commands([('stop', None), ('takeoff', None)], 'chat'), 1)
                thread.return_value.start.assert_called_once()
            self.assertTrue(self.module.command_buffer.empty())

    def test_alias_takeoff_obeys_parser_lock(self):
        with patch.dict(self.module.app_state, allow_parser_takeoff=True):
            self.assertEqual(self.module.enqueue_executor_commands([('decolla', None)], 'ocr'), 0)

    def test_alias_emergency_clears_pending_commands(self):
        self.module.enqueue_executor_commands([('avanti', 30)], 'web')
        with patch.object(self.module.threading, 'Thread') as thread:
            result = self.client.post('/api/command', json={'command': 'stop'})
            self.assertTrue(result.json['success'])
            thread.return_value.start.assert_called_once()
        self.assertTrue(self.module.command_buffer.empty())

    def test_config_rejects_injection_and_invalid_numbers(self):
        for config in ({'TARGET_FPS': 'nan'}, {'COMMAND_BUFFER_MAX_SIZE': 0}, {'OCR_TIMEOUT': -1}, {'MEDIA_VIDEO_CODEC': 'bad'}, {'BAD\nKEY': 'x'}, {'OLLAMA_MODEL': 'x\nFLASK_DEBUG=True'}):
            self.assertEqual(self.client.post('/api/config', json={'config': config}).status_code, 400)

    def test_integer_config_is_serialized_for_startup(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(self.module, '__file__', str(Path(directory) / 'app.py')), patch.object(self.module, '_reload_runtime_config', return_value=([], [])):
                response = self.client.post('/api/config', json={'config': {'FRAME_WIDTH': '960.0'}})
                self.assertTrue(response.json['success'])
                self.assertEqual(int(dotenv_values(Path(directory) / '.env')['FRAME_WIDTH']), 960)

    def test_config_roundtrip_quotes_and_hash(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(self.module, '__file__', str(Path(directory) / 'app.py')), patch.object(self.module, '_reload_runtime_config', return_value=([], [])):
                value = "model's #1"
                self.assertTrue(self.client.post('/api/config', json={'config': {'OLLAMA_MODEL': value}}).json['success'])
                self.assertEqual(dotenv_values(Path(directory) / '.env')['OLLAMA_MODEL'], value)
                self.assertEqual(self.client.get('/api/config').json['config']['OLLAMA_MODEL'], value)

    def test_worker_does_not_block_for_keepalive_cooldown(self):
        class StopWorker(BaseException):
            pass
        module = self.module
        with patch.object(module.drone_reader, 'get_tello', return_value=Mock()), patch.object(module.command_executor, 'is_flying', return_value=True), patch.object(module.command_executor, 'run', return_value=(True, 'ok')), patch.object(module.time, 'sleep', side_effect=StopWorker) as sleep:
            module._last_keepalive_ts = 0
            with self.assertRaises(StopWorker):
                module._command_buffer_worker()
            self.assertLessEqual(sleep.call_args.args[0], .1)


class WifiTests(unittest.TestCase):
    def test_ssid_xml_escaped(self):
        def inspect(command):
            import xml.etree.ElementTree as ET
            path = command[-1].split('=', 1)[1]
            tree = ET.parse(path)
            self.assertEqual(tree.getroot()[0].text, 'TELLO<&')
            return Mock(returncode=0)
        with patch.object(wifi, '_run', side_effect=inspect):
            self.assertTrue(wifi._ensure_open_profile('TELLO<&'))

    def test_profile_failure_does_not_disconnect(self):
        with patch.object(wifi, 'is_connected_to', return_value=False), patch.object(wifi, '_is_windows', return_value=True), patch.object(wifi, '_get_wifi_interface', return_value='Wi-Fi'), patch.object(wifi, '_ensure_open_profile', return_value=False), patch.object(wifi, '_run') as run:
            self.assertFalse(wifi.connect('TELLO'))
            run.assert_not_called()
