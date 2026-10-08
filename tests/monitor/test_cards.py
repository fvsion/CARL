"""Live tab cards drawn from snapshots (no server, no files)."""
from __future__ import annotations

import re
import unittest
from dataclasses import replace
from typing import Any

from mon_support import GIB, shape
from monitor.cards import (View, card_connect, card_health, card_memory, card_requests, card_slots, card_speed, column,
                           kv_info, live_sentence, log_view, req_row, status_of)
from monitor.fmt import ANSI, Ln, aligned, vlen
from monitor.logbook import LogBook, RequestRecord
from monitor.model import ServerData, SlotInfo, SlowStats

CMD = "llama-server -m /m/a.gguf -c 196608 --parallel 2 -ctk q4_0 -ctv q8_0 --cache-ram 4096"


def view(**kw: Any) -> View:
    base = dict(levels={}, host="127.0.0.1", port=8080, base="http://127.0.0.1:8080", key="secretkey1234",
                key_file="/home/u/.config/carl/api-key", key_shown=False, server_pid=None, log=LogBook(), log_path=None,
                model_path="/m/a.gguf", model_size=13 * GIB, gpu_limit=(25 * GIB, "test"), slow=SlowStats(),
                total_mem=32 * GIB, home="/home/u")
    base.update(kw)
    return View(**base)


def text(lines: Any) -> str:
    """The card's text as shown: its rows laid out (label column), no colours."""
    return "\n".join(ANSI.sub("", x.text if isinstance(x, Ln) else x) for x in aligned(list(lines)))


def table_row(body: str, label: str) -> list[str]:
    """The cells of the table row that starts with label (the SPEED table)."""
    return next(ln.split() for ln in body.splitlines() if ln.startswith(label + " "))


class StatusTest(unittest.TestCase):
    def test_words(self) -> None:
        """The header's words: STOPPED, LOADING, IDLE, READING, WRITING, BUSY ×N (no OFFLINE, GENERATING)."""
        self.assertEqual(status_of(ServerData(exited=True), 42)[0], "STOPPED")
        self.assertEqual(status_of(ServerData(pid=5), None)[0], "LOADING")
        self.assertEqual(status_of(ServerData(), None)[0], "STOPPED")
        self.assertEqual(status_of(ServerData(up=True), None)[0], "IDLE")
        self.assertEqual(status_of(ServerData(up=True, slots=True), None)[0], "IDLE")
        reading = ServerData(up=True, slots=True, busy=True, prompt=200, cached=50, processed=50)
        self.assertEqual(status_of(reading, None), ("READING", "43"))
        self.assertIn("50% done", live_sentence(reading, 8080))
        self.assertEqual(status_of(ServerData(up=True, slots=True, busy=True, decoded=3), None)[0], "WRITING")
        two = ServerData(up=True, slots=True, busy=True, slot_list=[SlotInfo(0, True, 1, 1, 1, 0, 0, 0)] * 2)
        self.assertEqual(status_of(two, None)[0], "BUSY ×2")

    def test_the_sentence_says_the_state(self) -> None:
        self.assertEqual(live_sentence(ServerData(), 8080), "The server is not running.")
        idle = ServerData(up=True, slots=True, slot_list=[SlotInfo(0, False, 1, 98304, 0, 0, 0, 0),
                                                          SlotInfo(1, False, 1, 98304, 0, 0, 0, 0)])
        idle.system.pressure = "normal"
        self.assertEqual(live_sentence(idle, 8080), "No request is running. The 2 slots are free.")   # pressure: THIS MAC
        writing = ServerData(up=True, slots=True, busy=True, decoded=5, tg_rate=38.4,
                             slot_list=[SlotInfo(0, True, 1, 98304, 10, 0, 0, 5), SlotInfo(1, False, 1, 98304, 0, 0, 0, 0)])
        writing.system.pressure = "normal"
        self.assertEqual(live_sentence(writing, 8080),
                         "Writing an answer in slot 0 at 38.4 tok/s. Slot 1 is free.")


