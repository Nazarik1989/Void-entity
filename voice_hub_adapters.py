"""Narrow persona adapter contracts for the Realtime Voice Hub."""
from __future__ import annotations

import asyncio
import json
import secrets
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Protocol


NAZ_ADAPTER_PROTOCOL = "voice_hub.naz.v1"
MAX_IPC_BYTES = 64 * 1024


class PersonaAdapter(Protocol):
    async def instructions(self, user_id: str) -> str:
        ...

    async def deliver(self, envelope: Any) -> str:
        ...


class VoidSqlitePersonaAdapter:
    """Project-local VOID adapter; never accesses the independent Naz database."""

    def __init__(self, db_path: Path, instructions: str) -> None:
        self._db_path = Path(db_path)
        self._instructions = instructions

    async def instructions(self, user_id: str) -> str:
        if not str(user_id).isdigit():
            raise TypeError("VOID user_id must be numeric")
        return self._instructions

    async def deliver(self, envelope: Any) -> str:
        if envelope.persona != "void" or not str(envelope.user_id).isdigit():
            raise ValueError("VOID adapter envelope is invalid")
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        conn = sqlite3.connect(self._db_path, timeout=5)
        try:
            with conn:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS voice_hub_deliveries (
                        idempotency_key TEXT PRIMARY KEY,
                        user_id INTEGER NOT NULL,
                        created_at TEXT NOT NULL
                    )
                    """
                )
                existing = conn.execute(
                    "SELECT user_id FROM voice_hub_deliveries WHERE idempotency_key=?",
                    (envelope.session_id,),
                ).fetchone()
                if existing is not None:
                    if int(existing[0]) != int(envelope.user_id):
                        raise PermissionError("Idempotency key belongs to another user")
                    return f"void:{envelope.session_id}"
                conn.execute(
                    """
                    INSERT INTO dialog_messages(user_id, role, content, created_at)
                    VALUES (?, 'memory', ?, ?)
                    """,
                    (int(envelope.user_id), envelope.summary, now),
                )
                conn.execute(
                    """
                    INSERT INTO voice_hub_deliveries(idempotency_key, user_id, created_at)
                    VALUES (?, ?, ?)
                    """,
                    (envelope.session_id, int(envelope.user_id), now),
                )
        finally:
            conn.close()
        return f"void:{envelope.session_id}"


async def unix_json_request(socket_path: Path, payload: dict[str, Any]) -> dict[str, Any]:
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n"
    if len(encoded) > MAX_IPC_BYTES:
        raise ValueError("Naz adapter request is too large")
    reader, writer = await asyncio.wait_for(
        asyncio.open_unix_connection(str(socket_path)), timeout=2
    )
    try:
        writer.write(encoded)
        await asyncio.wait_for(writer.drain(), timeout=2)
        raw = await asyncio.wait_for(reader.readline(), timeout=5)
    finally:
        writer.close()
        await writer.wait_closed()
    if not raw or len(raw) > MAX_IPC_BYTES:
        raise RuntimeError("Naz adapter returned an invalid response")
    try:
        response = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("Naz adapter returned invalid JSON") from exc
    if not isinstance(response, dict):
        raise RuntimeError("Naz adapter returned an invalid object")
    return response


class NazUnixPersonaAdapter:
    def __init__(
        self,
        socket_path: Path,
        *,
        request: Callable[[Path, dict[str, Any]], Awaitable[dict[str, Any]]] = unix_json_request,
    ) -> None:
        self._socket_path = Path(socket_path)
        self._request = request

    async def _call(self, operation: str, **values: Any) -> dict[str, Any]:
        request_id = secrets.token_urlsafe(18)
        response = await self._request(
            self._socket_path,
            {
                "protocol": NAZ_ADAPTER_PROTOCOL,
                "request_id": request_id,
                "operation": operation,
                **values,
            },
        )
        if response.get("request_id") != request_id:
            raise RuntimeError("Naz adapter response ID mismatch")
        if response.get("ok") is not True:
            raise PermissionError(str(response.get("error") or "naz_adapter_rejected"))
        return response

    async def instructions(self, user_id: str) -> str:
        if not str(user_id).isdigit():
            raise TypeError("Naz user_id must be numeric")
        response = await self._call("persona_instructions", user_id=int(user_id))
        instructions = response.get("instructions")
        if not isinstance(instructions, str) or not instructions.strip() or len(instructions) > 32_000:
            raise RuntimeError("Naz adapter returned invalid instructions")
        return instructions

    async def deliver(self, envelope: Any) -> str:
        if envelope.persona != "naz" or not str(envelope.user_id).isdigit():
            raise ValueError("Naz adapter envelope is invalid")
        response = await self._call(
            "final_summary",
            user_id=int(envelope.user_id),
            session_id=envelope.session_id,
            summary=envelope.summary,
        )
        if not isinstance(response.get("saved"), bool):
            raise RuntimeError("Naz adapter did not return a saved result")
        receipt = response.get("receipt")
        if not isinstance(receipt, str) or not receipt:
            raise RuntimeError("Naz adapter did not return a receipt")
        return receipt
