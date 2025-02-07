# coding=utf-8
#
# The Qubes OS Project, http://www.qubes-os.org
#
# Copyright (C) 2022  Piotr Bartman <prbartman@invisiblethingslab.com>
#
# This program is free software; you can redistribute it and/or
# modify it under the terms of the GNU General Public License
# as published by the Free Software Foundation; either version 2
# of the License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301,
# USA.

import os
from pathlib import Path

import apt
import apt.progress.base
import apt_pkg

from source.common.stream_redirector import StreamRedirector
from source.common.process_result import ProcessResult
from source.common.progress_reporter import ProgressReporter, Progress

from .apt_cli import APTCLI


class APT(APTCLI):
    def __init__(self, log_handler, log_level):
        super().__init__(log_handler, log_level)
        self.apt_cache = apt.Cache()
        update = FetchProgress(weight=4)  # 4% of total time
        fetch = FetchProgress(weight=48)  # 48% of total time
        upgrade = UpgradeProgress(weight=48)  # 48% of total time
        self.progress = ProgressReporter(update, fetch, upgrade)

        # to prevent a warning: `debconf: unable to initialize frontend: Dialog`
        os.environ['DEBIAN_FRONTEND'] = 'noninteractive'

    def refresh(self, hard_fail: bool) -> ProcessResult:
        """
        Use package manager to refresh available packages.

        :param hard_fail: raise error if some repo is unavailable
        :return: (exit_code, stdout, stderr)
        """
        result = ProcessResult()
        try:
            with StreamRedirector(result):
                success = self.apt_cache.update(
                    self.progress.update_progress,
                    pulse_interval=1000  # microseconds
                )
                self.apt_cache.open()
            if not success:
                result += ProcessResult(1)
        except Exception as exc:
            result += ProcessResult(2, out="", err=str(exc))

        return result

    def upgrade_internal(self, remove_obsolete: bool) -> ProcessResult:
        """
        Use `apt` package to upgrade and track progress.
        """

        result = ProcessResult()
        try:
            self.apt_cache.upgrade(dist_upgrade=remove_obsolete)
            Path(os.path.join(
                apt_pkg.config.find_dir("Dir::Cache::Archives"), "partial")
            ).mkdir(parents=True, exist_ok=True)
            with StreamRedirector(result):
                self.apt_cache.commit(
                    self.progress.fetch_progress,
                    self.progress.upgrade_progress
                )
        except Exception as exc:
            result += ProcessResult(3, out="", err=str(exc))

        return result


class FetchProgress(apt.progress.base.AcquireProgress, Progress):
    def __init__(self, weight: int):
        Progress.__init__(self, weight)

    def fail(self, item):
        """
        Write an error message to the fake stderr.
        """
        print(str(item), flush=True, file=self._stderr)

    def pulse(self, _owner):
        """
        Report ongoing progress on fetching packages.

        Periodically invoked while the Acquire process is underway.
        This function returns a boolean value indicating whether the
        acquisition should be continued (True) or cancelled (False).
        """
        self.notify_callback(self.current_bytes / self.total_bytes * 100)
        return True

    def start(self):
        """Invoked when the Acquire process starts running."""
        super().start()
        self.notify_callback(0)

    def stop(self):
        """Invoked when the Acquire process stops running."""
        super().stop()
        self.notify_callback(100)


class UpgradeProgress(apt.progress.base.InstallProgress, Progress):
    def __init__(self, weight: int):
        apt.progress.base.InstallProgress.__init__(self)
        Progress.__init__(self, weight)

    def status_change(self, _pkg, percent, _status):
        """
        Report ongoing progress on installing/upgrading packages.
        """
        self.notify_callback(percent)

    def error(self, pkg, errormsg):
        """
        Write an error message to the fake stderr.
        """
        print("Error during installation " + str(pkg) + ":" + str(errormsg),
              flush=True, file=self.stderr)

    def start_update(self):
        super().start_update()
        self.notify_callback(0)

    def finish_update(self):
        super().finish_update()
        self.notify_callback(100)
