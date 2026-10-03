"""Overview cards drawn from snapshots (no server, no files)."""
from __future__ import annotations

import re
import unittest
from typing import Any

from mon_support import GIB, shape
from monitor.cards import (View, card_activity, card_connect, card_context, card_health, card_requests, column,
                           kv_info, log_view, mx_card_context, req_row, status_of)
from monitor.fmt import ANSI, Ln, vlen
from monitor.logbook import LogBook, RequestRecord
from monitor.model import ServerData, SlotInfo, SlowStats

CMD = "llama-server -m /m/a.gguf -c 196608 --parallel 2 -ctk q4_0 -ctv q8_0 --cache-ram 4096"


def view(**kw: Any) -> View:
    base = dict(levels={}, host="127.0.0.1", port=8080, base="http://127.0.0.1:8080", key="secretkey1234",
                key_file="/home/u/.mtplx/api-key", key_shown=False, server_pid=None, log=LogBook(), log_path=None,
                model_path="/m/a.gguf", model_size=13 * GIB, gpu_limit=(25 * GIB, "test"), slow=SlowStats(),
                total_mem=32 * GIB, home="/home/u")
    base.update(kw)
    return View(**base)


def text(lines: Any) -> str:
    return "\n".join(ANSI.sub("", x.text if isinstance(x, Ln) else x) for x in lines)


class StatusTest(unittest.TestCase):
    def test_labels(self) -> None:
        self.assertEqual(status_of(ServerData(exited=True), 42)[0], "EXITED")
        self.assertEqual(status_of(ServerData(pid=5), None)[0], "LOADING")
        self.assertEqual(status_of(ServerData(), None)[0], "OFFLINE")
        self.assertEqual(status_of(ServerData(up=True), None)[0], "UP")
        self.assertEqual(status_of(ServerData(up=True, slots=True), None)[0], "IDLE")
        reading = ServerData(up=True, slots=True, busy=True, prompt=200, cached=50, processed=50)
        self.assertEqual(status_of(reading, None)[:2], ("READING", "43"))
        self.assertIn("50%", status_of(reading, None)[2])
        self.assertEqual(status_of(ServerData(up=True, slots=True, busy=True, decoded=3), None)[0], "GENERATING")
        two = ServerData(up=True, slots=True, busy=True, slot_list=[SlotInfo(0, True, 1, 1, 1, 0, 0, 0)] * 2)
        self.assertEqual(status_of(two, None)[0], "BUSY ×2")
        mx = ServerData(up=True, backend="mtplx", busy=True, mx={"active_requests": 3})
        self.assertEqual(status_of(mx, None)[0], "BUSY +2")


class CardsTest(unittest.TestCase):
    def test_connect_hides_the_key(self) -> None:
        shown = text(card_connect(view(), ServerData(conns=[("10.0.0.2", "5000")])).lines)
        self.assertIn("••••••••••••••••1234", shown)
        self.assertNotIn("secretkey", shown)
        self.assertIn("1 connected from 10.0.0.2", shown)
        self.assertIn("secretkey1234", text(card_connect(view(key_shown=True), ServerData()).lines))

    def test_context_with_two_slots_and_mixed_kv(self) -> None:
        d = ServerData(cmd=CMD, shape=shape(), n_ctx=98304, slot_list=[SlotInfo(0, True, 1, 98304, 20000, 0, 0, 0),
                                                                        SlotInfo(1, False, 2, 98304, 685, 0, 0, 134)])
        kv = kv_info(d)
        assert kv is not None
        self.assertEqual((kv.k, kv.v, kv.slots, kv.cache_ram), ("q4_0", "q8_0", 2, 4096 * 2**20))
        card = card_context(view(), d)
        self.assertIn("2 slots", ANSI.sub("", card.summary))
        body = text(card.lines)
        self.assertIn("slot 0", body)
        self.assertIn("mixed types", body)

    def test_cards_keep_their_height_without_data(self) -> None:
        v = view(levels={"context": 2, "health": 2})
        self.assertEqual(len(card_context(v, ServerData()).lines), 9)
        self.assertEqual(len(card_health(v, ServerData()).lines), 9)

    def test_activity_eta_while_reading(self) -> None:
        d = ServerData(up=True, slots=True, busy=True, prompt=10000, cached=0, processed=4000, pp_rate=200.0)
        card = card_activity(view(), d)
        self.assertEqual(ANSI.sub("", card.summary), "ETA 30s")

    def test_mtplx_context_survives_odd_snapshots(self) -> None:
        card = mx_card_context(view(levels={"context": 2}), ServerData(backend="mtplx", mx={"memory_plan": "x", "session_bank": [],
                                                                                            "latest": {"x": 1}}))
        self.assertEqual(card.title, "CONTEXT")

    def test_column_rows_are_exactly_w_wide(self) -> None:
        rows = column(view(), ["connect", "context", "memory", "activity", "model", "health", "system"], ServerData(), 70)
        self.assertTrue(all(vlen(t) == 70 for t, _ in rows))


class RequestsLogTest(unittest.TestCase):
    def test_req_row(self) -> None:
        r = RequestRecord(t0=10.0, t1=35.0, ctx=55400, new=119, pp=118.4, gen=440, tg=17.7, acc=0.72)
        row = ANSI.sub("", req_row(r, lambda t: "22:49:46"))
        self.assertEqual(re.sub(" +", " ", row), "22:49:46 55.4K 119 118 440 17.7 25s 72%")
        self.assertTrue(req_row(RequestRecord(error=True), lambda t: "--:--:--").endswith("error\x1b[0m"))

    def test_requests_card_pads_to_n_rows(self) -> None:
        card = card_requests(view(), ServerData(), 3)
        self.assertEqual(len(card.lines), 1 + 3)

    def test_log_view_errors_only_and_scroll(self) -> None:
        book = LogBook()
        for line in ("0.00.000.001 I a", "0.00.000.002 E b", "0.00.000.003 W c", "0.00.000.004 I d"):
            book.add(line)
        v = view(log=book, log_path="/l.log", errors_only=True)
        self.assertEqual([ANSI.sub("", x) for x in log_view(v, 3, 80) if isinstance(x, str)],
                         ["0.00.000.002 E b", "0.00.000.003 W c", ""])
        scrolled = view(log=book, log_path="/l.log", log_scroll=1)
        self.assertEqual(ANSI.sub("", str(log_view(scrolled, 2, 80)[-1])), "0.00.000.003 W c")
        self.assertIn("no log file", ANSI.sub("", str(log_view(view(), 2, 80)[0])))


if __name__ == "__main__":
    unittest.main()
