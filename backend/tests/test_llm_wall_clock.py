"""Wall-clock enforcement on Ollama generation.

``urllib.request.urlopen(timeout=n)`` is a per-operation socket inactivity
timeout, not a deadline on the request. A server that keeps the connection alive
- Ollama sends newline keep-alives while a queued generation runs - re-armed it on
every byte, so a configured 90 s budget was not a bound on how long a chat request
stayed open: one recorded production turn waited 377,467 ms client-side before it
reported a transport error. These tests drive a real loopback socket server so the
mechanism is what gets pinned, not a mock. Nothing leaves this machine, and every
server is shut down by the fixture that started it.
"""

import http.server
import socket
import socketserver
import threading
import time

import pytest

from app.core.config import settings
from app.services import llm_service

FINISHED_BODY = b'{"response": "Reading therapy $7.60", "model": "stub:1b"}'


def _make_header_stall_server(cadence: float):
    """A server that starts the HTTP *headers* and never finishes them.

    ``getresponse()`` parses the status line and header block before any body read
    happens, so the read loop that bounds a response body cannot reach this phase.
    Every byte re-arms the socket inactivity timer here exactly as it does on a body,
    which is how one measured call spent 296,579 ms on a 90 s budget.
    """

    class Handler(socketserver.BaseRequestHandler):
        def handle(self):
            data = b""
            while b"\r\n\r\n" not in data:
                chunk = self.request.recv(4096)
                if not chunk:
                    return
                data += chunk
            try:
                self.request.sendall(
                    b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                )
                while True:
                    # A space, never a newline: the header block stays incomplete, so
                    # the client is blocked in begin() and no body read ever starts.
                    self.request.sendall(b" ")
                    time.sleep(cadence)
            except OSError:
                return

    server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread, f"http://127.0.0.1:{server.server_address[1]}"


def _make_server(mode: str, cadence: float, finish_after: int):
    """A one-request-per-thread HTTP server with a chosen stalling behaviour.

    ``trickle`` dribbles newline keep-alives and never finishes, ``silence`` accepts
    the request and says nothing, ``slow`` trickles ``finish_after`` keep-alives and
    then sends a complete body. Each is the shape of a queue that Ollama really
    holds open, which is the case an inactivity timer cannot bound.
    """

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802 - http.server dispatch name
            self.rfile.read(int(self.headers.get("Content-Length") or 0))
            if mode == "late-headers":
                # A model that is still loading holds the status line back entirely:
                # no bytes at all, then a whole answer. A deadline guard that clamps
                # the socket below the budget would call this a transport error and
                # throw away a healthy warm-up.
                time.sleep(cadence)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            if mode in ("silence", "late-headers"):
                if mode == "silence":
                    time.sleep(30)
                else:
                    self.wfile.write(FINISHED_BODY)
                    self.wfile.flush()
                self.close_connection = True
                return
            sent = 0
            try:
                while finish_after == 0 or sent < finish_after:
                    self.wfile.write(b"\n")
                    self.wfile.flush()
                    sent += 1
                    time.sleep(cadence)
                if finish_after:
                    self.wfile.write(FINISHED_BODY)
                    self.wfile.flush()
            except OSError:
                # The client gave up on its deadline; this server has nothing left
                # to say and no reason to keep saying it.
                return
            self.close_connection = True

        def log_message(self, *args):  # keep the test output readable
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread, f"http://127.0.0.1:{server.server_address[1]}"


@pytest.fixture
def stalled_ollama():
    """Hands out stall servers and shuts every one of them down again."""
    started: list[tuple[http.server.ThreadingHTTPServer, threading.Thread]] = []

    def build(mode="trickle", cadence=0.05, finish_after=0):
        if mode == "header-stall":
            server, thread, url = _make_header_stall_server(cadence)
        else:
            server, thread, url = _make_server(mode, cadence, finish_after)
        started.append((server, thread))
        return url

    yield build
    for server, thread in started:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.fixture
def point_at(stalled_ollama, monkeypatch):
    """Point the provider at a stall server and keep the original URL restorable."""

    def attach(**kwargs):
        url = stalled_ollama(**kwargs)
        monkeypatch.setattr(settings, "OLLAMA_BASE_URL", url)
        return url

    return attach


