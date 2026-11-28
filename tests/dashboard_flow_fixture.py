"""Loopback management API with a fixed report for Landing's checkout integration test."""

from http.server import ThreadingHTTPServer
from pathlib import Path
import sys
import tempfile
import threading

from engagement.api import make_handler
from engagement.auth import AuthenticationError
from engagement.models import EngagementError
from engagement.store import EngagementStore


class FixtureIdentity:
    def verify(self, header):
        if header != "Bearer fixture-identity":
            raise AuthenticationError("invalid fixture identity")


class ReportFixture:
    def __init__(self, store):
        self.store = store

    def launch(self, request):
        if request.hostname != "app.example.com":
            raise EngagementError("unexpected fixture hostname")
        if request.inference_api_key is None:
            raise EngagementError("the fixture expects the run's inference key")
        accepted = self.store.create(request)
        if self.store.inference_key(request.id) != request.inference_api_key:
            raise EngagementError("the run's inference key was not stored")
        self.store.update(request.id, status="completed", report={"findings": [
            {"id": "fixture-1", "title": "Checkout integration fixture"}
        ]})
        return accepted


def main():
    with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
        store = EngagementStore(directory)
        handler = make_handler(store, ReportFixture(store), FixtureIdentity())
        with ThreadingHTTPServer(("127.0.0.1", 0), handler) as server:
            thread = threading.Thread(target=server.serve_forever)
            thread.start()
            try:
                print(server.server_port, flush=True)
                sys.stdin.readline()
            finally:
                server.shutdown()
                thread.join()


if __name__ == "__main__":
    main()
