import asyncio
import os
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import aiohttp
from aiohttp import web

from core import device_use_turn
from core.connection import ConnectionHandler
from plugins_func.functions.local_media import play_local_media
from plugins_func.register import Action


class DeviceUseTurnTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.calls = []
        self.state = {}

        async def begin(request):
            self.assertEqual(request.headers["Authorization"], "Bearer " + "a" * 64)
            data = await request.json()
            self.calls.append(("begin", data))
            call = self.state.setdefault(data["callId"], {
                "callId": data["callId"], "state": "accepted", "generation": 1,
            })
            return web.json_response(call)

        async def end(request):
            data = await request.json()
            kind = request.path.rsplit("/", 1)[-1]
            self.calls.append((kind, data))
            call = self.state[data["callId"]]
            call["state"] = "canceled" if kind == "cancel" else "completed"
            return web.json_response(call)

        async def play(request):
            data = await request.json()
            self.calls.append(("play", data))
            return web.json_response({"accepted": True,
                "call": {"callId": data["callId"], "scope": "SESSION"}})

        app = web.Application()
        app.router.add_post("/calls/begin", begin)
        app.router.add_post("/calls/complete", end)
        app.router.add_post("/calls/cancel", end)
        app.router.add_post("/media/play", play)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        self.site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await self.site.start()
        port = self.site._server.sockets[0].getsockname()[1]
        self.host_patch = patch.object(device_use_turn, "HOST", f"http://127.0.0.1:{port}")
        self.host_patch.start()
        self.temp = tempfile.TemporaryDirectory()
        token_file = Path(self.temp.name) / "token"
        token_file.write_text("a" * 64)
        self.token_patch = patch.dict(os.environ, {"RLCD_DEVICE_USE_TOKEN_FILE": str(token_file)})
        self.token_patch.start()

    async def asyncTearDown(self):
        self.token_patch.stop()
        self.host_patch.stop()
        await self.runner.cleanup()
        self.temp.cleanup()

    async def test_stable_tool_identity_and_session_root_completion(self):
        conn = SimpleNamespace(device_use_started_turns=set())
        turn_token = device_use_turn.turn_id_context.set("turn-1")
        tool_token = device_use_turn.tool_id_context.set("model-tool-1")
        try:
            async with aiohttp.ClientSession() as session:
                headers = device_use_turn.control_headers()
                first = await device_use_turn.call_metadata(conn, session, headers)
                second = await device_use_turn.call_metadata(conn, session, headers)
            self.assertEqual(first, second)
            self.assertEqual(first["parentId"], "turn-1")
            self.assertEqual(len(first["callId"]), 32)
            self.assertEqual(conn.device_use_started_turns, {"turn-1"})
            await device_use_turn.finish_turn(conn, "turn-1", canceled=False)
            self.assertEqual(self.state["turn-1"]["state"], "completed")
            self.assertEqual(conn.device_use_started_turns, set())
            self.assertEqual([kind for kind, _ in self.calls],
                             ["begin", "begin", "complete"])
        finally:
            device_use_turn.tool_id_context.reset(tool_token)
            device_use_turn.turn_id_context.reset(turn_token)

    async def test_abort_cancels_root(self):
        conn = SimpleNamespace(device_use_started_turns=set())
        turn_token = device_use_turn.turn_id_context.set("turn-abort")
        tool_token = device_use_turn.tool_id_context.set("model-tool-abort")
        try:
            async with aiohttp.ClientSession() as session:
                await device_use_turn.call_metadata(conn, session,
                    device_use_turn.control_headers())
            await device_use_turn.finish_turn(conn, "turn-abort", canceled=True)
            self.assertEqual(self.state["turn-abort"]["state"], "canceled")
            self.assertEqual(conn.device_use_started_turns, set())
        finally:
            device_use_turn.tool_id_context.reset(tool_token)
            device_use_turn.turn_id_context.reset(turn_token)

    async def test_authorized_media_uses_session_call(self):
        media = Path(self.temp.name) / "song.mp3"
        media.write_bytes(b"test")
        conn = SimpleNamespace(
            device_use_started_turns=set(), headers={"device-id": "dev-id"},
            config={"media": {"root": self.temp.name},
                    "server": {"vision_explain": "http://127.0.0.1:8003/x"}},
            mcp_client=None,
        )
        turn_token = device_use_turn.turn_id_context.set("turn-media")
        tool_token = device_use_turn.tool_id_context.set("model-media")
        with patch.dict(os.environ, {"XIAOZHI_DEVICE_USE_DEVICE_IDS": "dev-id"}):
            try:
                result = await play_local_media(conn, "song.mp3")
                self.assertEqual(result.action, Action.RECORD, result.response)
                self.assertEqual([kind for kind, _ in self.calls], ["begin", "play"])
                payload = self.calls[1][1]
                self.assertEqual(payload["parentId"], "turn-media")
                self.assertEqual(len(payload["callId"]), 32)
                self.assertIn("/media/opus/song.mp3", payload["url"])
            finally:
                device_use_turn.tool_id_context.reset(tool_token)
                device_use_turn.turn_id_context.reset(turn_token)

    async def test_chat_worker_completes_its_own_turn(self):
        conn = object.__new__(ConnectionHandler)
        conn._chat_turn = threading.local()
        conn.device_use_started_turns = set()
        conn.loop = asyncio.get_running_loop()
        conn.client_abort = False
        closed = asyncio.Event()
        observed = []

        def chat_impl(query, depth):
            turn_id = conn._chat_turn.id
            conn.device_use_started_turns.add(turn_id)
            return turn_id

        async def finish(_conn, turn_id, canceled):
            observed.append((turn_id, canceled))
            closed.set()

        conn._chat_impl = chat_impl
        with patch("core.connection.finish_device_use_turn", finish):
            turn_id = await asyncio.to_thread(conn.chat, "hello")
            await asyncio.wait_for(closed.wait(), 2)
        self.assertEqual(observed, [(turn_id, False)])


if __name__ == "__main__":
    unittest.main()