def _budget(seconds):
    """Ask for a generation and return (elapsed, raised) - the deadline is the claim."""
    started = time.perf_counter()
    try:
        llm_service._post("/api/generate", {"model": "stub:1b", "prompt": "x"}, seconds)
        return time.perf_counter() - started, None
    except Exception as exc:  # noqa: BLE001 - the test decides what it expected
        return time.perf_counter() - started, exc


def _cut_on_the_budget(elapsed, budget, late=1.0):
    """Assert the call ended on its budget - neither past it nor before it.

    The upper half is the defect: an inactivity timer let a server buy time
    indefinitely. The lower half is the new risk once a deadline is enforced from
    another thread - a guard that fires early throws away an answer the budget
    still allows. It is one clock tick rather than zero because Windows timer
    granularity is ~15.6 ms: 12 samples of a 1 s budget cut at 1.017-1.036 s, and a
    full-suite run once reported 0.987 s.
    """
    assert budget - 0.1 <= elapsed < budget + late, (
        f"a {budget:.2f}s budget was honoured at {elapsed:.2f}s"
    )


def test_a_trickling_response_cannot_extend_the_request_past_the_budget(point_at):
    """The budget is a deadline, so bytes arriving must not buy more time."""
    point_at(mode="trickle", cadence=0.05)
    elapsed, exc = _budget(1.0)
    assert isinstance(exc, llm_service.GenerationTimeout), exc
    _cut_on_the_budget(elapsed, 1.0)


def test_a_silent_connection_is_also_cut_at_the_budget(point_at):
    """No bytes at all: the socket timeout and the wall clock must agree."""
    point_at(mode="silence")
    elapsed, exc = _budget(1.0)
    assert isinstance(exc, llm_service.GenerationTimeout), exc
    _cut_on_the_budget(elapsed, 1.0)


def test_a_slow_answer_that_arrives_inside_the_budget_is_still_read(point_at):
    """Capping the request must not start throwing away valid completions."""
    point_at(mode="slow", cadence=0.15, finish_after=3)
    elapsed = time.perf_counter()
    payload = llm_service._post(
        "/api/generate", {"model": "stub:1b", "prompt": "x"}, 5.0
    )
    assert payload["response"] == "Reading therapy $7.60"
    assert payload["model"] == "stub:1b"
    assert time.perf_counter() - elapsed < 5.0


def test_generation_cannot_hold_the_chatbot_open_indefinitely(point_at, monkeypatch):
    """The caller's fallback must arrive on the budget, not on the server's whim."""

    def tags(path, timeout):
        assert path == "/api/tags"
        return {"models": [{"name": "stub:1b"}]}

    point_at(mode="trickle", cadence=0.05)
    monkeypatch.setattr(llm_service, "_get", tags)
    monkeypatch.setattr(llm_service, "_status_cache", {"checked_at": 0.0, "payload": None})
    monkeypatch.setattr(settings, "OLLAMA_MODEL", "stub:1b")
    monkeypatch.setattr(settings, "OLLAMA_ENABLED", True)
    monkeypatch.setattr(settings, "OLLAMA_GENERATE_TIMEOUT", 1.0)

    started = time.perf_counter()
    result = llm_service.generate_answer(
        "Which activity involves reading?", ["[1] Reading therapy $7.60"]
    )
    elapsed = time.perf_counter() - started

    assert result is None, "a timed-out generation must fall back, not answer"
    assert elapsed < 2.0, f"the request was held open for {elapsed:.2f}s"


def test_the_budget_is_reported_as_a_budget_and_not_a_broken_runtime(
    point_at, monkeypatch, caplog
):
    """One slow question must not make the provider look unusable for the next one."""

    def tags(path, timeout):
        return {"models": [{"name": "stub:1b"}]}

    point_at(mode="trickle", cadence=0.05)
    monkeypatch.setattr(llm_service, "_get", tags)
    monkeypatch.setattr(llm_service, "_status_cache", {"checked_at": 0.0, "payload": None})
    monkeypatch.setattr(settings, "OLLAMA_MODEL", "stub:1b")
    monkeypatch.setattr(settings, "OLLAMA_ENABLED", True)

    with caplog.at_level("INFO"):
        assert llm_service.generate_answer("q", ["[1] Reading therapy $7.60"]) is None
        assert llm_service.is_usable() is True
    assert any(
        "wall-clock budget" in record.message for record in caplog.records
    ), [record.message for record in caplog.records]


