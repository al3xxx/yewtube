import os
import re
import shlex
import shutil
import socket
import subprocess
import tempfile
import time

from .. import c, config, g, util
from ..player import CmdPlayer

mswin = os.name == "nt"


class vlc(CmdPlayer):
    def __init__(self, player):
        self.player = player
        self.rc_sockpath = None
        self.rc_temp_dir = None

    def _generate_real_playerargs(self):
        args = shlex.split(config.PLAYERARGS.get.strip(), posix=not mswin)

        pd = g.playerargs_defaults["vlc"]
        args.extend((pd["title"], '"{0}"'.format(self.song.title)))

        if config.VLC_DUMMY_INTERFACE.get:
            print(
                '[VLC DUMMY INTERFACE] Playing "{0}" ...'.format(
                    self.song.title
                )
            )
            args.extend(("-I", "dummy"))  # vlc without gui
        if not config.SHOW_VIDEO.get:
            args.extend(("--no-video",))

        if self.subtitle_path:
            args.extend(("--sub-file", self.subtitle_path))

        util.list_update("--play-and-exit", args)

        return [self.player] + args + [self.stream["url"]]

    def clean_up(self):
        self._kill_instance()
        if self.rc_sockpath and os.path.exists(self.rc_sockpath):
            os.unlink(self.rc_sockpath)
        self.rc_sockpath = None

        if self.rc_temp_dir and os.path.exists(self.rc_temp_dir):
            shutil.rmtree(self.rc_temp_dir, ignore_errors=True)
        self.rc_temp_dir = None

    def launch_player(self, cmd):
        if not mswin:
            self.rc_temp_dir = tempfile.mkdtemp(prefix="mpsyt-vlc-")
            self.rc_sockpath = os.path.join(self.rc_temp_dir, "vlc.sock")
            cmd.extend(
                [
                    "--extraintf",
                    "oldrc",
                    "--rc-unix",
                    self.rc_sockpath,
                    "--rc-fake-tty",
                ]
            )

        with open(os.devnull, "w") as devnull:
            self.p = subprocess.Popen(cmd, shell=False, stderr=devnull)
        self._player_status(self.songdata + "; ", self.song.length)
        self.p.wait()
        self.next()

    def _help(self, short=True):
        return (
            "    [{0}CTRL-C{1}] return "
            "(Use VLC controls in player window)"
        ).format(c.g, c.w)

    def _player_status(self, prefix, songlength=0):
        """Track VLC progress using RC socket, fallback to coarse timer."""
        if self._player_status_via_socket(prefix, songlength):
            return

        start = time.time()
        while self.p.poll() is None:
            elapsed_s = int(max(0, time.time() - start))
            self.make_status_line(elapsed_s, prefix, songlength)
            time.sleep(1)

    def _query_rc_int(self, rc_sock, command):
        """Send an RC command and parse an integer response if available."""
        rc_sock.sendall((command + "\n").encode())
        chunks = []
        deadline = time.time() + 0.4

        while time.time() < deadline:
            try:
                chunk = rc_sock.recv(4096)
            except socket.timeout:
                break
            if not chunk:
                break
            chunks.append(chunk.decode(errors="ignore"))
            if "\n" in chunks[-1]:
                break

        response = "".join(chunks)
        for line in response.splitlines():
            line = line.strip()
            if not line:
                continue
            if line.isdigit():
                return int(line)
            match = re.search(r"(-?\d+)$", line)
            if match:
                return int(match.group(1))
        return None

    def _player_status_via_socket(self, prefix, songlength=0):
        """Read current position from VLC oldrc Unix socket."""
        if mswin or not self.rc_sockpath:
            return False

        for _ in range(20):
            if self.p.poll() is not None:
                return False
            if os.path.exists(self.rc_sockpath):
                break
            time.sleep(0.1)
        else:
            return False

        rc_sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        rc_sock.settimeout(0.2)
        try:
            rc_sock.connect(self.rc_sockpath)
            known_length = songlength
            while self.p.poll() is None:
                if not known_length:
                    reported_length = self._query_rc_int(rc_sock, "get_length")
                    if reported_length and reported_length > 0:
                        known_length = reported_length

                elapsed_s = self._query_rc_int(rc_sock, "get_time")
                if elapsed_s is not None and elapsed_s >= 0:
                    self.make_status_line(elapsed_s, prefix, known_length)
                time.sleep(1)
            return True
        except OSError as err:
            util.dbg("VLC RC socket unavailable, using fallback status: %s", err)
            return False
        finally:
            rc_sock.close()

    def _kill_instance(self):
        if self.p and self.p.poll() is None:
            self.p.terminate()
