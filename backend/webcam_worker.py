"""One local OpenCV worker, controlled by FastAPI without owning its camera."""

import errno
import json
import os
import subprocess
import threading
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
ACTIVE_STATES = {'starting', 'running', 'stopping'}


def _windows_pid_state(pid):
    import ctypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = (ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong)
    kernel.OpenProcess.restype = ctypes.c_void_p
    kernel.GetExitCodeProcess.argtypes = (ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong))
    kernel.GetExitCodeProcess.restype = ctypes.c_int
    kernel.CloseHandle.argtypes = (ctypes.c_void_p,)
    handle = kernel.OpenProcess(0x1000, False, pid)
    if not handle:
        return 'gone' if ctypes.get_last_error() == 87 else 'unknown'
    exit_code = ctypes.c_ulong()
    try:
        if not kernel.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
            return 'unknown'
        return 'alive' if exit_code.value == 259 else 'gone'
    finally:
        kernel.CloseHandle(handle)


def _pid_alive(pid):
    if not isinstance(pid, int) or pid <= 0:
        return False
    if os.name == 'nt':
        return _windows_pid_state(pid) == 'alive'
    try:
        os.kill(pid, 0)
    except (OSError, SystemError):
        return False
    return True


def _pid_confirmed_gone(pid):
    """Only allow replacing recovered IPC state when the old PID is known to be gone."""
    if not isinstance(pid, int) or pid <= 0:
        return False
    if os.name == 'nt':
        return _windows_pid_state(pid) == 'gone'
    try:
        os.kill(pid, 0)
    except OSError as exc:
        return exc.errno == errno.ESRCH
    return False


class WebcamWorker:
    def __init__(self, root=ROOT, process_factory=subprocess.Popen, state_dir=None):
        self.root = Path(root)
        self.process_factory = process_factory
        self.state_dir = Path(state_dir or self.root / 'data' / 'webcam-worker-ipc')
        self.lock = threading.RLock()
        self.process = None
        self.directory = self.state_dir if (self.state_dir / 'status.json').exists() else None
        self.log_handle = None

    def _read_status(self):
        if self.directory is None:
            return {'state': 'idle'}
        try:
            data = json.loads((self.directory / 'status.json').read_text(encoding='utf-8'))
            return data if isinstance(data, dict) else {'state': 'error', 'message': 'Invalid worker status.'}
        except (FileNotFoundError, ValueError, OSError):
            return {'state': 'starting'}

    def _failure_message(self, exit_code):
        code = f' with code {exit_code}' if exit_code is not None else ''
        message = f'Webcam worker exited{code} before completing.'
        try:
            with open(self.directory / 'worker.log', 'rb') as handle:
                handle.seek(0, os.SEEK_END)
                handle.seek(max(0, handle.tell() - 8192))
                lines = handle.read().decode('utf-8', errors='replace').splitlines()
            errors = [line.strip() for line in lines if line.lstrip().startswith(
                ('PermissionError:', 'FileNotFoundError:', 'RuntimeError:',
                 'ModuleNotFoundError:', 'ImportError:', 'OSError:', 'ValueError:',
                 'EventJournalError:'))]
            if errors:
                message += f' {errors[-1]}'
        except OSError:
            pass
        return message

    def status(self):
        with self.lock:
            state = self._read_status()
            if self.directory is None:
                return state
            pid = getattr(self.process, 'pid', None) or state.get('pid')
            if pid:
                state['pid'] = pid
            exit_code = self.process.poll() if self.process is not None else None
            if self.process is not None and exit_code is not None:
                self._close_log()
                if state.get('state') == 'completed':
                    return {**state, 'state': 'stopped'}
                if state.get('state') != 'error':
                    return {**state, 'state': 'error', 'message': self._failure_message(exit_code)}
                return state
            if self.process is None and state.get('state') != 'idle':
                try:
                    age = (datetime.now(timezone.utc) - datetime.fromisoformat(state.get('updatedAt'))).total_seconds()
                except (TypeError, ValueError):
                    age = float('inf')
                alive = _pid_alive(pid)
                if state.get('state') == 'completed' and not alive:
                    return {**state, 'state': 'stopped'}
                if not alive and _pid_confirmed_gone(pid):
                    return {**state, 'state': 'error',
                            'message': f'Webcam worker PID {pid} exited. ' + self._failure_message(None)}
                if not alive or age > 15:
                    return {**state, 'state': 'error',
                            'message': f'Cannot confirm webcam worker PID {pid}; camera ownership is uncertain.'}
            if (self.directory / 'stop').exists() and state.get('state') in ACTIVE_STATES | {'completed'}:
                return {**state, 'state': 'stopping'}
            if state.get('state') == 'completed':
                return {**state, 'state': 'stopping'}
            return state

    def _close_log(self):
        if self.log_handle is not None:
            self.log_handle.close()
            self.log_handle = None

    def start(self, *, python=None, event_origin=None, summary_origin=None, base_url=None):
        with self.lock:
            current = self.status()
            if self.process is not None and self.process.poll() is None:
                return current
            if self.process is None and self.directory is not None and current['state'] in ACTIVE_STATES | {'error'} \
                    and not _pid_confirmed_gone(current.get('pid')):
                raise ValueError('Previous webcam worker ownership is uncertain. Stop or inspect its PID before starting again.')
            if not event_origin or event_origin == 'CAdmin' or (summary_origin or event_origin) == 'CAdmin':
                raise ValueError('Configure verified non-admin webcam event and summary publisher origins.')
            interpreter = Path(python or os.getenv('CINDYSENSE_WEBCAM_PYTHON') or
                               self.root / '.venv-webcam' / 'Scripts' / 'python.exe')
            if not interpreter.is_file():
                raise ValueError('Configure CINDYSENSE_WEBCAM_PYTHON with the webcam Python environment.')
            self._close_log()
            self.state_dir.mkdir(parents=True, exist_ok=True)
            for name in ('status.json', 'frame.jpg', 'stop', 'worker.log'):
                (self.state_dir / name).unlink(missing_ok=True)
            self.directory = self.state_dir
            self.log_handle = open(self.directory / 'worker.log', 'wb')
            command = [str(interpreter), str(self.root / 'tools' / 'webcam_squat.py'),
                       '--exercise', 'cindy', '--duration-minutes', '20', '--tinyiot',
                       '--tinyiot-base', base_url, '--tinyiot-event-origin', event_origin,
                       '--tinyiot-summary-origin', summary_origin or event_origin,
                       '--dashboard-ipc', str(self.directory), '--no-workout-timeseries']
            try:
                self.process = self.process_factory(command, cwd=self.root,
                                                    stdout=self.log_handle, stderr=subprocess.STDOUT)
            except OSError:
                self._close_log()
                raise ValueError('Could not launch the webcam Python worker.') from None
            return self.status()

    def request_stop(self):
        with self.lock:
            current = self.status()
            if self.directory is None or current['state'] in ('idle', 'stopped'):
                return current
            (self.directory / 'stop').touch(exist_ok=True)
            return self.status()

    def frame_path(self):
        with self.lock:
            return self.directory / 'frame.jpg' if self.directory else None

    def shutdown(self):
        self.request_stop()
        with self.lock:
            process = self.process
        if process is not None and process.poll() is None:
            try:
                process.wait(timeout=90)
            except subprocess.TimeoutExpired:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
        with self.lock:
            self._close_log()