def test_the_socket_is_closed_when_the_budget_is_exceeded(point_at):
    """Abandoning a request must not leak the connection it was made on.

    Repeated timeouts on one server are the test: a client that left its socket
    open would queue each new request behind the previous, and the budget would
    stop being a bound on anything.
    """
    point_at(mode="trickle", cadence=0.05)
    for attempt in range(5):
        elapsed, exc = _budget(0.5)
        assert isinstance(exc, llm_service.GenerationTimeout), (attempt, exc)
        assert elapsed < 1.5, f"attempt {attempt} drifted to {elapsed:.2f}s"


def test_a_body_that_stops_arriving_is_a_timeout_not_a_blank_answer(point_at):
    """A half-delivered response is a failure the caller falls back on."""
    point_at(mode="trickle", cadence=0.05)
    elapsed, exc = _budget(1.0)
    assert isinstance(exc, llm_service.GenerationTimeout), exc
    assert not isinstance(exc, llm_service.OllamaUnavailable)
    assert elapsed < 2.0


def test_headers_that_never_finish_are_cut_at_the_budget_too(point_at):
    """The phase before the body read is where the leak actually lived.

    A measured post-fix run still spent 296,579 ms in one generation call while the
    budget said 90 s, because connect, send and the header parse each got the whole
    budget again and each byte of an unfinished header re-armed them. The body read
    loop cannot see this phase at all, so the watchdog has to.
    """
    point_at(mode="header-stall", cadence=0.05)
    elapsed, exc = _budget(1.0)
    assert isinstance(exc, llm_service.GenerationTimeout), exc
    _cut_on_the_budget(elapsed, 1.0, late=1.5)


def test_a_stopped_ollama_is_not_reported_as_a_slow_question(monkeypatch):
    """A refused connection must stay an availability signal, not a budget miss.

    This is what keeps "fall back to the extractive answer" and "the runtime is
    down, stop knocking" distinguishable after the deadline handling went in.
    """
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    monkeypatch.setattr(settings, "OLLAMA_BASE_URL", f"http://127.0.0.1:{port}")

    elapsed, exc = _budget(5.0)
    assert exc is not None
    assert not isinstance(exc, llm_service.GenerationTimeout), exc
    assert elapsed < 5.0, f"a connection that was refused took {elapsed:.2f}s"


def test_a_lost_abandon_attempt_does_not_reopen_the_wait(point_at, monkeypatch):
    """The ceiling cannot depend on one shutdown landing.

    Run C on the real models measured the shape this pins: with a single watchdog
    attempt, three qwen calls stopped within 23 ms of their 90 s budget while a
    fourth ran 111,365 ms - 21 s past it. A shutdown that finds no socket yet, or
    one that fails to wake a parked read, left only an inactivity timer that the
    server re-arms byte by byte. The deadline guard re-asserts, so swallowing the
    first attempts may cost a tick but cannot cost the wait.
    """
    real = llm_service._abandon
    swallowed: list[int] = []

    def flaky(connection):
        if len(swallowed) < 3:
            swallowed.append(1)
            return
        real(connection)

    point_at(mode="header-stall", cadence=0.05)
    monkeypatch.setattr(llm_service, "_abandon", flaky)

    elapsed, exc = _budget(1.0)
    assert isinstance(exc, llm_service.GenerationTimeout), exc
    _cut_on_the_budget(elapsed, 1.0)
    assert len(swallowed) >= 1, "the guard never tried to abandon the socket"


def test_a_budget_clamp_never_shortens_a_healthy_wait(point_at):
    """A model that is still loading sends nothing, and that is not a failure.

    The guard re-arms the socket with the time genuinely left. If it clamped to the
    read slice instead, a 55 s qwen warm-up would be aborted as a transport error at
    the first quarter-second of silence and every cold generation would fall back.
    """
    point_at(mode="late-headers", cadence=2.0)
    started = time.perf_counter()
    payload = llm_service._post("/api/generate", {"model": "stub:1b", "prompt": "x"}, 6.0)
    assert payload["response"] == "Reading therapy $7.60"
    elapsed = time.perf_counter() - started
    assert 1.9 <= elapsed < 6.0, f"a silent but live wait was cut at {elapsed:.2f}s"
