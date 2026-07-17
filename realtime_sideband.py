"""Server-side control channel for OpenAI Realtime WebRTC calls."""
from __future__ import annotations

import asyncio
import json
import re
import secrets
import time
from collections import Counter
from typing import Any, Awaitable, Callable
from urllib.parse import quote

from aiohttp import ClientError, ClientSession, ClientTimeout, WSMsgType


CALL_ID_RE = re.compile(r"^rtc_[A-Za-z0-9_-]{8,200}$")
REALTIME_SIDEBAND_URL = "wss://api.openai.com/v1/realtime?call_id="
REALTIME_CALLS_URL = "https://api.openai.com/v1/realtime/calls"


class SidebandError(RuntimeError):
    pass


def extract_response_text(response: dict[str, Any]) -> str:
    parts: list[str] = []
    for item in response.get("output", []) if isinstance(response.get("output"), list) else []:
        if not isinstance(item, dict):
            continue
        for content in item.get("content", []) if isinstance(item.get("content"), list) else []:
            if not isinstance(content, dict):
                continue
            value = content.get("text")
            if isinstance(value, str) and value.strip():
                parts.append(value.strip())
    return "\n".join(parts).strip()


class SidebandControl:
    def __init__(
        self,
        *,
        api_key: str,
        hub_session_id: str,
        call_id: str,
        expected_realtime_session_id: str,
        instructions: str,
        voice: str,
        deadline: int,
        on_limit: Callable[[str], Awaitable[None]],
        on_end: Callable[[str], Awaitable[None]],
        client_session: ClientSession,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if not CALL_ID_RE.fullmatch(call_id):
            raise SidebandError("Invalid Realtime call ID")
        self.api_key = api_key
        self.hub_session_id = hub_session_id
        self.call_id = call_id
        self.expected_realtime_session_id = expected_realtime_session_id
        self.instructions = instructions
        self.voice = voice
        self.deadline = deadline
        self._on_limit = on_limit
        self._on_end = on_end
        self._client_session = client_session
        self._clock = clock
        self._ws: Any = None
        self._reader_task: asyncio.Task[None] | None = None
        self._limit_task: asyncio.Task[None] | None = None
        self._ready = asyncio.get_running_loop().create_future()
        self._summary_marker: str | None = None
        self._summary_future: asyncio.Future[str] | None = None
        self._closing = False
        self._lifecycle = Counter()
        self._usage = Counter()
        self._transcript_parts: list[str] = []

    async def connect(self) -> None:
        try:
            self._ws = await self._client_session.ws_connect(
                REALTIME_SIDEBAND_URL + quote(self.call_id, safe=""),
                headers={"Authorization": f"Bearer {self.api_key}"},
                heartbeat=20,
                timeout=ClientTimeout(total=10),
            )
        except (asyncio.TimeoutError, ClientError) as exc:
            raise SidebandError("Could not connect Realtime sideband") from exc
        self._reader_task = asyncio.create_task(self._reader_loop())
        try:
            await asyncio.wait_for(asyncio.shield(self._ready), timeout=10)
        except Exception:
            await self.close()
            raise
        await self._send_authoritative_session_update()
        self._limit_task = asyncio.create_task(self._enforce_deadline())

    async def _reader_loop(self) -> None:
        try:
            async for message in self._ws:
                if message.type != WSMsgType.TEXT:
                    if message.type in {WSMsgType.CLOSE, WSMsgType.CLOSED, WSMsgType.ERROR}:
                        break
                    continue
                try:
                    event = json.loads(message.data)
                except (TypeError, json.JSONDecodeError):
                    continue
                if isinstance(event, dict):
                    await self.handle_event(event)
        finally:
            was_bound = self._ready.done() and not self._ready.cancelled() and self._ready.exception() is None
            if not self._ready.done():
                self._ready.set_exception(SidebandError("Sideband closed before session binding"))
            if self._summary_future is not None and not self._summary_future.done():
                self._summary_future.set_exception(SidebandError("Sideband closed before summary"))
            if was_bound and not self._closing:
                asyncio.create_task(self._on_end(self.hub_session_id))

    async def handle_event(self, event: dict[str, Any]) -> None:
        event_type = str(event.get("type") or "unknown")
        self._lifecycle[event_type] += 1
        if event_type == "response.audio_transcript.done":
            self._remember_transcript(event.get("transcript"))
        elif event_type in {"conversation.item.created", "conversation.item.done"}:
            item = event.get("item") or {}
            for content in item.get("content", []) if isinstance(item, dict) else []:
                if isinstance(content, dict):
                    self._remember_transcript(content.get("transcript") or content.get("text"))
        if event_type == "session.created":
            realtime_id = str((event.get("session") or {}).get("id") or "")
            if realtime_id != self.expected_realtime_session_id:
                error = SidebandError("Realtime session binding mismatch")
                if not self._ready.done():
                    self._ready.set_exception(error)
                return
            if not self._ready.done():
                self._ready.set_result(True)
            return
        if event_type == "session.updated":
            current = event.get("session") or {}
            audio = current.get("audio") or {}
            output = audio.get("output") or {} if isinstance(audio, dict) else {}
            if (
                current.get("instructions") != self.instructions
                or current.get("tools") not in (None, [])
                or current.get("tool_choice") not in (None, "none")
                or output.get("voice") not in (None, self.voice)
            ) and not self._closing:
                await self._send_authoritative_session_update()
            return
        if event_type != "response.done":
            return
        response = event.get("response") or {}
        usage = response.get("usage") or {}
        if isinstance(usage, dict):
            for key in ("total_tokens", "input_tokens", "output_tokens"):
                value = usage.get(key)
                if isinstance(value, int) and value >= 0:
                    self._usage[key] += value
        metadata = response.get("metadata") or {}
        if (
            self._summary_future is not None
            and not self._summary_future.done()
            and isinstance(metadata, dict)
            and metadata.get("voice_hub_summary") == self._summary_marker
        ):
            text = extract_response_text(response)
            if text:
                self._summary_future.set_result(text)
            else:
                self._summary_future.set_exception(SidebandError("Realtime summary was empty"))

    def _remember_transcript(self, value: Any) -> None:
        if not isinstance(value, str):
            return
        clean = " ".join(value.split()).strip()
        if not clean:
            return
        self._transcript_parts.append(clean[:4000])
        while sum(len(part) for part in self._transcript_parts) > 16_000:
            self._transcript_parts.pop(0)

    def transcript_summary(self) -> str:
        text = "\n".join(self._transcript_parts).strip()
        if not text:
            return "Сессия завершена; серверная текстовая расшифровка недоступна."
        return text[-4000:]

    async def _send_authoritative_session_update(self) -> None:
        await self.send(
            {
                "type": "session.update",
                "session": {
                    "type": "realtime",
                    "instructions": self.instructions,
                    "tools": [],
                    "tool_choice": "none",
                    "audio": {"output": {"voice": self.voice}},
                },
            }
        )

    async def send(self, event: dict[str, Any]) -> None:
        if self._ws is None or self._ws.closed:
            raise SidebandError("Realtime sideband is not connected")
        await self._ws.send_json(event)

    async def request_summary(self, *, timeout: float = 12) -> str:
        if self._summary_future is not None:
            return await asyncio.wait_for(asyncio.shield(self._summary_future), timeout=timeout)
        marker = secrets.token_urlsafe(18)
        self._summary_marker = marker
        self._summary_future = asyncio.get_running_loop().create_future()
        await self.send(
            {
                "type": "response.create",
                "response": {
                    "conversation": "auto",
                    "output_modalities": ["text"],
                    "metadata": {"voice_hub_summary": marker},
                    "instructions": (
                        "Create a concise Russian internal summary of this conversation. "
                        "Include only decisions, useful context and open questions. "
                        "Do not invent facts, secrets or personal data not stated in the conversation."
                    ),
                },
            }
        )
        return await asyncio.wait_for(asyncio.shield(self._summary_future), timeout=timeout)

    async def hangup(self) -> None:
        self._closing = True
        last_error: Exception | None = None
        for attempt in range(2):
            try:
                async with self._client_session.post(
                    f"{REALTIME_CALLS_URL}/{quote(self.call_id, safe='')}/hangup",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                ) as response:
                    if response.status in {200, 404}:
                        return
                    last_error = SidebandError("OpenAI rejected Realtime hangup")
            except (asyncio.TimeoutError, ClientError) as exc:
                last_error = SidebandError("Realtime hangup request failed")
                last_error.__cause__ = exc
            if attempt == 0:
                await asyncio.sleep(0.2)
        assert last_error is not None
        raise last_error

    def snapshot(self) -> dict[str, Any]:
        return {
            "usage": dict(self._usage),
            "lifecycle": dict(self._lifecycle),
        }

    async def _enforce_deadline(self) -> None:
        delay = max(0, self.deadline - self._clock())
        await asyncio.sleep(delay)
        if not self._closing:
            await self._on_limit(self.hub_session_id)

    async def close(self) -> None:
        self._closing = True
        current = asyncio.current_task()
        cancelled: list[asyncio.Task[None]] = []
        for task in (self._limit_task, self._reader_task):
            if task is not None and task is not current and not task.done():
                task.cancel()
                cancelled.append(task)
        if self._ws is not None and not self._ws.closed:
            await self._ws.close()
        if cancelled:
            await asyncio.gather(*cancelled, return_exceptions=True)


class SidebandManager:
    def __init__(
        self,
        api_key: str,
        *,
        client_session: ClientSession | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._api_key = api_key
        self._client_session = client_session
        self._owns_client = client_session is None
        self._clock = clock
        self._controls: dict[str, SidebandControl] = {}
        self._lock = asyncio.Lock()

    async def attach(
        self,
        *,
        hub_session_id: str,
        call_id: str,
        expected_realtime_session_id: str,
        instructions: str,
        voice: str,
        deadline: int,
        on_limit: Callable[[str], Awaitable[None]],
        on_end: Callable[[str], Awaitable[None]],
    ) -> SidebandControl:
        async with self._lock:
            if hub_session_id in self._controls:
                existing = self._controls[hub_session_id]
                if existing.call_id != call_id:
                    raise SidebandError("Hub session is already bound to another call")
                return existing
            if self._client_session is None:
                self._client_session = ClientSession(timeout=ClientTimeout(total=15))
            control = SidebandControl(
                api_key=self._api_key,
                hub_session_id=hub_session_id,
                call_id=call_id,
                expected_realtime_session_id=expected_realtime_session_id,
                instructions=instructions,
                voice=voice,
                deadline=deadline,
                on_limit=on_limit,
                on_end=on_end,
                client_session=self._client_session,
                clock=self._clock,
            )
            self._controls[hub_session_id] = control
        try:
            await control.connect()
        except Exception:
            async with self._lock:
                self._controls.pop(hub_session_id, None)
            raise
        return control

    def get(self, hub_session_id: str) -> SidebandControl | None:
        return self._controls.get(hub_session_id)

    async def remove(self, hub_session_id: str) -> None:
        async with self._lock:
            control = self._controls.pop(hub_session_id, None)
        if control is not None:
            await control.close()

    async def close(self) -> None:
        controls = list(self._controls.values())
        self._controls.clear()
        for control in controls:
            await control.close()
        if self._owns_client and self._client_session is not None:
            await self._client_session.close()
            self._client_session = None