class CardsTest(unittest.TestCase):
    def test_connect_hides_the_key(self) -> None:
        shown = text(card_connect(view(), ServerData(conns=[("10.0.0.2", "5000")])).lines)
        self.assertIn("••••••••1234", shown)
        self.assertNotIn("secretkey", shown)
        self.assertRegex(shown, r"Connections +1   from 10\.0\.0\.2")
        self.assertIn("10.0.0.2", text(card_connect(view(detail="full"), ServerData(conns=[("10.0.0.2", "5")])).lines))
        self.assertIn("secretkey1234", text(card_connect(view(key_shown=True), ServerData()).lines))

    def test_slots_with_two_slots_and_mixed_types(self) -> None:
        d = ServerData(cmd=CMD, shape=shape(), n_ctx=98304, slot_list=[SlotInfo(0, True, 1, 98304, 20000, 0, 0, 0),
                                                                        SlotInfo(1, False, 2, 98304, 685, 0, 0, 134)])
        kv = kv_info(d)
        assert kv is not None
        self.assertEqual((kv.k, kv.v, kv.slots, kv.cache_ram), ("q4_0", "q8_0", 2, 4096 * 2**20))
        card = card_slots(view(), d)
        self.assertEqual(ANSI.sub("", card.summary), "2 × 96K   11% used")   # the pool fill is in the title
        body = text(card.lines)
        self.assertIn("slot 0", body)
        self.assertIn("of 96K", body)
        self.assertIn("differ", body)                                 # the warning shows at simple when it applies
        self.assertNotIn("Context memory", body)                      # the types as a row: full only
        self.assertRegex(text(card_slots(view(detail="full"), d).lines), r"Context memory +q4_0 K, q8_0 V")

    def test_memory_in_gib(self) -> None:
        d = ServerData(up=True, slots=True, rss=int(17.9 * GIB), shape=shape(), n_ctx=98304, cmd=CMD)
        body = text(card_memory(view(), d).lines)
        self.assertRegex(body, r"Server .*17\.9 of 32\.0 GiB")
        self.assertRegex(body, r"Model +13\.0 GiB")
        self.assertNotIn("1.07 GB", body)                             # no unit lesson in the main panels
        self.assertRegex(text(card_memory(view(), replace(d, up=False)).lines), r"Model +loading")

    def test_speed_while_reading(self) -> None:
        d = ServerData(up=True, slots=True, busy=True, prompt=10000, cached=0, processed=4000, pp_rate=200.0)
        body = text(card_speed(view(), d).lines)
        self.assertEqual(table_row(body, "Tok/s"), ["Tok/s", "Now", "Average", "Last", "request"])
        self.assertEqual(table_row(body, "Read"), ["Read", "200", "–", "–"])
        self.assertIn("about 30 s left", live_sentence(d, 8080))

    def test_averages_need_time_behind_them(self) -> None:
        """One 1-token reply measures ~1 µs of writing: no 1,000,000 tok/s average, nothing until 0.5 s."""
        m = {"prompt_tokens_total": 5000, "prompt_seconds_total": 6.5, "tokens_predicted_total": 1,
             "tokens_predicted_seconds_total": 0.000001}
        body = text(card_speed(view(), ServerData(up=True, slots=True, metrics=m)).lines)
        self.assertEqual(table_row(body, "Read"), ["Read", "–", "769", "–"])
        self.assertEqual(table_row(body, "Write"), ["Write", "–", "–", "–"])     # not measured yet
        m.update(tokens_predicted_total=500, tokens_predicted_seconds_total=10.0)
        body = text(card_speed(view(), ServerData(up=True, slots=True, metrics=m)).lines)
        self.assertEqual(table_row(body, "Write"), ["Write", "–", "50.0", "–"])

    def test_health_full_detail_counts(self) -> None:
        book = LogBook()
        book.add("0.00.000.001 E llama_init_from_model: failed: Gemma4Assistant requires ctx_other to be set (normal)")
        book.add("0.00.000.002 W load: control-looking token: 50 '<|tool_response>' was not control-type")
        self.assertEqual((book.counts["E"], book.counts["W"], book.counts["notice"]), (0, 0, 2))
        self.assertIn("No errors.", text(card_health(view(log=book), ServerData()).lines))
        self.assertRegex(text(card_health(view(log=book, detail="full"), ServerData()).lines), r"Routine notices +2\n")

    def test_column_rows_are_exactly_w_wide(self) -> None:
        for detail in ("simple", "full"):
            rows = column(view(detail=detail), ["slots", "speed", "memory", "connect", "health", "model"], ServerData(), 70)
            self.assertTrue(all(vlen(t) == 70 for t, _ in rows))


class RequestsLogTest(unittest.TestCase):
    def test_req_row(self) -> None:
        r = RequestRecord(t0=10.0, t1=35.0, ctx=55400, new=119, pp=118.4, gen=440, tg=17.7, acc=0.72)
        row = ANSI.sub("", req_row(r, lambda t: "22:49:46"))
        self.assertEqual(re.sub(" +", " ", row), "22:49:46 54.1K 119 118 440 17.7 25 s 72%")
        self.assertTrue(req_row(RequestRecord(error=True), lambda t: "--:--:--").endswith("error\x1b[0m"))

    def test_requests_card_says_when_empty(self) -> None:
        card = card_requests(view(), ServerData(), 3)
        self.assertIn("No finished request", text(card.lines))

    def test_log_view_errors_only_and_scroll(self) -> None:
        book = LogBook()
        for line in ("0.00.000.001 I a", "0.00.000.002 E b", "0.00.000.003 W c", "0.00.000.004 I d"):
            book.add(line)
        v = view(log=book, log_path="/l.log", errors_only=True)
        self.assertEqual([ANSI.sub("", x) for x in log_view(v, 3, 80) if isinstance(x, str)],
                         ["0.00.000.002 E b", "0.00.000.003 W c"])
        scrolled = view(log=book, log_path="/l.log", log_scroll=1)
        self.assertEqual(ANSI.sub("", str(log_view(scrolled, 2, 80)[-1])), "0.00.000.003 W c")
        self.assertIn("No log file", ANSI.sub("", str(log_view(view(), 2, 80)[0])))


if __name__ == "__main__":
    unittest.main()
