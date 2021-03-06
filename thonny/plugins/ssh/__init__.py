import ast
import logging
import io
import os
import platform
import queue
import re
import subprocess
import sys
import textwrap
import threading
import time
import tokenize
import traceback
import webbrowser
from queue import Queue
from textwrap import dedent
from time import sleep
from tkinter import ttk, messagebox
from thonny.ui_utils import askopenfilename, create_url_label
from typing import Optional

import jedi
import serial.tools.list_ports
from serial import SerialException

from thonny import common, get_runner, get_shell, get_workbench, running
from thonny.common import (
    BackendEvent,
    InlineResponse,
    MessageFromBackend,
    ToplevelCommand,
    ToplevelResponse,
    InterruptCommand,
    EOFCommand,
    CommandToBackend,
)
from thonny.config_ui import ConfigurationPage
from thonny.misc_utils import find_volumes_by_name, TimeHelper
from thonny.plugins.backend_config_page import BackendDetailsConfigPage
from thonny.running import BackendProxy, SubprocessProxy
from thonny.ui_utils import SubprocessDialog, create_string_var, show_dialog
import collections
from threading import Thread


class SshProxy(SubprocessProxy):
    def __init__(self, clean):
        super().__init__(clean, "python3")
        self._host = get_workbench().get_option("ssh.host")
        self._user = get_workbench().get_option("ssh.user")
        self._password = get_workbench().get_option("ssh.password")
        self._client = None

        try:
            from paramiko.client import SSHClient
        except ImportError:
            self._show_error(
                "SSH connection requires an extra package -- 'paramiko'.\n"
                + "You can install it via 'Tools => Manage plug-ins' or via system package manager."
            )
            return
        
        self._client = SSHClient()
        self._client.connect(hostname=self._host, username=self._user, password=self._password)
        

    def _get_launcher_with_args(self):
        return ["~/launcher.py"]

    def _start_background_process(self, clean=None):
        # deque, because in one occasion I need to put messages back
        self._response_queue = collections.deque()

        """
        # prepare environment
        env = get_environment_for_python_subprocess(self._executable)
        # variables controlling communication with the back-end process
        env["PYTHONIOENCODING"] = "utf-8"

        # because cmd line option -u won't reach child processes
        # see https://github.com/thonny/thonny/issues/808
        env["PYTHONUNBUFFERED"] = "1"

        # Let back-end know about plug-ins
        env["THONNY_USER_DIR"] = THONNY_USER_DIR
        env["THONNY_FRONTEND_SYS_PATH"] = repr(sys.path)

        env["THONNY_LANGUAGE"] = get_workbench().get_option("general.language")
        env["FRIENDLY_TRACEBACK_LEVEL"] = str(
            get_workbench().get_option("assistance.friendly_traceback_level")
        )

        if get_workbench().in_debug_mode():
            env["THONNY_DEBUG"] = "1"
        elif "THONNY_DEBUG" in env:
            del env["THONNY_DEBUG"]

        if not os.path.exists(self._executable):
            raise UserError(
                "Interpreter (%s) not found. Please recheck corresponding option!"
                % self._executable
            )
        """
        
        cmd_line = [
            self._executable,
            "-u",  # unbuffered IO
            "-B",  # don't write pyo/pyc files
            # (to avoid problems when using different Python versions without write permissions)
        ] + self._get_launcher_with_args()

        debug("Starting the backend: %s %s", cmd_line, get_workbench().get_local_cwd())
        
        stdout, stderr, stdin = self._client.exec_command(command, bufsize, timeout, get_pty, environment)

        # setup asynchronous output listeners
        Thread(target=self._listen_stdout, args=(stdout,), daemon=True).start()
        Thread(target=self._listen_stderr, args=(stderr,), daemon=True).start()

    def _get_initial_cwd(self):
        return "~/"

    def interrupt(self):
        # Don't interrupt local process, but direct it to device
        self._send_msg(InterruptCommand())

    def supports_remote_files(self):
        return self._proc is not None

    def uses_local_filesystem(self):
        return False

    def ready_for_remote_file_operations(self):
        return self._proc is not None and get_runner().is_waiting_toplevel_command()

    def supports_remote_directories(self):
        return self._cwd is not None and self._cwd != ""

    def supports_trash(self):
        return False

    def is_connected(self):
        return self._proc is not None

    def _show_error(self, text):
        get_shell().print_error("\n" + text + "\n")

    def disconnect(self):
        self.destroy()

    def get_node_label(self):
        return self._host

    def get_exe_dirs(self):
        return []
    
    def destroy(self):
        super().destroy()
        self._client.close()
        


class SshProxyConfigPage(BackendDetailsConfigPage):
    backend_name = None  # Will be overwritten on Workbench.add_backend

    def __init__(self, master):
        super().__init__(master)

    def is_modified(self):
        return False

    def should_restart(self):
        return self.is_modified()

    def apply(self):
        return


def _load_plugin():
    get_workbench().set_default("ssh.host", "raspberrypi.local")
    get_workbench().set_default("ssh.user", "pi")
    get_workbench().set_default("ssh.password", "raspberry")
    get_workbench().add_backend("SSHProxy", SshProxy, "SSH proxy", SshProxyConfigPage)
