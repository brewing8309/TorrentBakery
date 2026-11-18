"""A stateful stand-in for qBittorrent 5's WebAPI, including the behaviours that bit: adding is asynchronous, a recheck of a torrent that
was stopped puts it back to stopped when it is over (and a start sent during the check is undone), and files at the wrong place give 0 %."""
import httpx


class FakeQbit:
    def __init__(self, files_found=True, login=False, listed_after=2, check_polls=3, undo_start_during_check=True, never_starts=False, existing=False):
        self.files_found, self.login, self.listed_after, self.check_polls = files_found, login, listed_after, check_polls
        self.undo_start_during_check, self.never_starts = undo_start_during_check, never_starts
        self.calls: list[tuple[str, bytes]] = []
        self.t: dict | None = None
        self.polls = 0
        if existing:  # a torrent that an earlier try left stopped at 100 %
            self.t = {"hash": None, "added_polls": 99, "checking": False, "progress": 1.0 if files_found else 0.0, "running": False,
                      "stop_after_check": False, "start_sent_during_check": False}

    def _state(self):
        t = self.t
        if t["checking"]:
            self.polls += 1
            if self.polls > self.check_polls:
                t["checking"] = False
                t["progress"] = 1.0 if self.files_found else 0.0
                if t["stop_after_check"] or self.undo_start_during_check and t["start_sent_during_check"]:
                    t["running"] = False
        if t["checking"]:
            return "checkingUP"
        if not t["running"]:
            return "stoppedUP" if t["progress"] >= 1 else "stoppedDL"
        return "missingFiles" if not self.files_found else "stalledUP"

    def __call__(self, r: httpx.Request) -> httpx.Response:
        path = r.url.path.removeprefix("/api/v2/")
        self.calls.append((path, r.content))
        if path == "auth/login":
            return httpx.Response(200, text="Ok.", headers={"set-cookie": "SID=1; Path=/"})
        if self.login and not r.headers.get("cookie"):
            return httpx.Response(403)
        if path == "app/version":
            return httpx.Response(200, text="v5.3.0")
        if path == "torrents/add":
            self.t = {"hash": None, "added_polls": 0, "checking": False, "progress": 1.0 if self.files_found else 0.0, "running": False,
                      "stop_after_check": False, "start_sent_during_check": False}
            return httpx.Response(200, text="Ok.")
        if path == "torrents/info":
            h = r.url.params.get("hashes", "")
            if self.t is None:
                return httpx.Response(200, json=[])
            self.t["hash"] = h
            self.t["added_polls"] += 1
            if self.t["added_polls"] <= self.listed_after:
                return httpx.Response(200, json=[])  # not listed yet
            return httpx.Response(200, json=[{"hash": h, "state": self._state(), "progress": self.t["progress"]}])
        if path == "torrents/recheck":
            self.t.update(checking=True, stop_after_check=not self.t["running"], start_sent_during_check=False)
            self.polls = 0
            return httpx.Response(200, text="")
        if path in ("torrents/start", "torrents/resume"):
            if self.t["checking"]:
                self.t["start_sent_during_check"] = True
            elif not self.never_starts:
                self.t["running"], self.t["stop_after_check"] = True, False
            return httpx.Response(200, text="")
        return httpx.Response(404)

    def transport(self):
        return httpx.MockTransport(self)

    @property
    def paths(self):
        return [p for p, _ in self.calls]
