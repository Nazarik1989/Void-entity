"""Minimal shared Realtime Voice Hub for Telegram Mini Apps."""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import hmac
import json
import os
import secrets
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable, Mapping, Protocol
from urllib.parse import parse_qsl

from aiohttp import ClientError, ClientSession, ClientTimeout, web

from realtime_sideband import CALL_ID_RE, SidebandError, SidebandManager
from voice_hub_adapters import NazUnixPersonaAdapter, PersonaAdapter, VoidSqlitePersonaAdapter
from voice_hub_store import StoreConflictError, StoredSession, VoiceHubStore


REALTIME_MODEL = "gpt-realtime-2.1"
OPENAI_CLIENT_SECRETS_URL = "https://api.openai.com/v1/realtime/client_secrets"
MAX_LAUNCH_DATA_BYTES = 16_384
MAX_SUMMARY_CHARS = 6_000

PERSONAS: dict[str, dict[str, str]] = {
    "naz": {
        "label": "Naz",
        "voice": "marin",
        "instructions": (
            "Ты Naz. Говори естественно по-русски: любопытно, быстро и без пафоса. "
            "Ты молодой практичный билдер, честно признаёшь неизвестное и проверяешь идеи вопросами. "
            "Не выдавай себя за человека и не проси секреты или персональные данные."
        ),
    },
    "void": {
        "label": "VOID",
        "voice": "marin",
        "instructions": (
            "Ты VOID. Говори естественно по-русски: спокойно, точно, кратко, с лёгкой сухой иронией. "
            "Замечай человека внутри цифрового шума, не морализируй и не изображай всезнающего гуру. "
            "Не выдавай себя за человека и не проси секреты или персональные данные."
        ),
    },
}


class VoiceHubError(RuntimeError):
    status = 400
    code = "voice_hub_error"


class AuthenticationError(VoiceHubError):
    status = 401
    code = "invalid_launch_data"


class AuthorizationError(VoiceHubError):
    status = 403
    code = "not_allowed"


class ActiveSessionError(VoiceHubError):
    status = 409
    code = "active_session_exists"


class SessionNotFoundError(VoiceHubError):
    status = 404
    code = "session_not_found"


class UpstreamError(VoiceHubError):
    status = 502
    code = "realtime_unavailable"


class VkVerificationNotConfigured(VoiceHubError):
    status = 501
    code = "vk_verification_not_configured"


@dataclass(frozen=True)
class LaunchIdentity:
    platform: str
    user_id: str


class LaunchVerifier(Protocol):
    def verify(self, launch_data: str, *, now: int | None = None) -> LaunchIdentity:
        ...


class TelegramLaunchVerifier:
    def __init__(self, bot_token: str, *, max_age_seconds: int = 300) -> None:
        if not bot_token:
            raise ValueError("BOT_TOKEN is required")
        if max_age_seconds < 30:
            raise ValueError("Telegram launch max age must be at least 30 seconds")
        self._bot_token = bot_token
        self._max_age_seconds = max_age_seconds

    def verify(self, launch_data: str, *, now: int | None = None) -> LaunchIdentity:
        if not isinstance(launch_data, str) or not launch_data:
            raise AuthenticationError("Telegram launch data is missing")
        if len(launch_data.encode("utf-8")) > MAX_LAUNCH_DATA_BYTES:
            raise AuthenticationError("Telegram launch data is too large")
        try:
            pairs = parse_qsl(launch_data, keep_blank_values=True, strict_parsing=True)
        except ValueError as exc:
            raise AuthenticationError("Telegram launch data is malformed") from exc
        if not pairs or len({key for key, _ in pairs}) != len(pairs):
            raise AuthenticationError("Telegram launch data contains duplicate fields")
        values = dict(pairs)
        received_hash = values.pop("hash", "")
        if len(received_hash) != 64:
            raise AuthenticationError("Telegram launch hash is invalid")
        data_check_string = "\n".join(f"{key}={value}" for key, value in sorted(values.items()))
        secret_key = hmac.new(b"WebAppData", self._bot_token.encode("utf-8"), hashlib.sha256).digest()
        expected_hash = hmac.new(secret_key, data_check_string.encode("utf-8"), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(received_hash, expected_hash):
            raise AuthenticationError("Telegram launch signature is invalid")

        current_time = int(time.time()) if now is None else int(now)
        try:
            auth_date = int(values["auth_date"])
        except (KeyError, TypeError, ValueError) as exc:
            raise AuthenticationError("Telegram auth_date is invalid") from exc
        if auth_date > current_time + 30 or current_time - auth_date > self._max_age_seconds:
            raise AuthenticationError("Telegram launch data is stale")
        try:
            user = json.loads(values["user"])
            user_id = int(user["id"])
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise AuthenticationError("Telegram user is invalid") from exc
        if user_id <= 0:
            raise AuthenticationError("Telegram user is invalid")
        return LaunchIdentity(platform="telegram", user_id=str(user_id))


class VkLaunchVerifier:
    """Interface placeholder: no VK launch contract is assumed by this MVP."""

    def verify(self, launch_data: str, *, now: int | None = None) -> LaunchIdentity:
        raise VkVerificationNotConfigured("VK launch verification is not configured")


@dataclass(frozen=True)
class VoiceHubConfig:
    openai_api_key: str
    telegram_bot_token: str
    allowed_telegram_user_ids: frozenset[str]
    max_session_duration_seconds: int = 900
    telegram_launch_max_age_seconds: int = 300
    ephemeral_token_ttl_seconds: int = 60
    state_db_path: Path = Path("data/voice_hub.sqlite3")
    naz_adapter_socket: Path = Path("/run/naz-realtime/adapter.sock")
    void_db_path: Path = Path("void.db")
    host: str = "127.0.0.1"
    port: int = 8080

    @classmethod
    def from_env(cls) -> "VoiceHubConfig":
        model_override = os.getenv("OPENAI_REALTIME_MODEL", REALTIME_MODEL).strip()
        if model_override != REALTIME_MODEL:
            raise ValueError(f"OPENAI_REALTIME_MODEL must be exactly {REALTIME_MODEL}")
        allowlist_source = os.getenv("VOICE_HUB_ALLOWED_TELEGRAM_USER_IDS")
        if allowlist_source is None:
            allowlist_source = os.getenv("ADMIN_ID", "")
        allowed_values = frozenset(
            item.strip()
            for item in allowlist_source.split(",")
            if item.strip()
        )
        if any(not item.isdigit() or int(item) <= 0 for item in allowed_values):
            raise ValueError("VOICE_HUB_ALLOWED_TELEGRAM_USER_IDS must contain positive integers")
        allowed = frozenset(str(int(item)) for item in allowed_values)
        if not allowed:
            raise ValueError("Voice Hub Telegram allowlist must not be empty")
        duration = int(os.getenv("VOICE_HUB_MAX_SESSION_SECONDS", "900"))
        if not 30 <= duration <= 3600:
            raise ValueError("VOICE_HUB_MAX_SESSION_SECONDS must be between 30 and 3600")
        ttl = int(os.getenv("VOICE_HUB_EPHEMERAL_TTL_SECONDS", "60"))
        if not 30 <= ttl <= 600:
            raise ValueError("VOICE_HUB_EPHEMERAL_TTL_SECONDS must be between 30 and 600")
        config = cls(
            openai_api_key=os.getenv("OPENAI_VOICE_API_KEY", "").strip(),
            telegram_bot_token=os.getenv("BOT_TOKEN", "").strip(),
            allowed_telegram_user_ids=allowed,
            max_session_duration_seconds=duration,
            telegram_launch_max_age_seconds=int(os.getenv("VOICE_HUB_TELEGRAM_AUTH_MAX_AGE_SECONDS", "300")),
            ephemeral_token_ttl_seconds=ttl,
            state_db_path=Path(os.getenv("VOICE_HUB_STATE_DB", "data/voice_hub.sqlite3")),
            naz_adapter_socket=Path(
                os.getenv("VOICE_HUB_NAZ_ADAPTER_SOCKET", "/run/naz-realtime/adapter.sock")
            ),
            void_db_path=Path(os.getenv("DB_PATH", "void.db")),
            host=os.getenv("VOICE_HUB_HOST", "127.0.0.1").strip(),
            port=int(os.getenv("VOICE_HUB_PORT", "8080")),
        )
        if not config.openai_api_key:
            raise ValueError("OPENAI_VOICE_API_KEY is required")
        if not config.telegram_bot_token:
            raise ValueError("BOT_TOKEN is required")
        return config


@dataclass(frozen=True)
class EphemeralToken:
    value: str
    expires_at: int
    realtime_session_id: str


class RealtimeTokenProvider(Protocol):
    async def mint(
        self, *, persona: str, instructions: str, safety_identifier: str
    ) -> EphemeralToken:
        ...


class OpenAIRealtimeTokenProvider:
    def __init__(
        self,
        api_key: str,
        *,
        ephemeral_ttl_seconds: int = 60,
        client_session: ClientSession | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("OpenAI API key is required")
        self._api_key = api_key
        self._ephemeral_ttl_seconds = ephemeral_ttl_seconds
        self._client_session = client_session

    def session_payload(self, persona: str, instructions: str | None = None) -> dict[str, Any]:
        persona_config = PERSONAS.get(persona)
        if persona_config is None:
            raise AuthorizationError("Persona is not allowed")
        return {
            "expires_after": {"anchor": "created_at", "seconds": self._ephemeral_ttl_seconds},
            "session": {
                "type": "realtime",
                "model": REALTIME_MODEL,
                "instructions": instructions or persona_config["instructions"],
                "output_modalities": ["audio"],
                "audio": {
                    "input": {"turn_detection": {"type": "server_vad"}},
                    "output": {"voice": persona_config["voice"]},
                },
            },
        }

    async def mint(
        self, *, persona: str, instructions: str, safety_identifier: str
    ) -> EphemeralToken:
        own_session = self._client_session is None
        session = self._client_session or ClientSession(timeout=ClientTimeout(total=15))
        try:
            async with session.post(
                OPENAI_CLIENT_SECRETS_URL,
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                    "OpenAI-Safety-Identifier": safety_identifier,
                },
                json=self.session_payload(persona, instructions),
            ) as response:
                try:
                    payload = await response.json()
                except (json.JSONDecodeError, ValueError) as exc:
                    raise UpstreamError("OpenAI returned an invalid response") from exc
                if response.status != 200:
                    raise UpstreamError("OpenAI rejected the Realtime token request")
        except (asyncio.TimeoutError, ClientError) as exc:
            raise UpstreamError("OpenAI Realtime token request failed") from exc
        finally:
            if own_session:
                await session.close()
        value = str(payload.get("value") or "")
        try:
            expires_at = int(payload["expires_at"])
            realtime_session_id = str(payload["session"]["id"])
        except (KeyError, TypeError, ValueError) as exc:
            raise UpstreamError("OpenAI response did not contain session binding") from exc
        if not value.startswith("ek_") or not realtime_session_id.startswith("sess_"):
            raise UpstreamError("OpenAI response did not contain an ephemeral token")
        return EphemeralToken(
            value=value,
            expires_at=expires_at,
            realtime_session_id=realtime_session_id,
        )


@dataclass(frozen=True)
class SummaryEnvelope:
    session_id: str
    platform: str
    user_id: str
    persona: str
    summary: str
    started_at: int
    ended_at: int


class VoiceHubService:
    def __init__(
        self,
        config: VoiceHubConfig,
        token_provider: RealtimeTokenProvider,
        *,
        verifiers: Mapping[str, LaunchVerifier],
        persona_adapters: Mapping[str, PersonaAdapter],
        store: VoiceHubStore,
        sidebands: SidebandManager,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.config = config
        self._token_provider = token_provider
        self._verifiers = dict(verifiers)
        self._persona_adapters = dict(persona_adapters)
        self._store = store
        self._sidebands = sidebands
        self._clock = clock
        self._instructions: dict[str, str] = {}
        self._finalize_locks: dict[str, asyncio.Lock] = {}

    def authenticate(self, platform: str, launch_data: str) -> LaunchIdentity:
        verifier = self._verifiers.get(platform)
        if verifier is None:
            raise AuthenticationError("Launch platform is not supported")
        identity = verifier.verify(launch_data)
        if identity.platform == "telegram" and identity.user_id not in self.config.allowed_telegram_user_ids:
            raise AuthorizationError("Telegram user is not allowlisted")
        return identity

    def _authorize_persona(self, persona: str) -> str:
        normalized = str(persona or "").strip().casefold()
        if normalized not in PERSONAS or normalized not in self._persona_adapters:
            raise AuthorizationError("Persona is not allowlisted")
        return normalized

    def _safety_identifier(self, identity: LaunchIdentity) -> str:
        digest = hmac.new(
            self.config.telegram_bot_token.encode("utf-8"),
            f"voice-hub:{identity.platform}:{identity.user_id}".encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        return f"voice-hub-{digest}"

    async def start_session(self, platform: str, launch_data: str, persona: str) -> dict[str, Any]:
        identity = self.authenticate(platform, launch_data)
        allowed_persona = self._authorize_persona(persona)
        try:
            session = self._store.reserve(
                session_id=secrets.token_urlsafe(24),
                platform=identity.platform,
                user_id=identity.user_id,
                persona=allowed_persona,
                duration_seconds=self.config.max_session_duration_seconds,
            )
        except StoreConflictError as exc:
            raise ActiveSessionError("User already has an active voice session") from exc
        try:
            instructions = await self._persona_adapters[allowed_persona].instructions(identity.user_id)
            token = await self._token_provider.mint(
                persona=allowed_persona,
                instructions=instructions,
                safety_identifier=self._safety_identifier(identity),
            )
        except Exception:
            self._store.delete_reservation(session.session_id)
            raise
        session = self._store.activate(session.session_id, token.realtime_session_id)
        self._instructions[session.session_id] = instructions
        return {
            "session_id": session.session_id,
            "ephemeral_token": token.value,
            "token_expires_at": token.expires_at,
            "session_expires_at": session.expires_at,
            "max_duration_seconds": self.config.max_session_duration_seconds,
            "model": REALTIME_MODEL,
            "persona": session.persona,
        }

    def _get_owned(self, session_id: str, identity: LaunchIdentity) -> StoredSession:
        session = self._store.get(session_id)
        if session is None or session.platform != identity.platform or session.user_id != identity.user_id:
            raise SessionNotFoundError("Voice session was not found")
        return session

    async def bind_call(
        self, session_id: str, platform: str, launch_data: str, call_id: str
    ) -> dict[str, Any]:
        identity = self.authenticate(platform, launch_data)
        session = self._get_owned(session_id, identity)
        if not CALL_ID_RE.fullmatch(call_id):
            raise VoiceHubError("Invalid Realtime call ID")
        if session.realtime_session_id is None:
            raise SessionNotFoundError("Realtime session binding is unavailable")
        instructions = self._instructions.get(session_id)
        if instructions is None:
            instructions = await self._persona_adapters[session.persona].instructions(session.user_id)
            self._instructions[session_id] = instructions
        try:
            await self._sidebands.attach(
                hub_session_id=session.session_id,
                call_id=call_id,
                expected_realtime_session_id=session.realtime_session_id,
                instructions=instructions,
                voice=PERSONAS[session.persona]["voice"],
                deadline=session.expires_at,
                on_limit=self._finish_on_limit,
                on_end=self._finish_on_end,
            )
            session = self._store.attach(session.session_id, call_id)
        except (SidebandError, StoreConflictError) as exc:
            raise VoiceHubError("Realtime call binding failed") from exc
        return {"session_id": session.session_id, "status": "attached", "expires_at": session.expires_at}

    async def finish_session(
        self, session_id: str, platform: str, launch_data: str
    ) -> dict[str, Any]:
        identity = self.authenticate(platform, launch_data)
        session = self._get_owned(session_id, identity)
        if session.state not in {"attached", "finalizing", "finished"}:
            raise VoiceHubError("Voice session is not bound to a Realtime call")
        return await self._finalize(session.session_id, reason="user")

    async def _finish_on_limit(self, session_id: str) -> None:
        try:
            await self._finalize(session_id, reason="server_limit")
        except Exception:
            return

    async def _finish_on_end(self, session_id: str) -> None:
        try:
            await self._finalize(session_id, reason="remote_end")
        except Exception:
            return

    async def _finalize(self, session_id: str, *, reason: str) -> dict[str, Any]:
        lock = self._finalize_locks.setdefault(session_id, asyncio.Lock())
        async with lock:
            session = self._store.get(session_id)
            if session is None:
                raise SessionNotFoundError("Voice session was not found")
            if session.state == "finished" and session.summary_receipt:
                return {
                    "session_id": session.session_id,
                    "status": "finished",
                    "summary_receipt": session.summary_receipt,
                }
            session = self._store.begin_finalization(session_id)
            control = self._sidebands.get(session_id)
            usage: dict[str, Any] = {}
            lifecycle: dict[str, Any] = {}
            if reason == "server_limit" and control is not None:
                await control.hangup()
            summary = session.summary
            if not summary and control is not None and reason != "server_limit":
                try:
                    summary = await control.request_summary(timeout=10)
                except (SidebandError, asyncio.TimeoutError):
                    summary = control.transcript_summary()
            if not summary and control is not None:
                summary = control.transcript_summary()
            if not summary:
                summary = "Сессия завершена; серверная текстовая расшифровка недоступна."
            summary = " ".join(summary.split()).strip()[:MAX_SUMMARY_CHARS]
            self._store.save_server_summary(session_id, summary)
            if control is not None:
                snapshot = control.snapshot()
                usage = dict(snapshot.get("usage") or {})
                lifecycle = dict(snapshot.get("lifecycle") or {})
                if reason != "server_limit":
                    await control.hangup()
            envelope = SummaryEnvelope(
                session_id=session.session_id,
                platform=session.platform,
                user_id=session.user_id,
                persona=session.persona,
                summary=summary,
                started_at=session.started_at,
                ended_at=int(self._clock()),
            )
            receipt = await self._persona_adapters[session.persona].deliver(envelope)
            session = self._store.finish(
                session_id,
                receipt=str(receipt),
                reason=reason,
                usage=usage,
                lifecycle=lifecycle,
            )
            await self._sidebands.remove(session_id)
            self._instructions.pop(session_id, None)
            self._finalize_locks.pop(session_id, None)
            return {
                "session_id": session.session_id,
                "status": "finished",
                "summary_receipt": session.summary_receipt,
            }

    async def recover(self) -> None:
        now = int(self._clock())
        for session in self._store.recoverable():
            if not session.call_id or not session.realtime_session_id:
                self._store.abandon(session.session_id, "restart_without_bound_call")
                continue
            try:
                instructions = await self._persona_adapters[session.persona].instructions(session.user_id)
                self._instructions[session.session_id] = instructions
                await self._sidebands.attach(
                    hub_session_id=session.session_id,
                    call_id=session.call_id,
                    expected_realtime_session_id=session.realtime_session_id,
                    instructions=instructions,
                    voice=PERSONAS[session.persona]["voice"],
                    deadline=session.expires_at,
                    on_limit=self._finish_on_limit,
                    on_end=self._finish_on_end,
                )
                if session.expires_at <= now:
                    await self._finalize(session.session_id, reason="server_limit")
                elif session.state == "finalizing":
                    await self._finalize(session.session_id, reason="recovery")
            except Exception:
                if session.summary or session.expires_at <= now:
                    await self._sidebands.hangup_call(session.call_id)
                    await self._finalize(session.session_id, reason="recovery")
                else:
                    raise SidebandError("Active Realtime call recovery failed")


def _json_error(code: str, status: int) -> web.Response:
    return web.json_response(
        {"error": code}, status=status, headers={"Cache-Control": "no-store"}
    )


async def _read_json(request: web.Request) -> dict[str, Any]:
    if request.content_type != "application/json":
        raise VoiceHubError("JSON body is required")
    try:
        payload = await request.json()
    except (json.JSONDecodeError, ValueError) as exc:
        raise VoiceHubError("JSON body is invalid") from exc
    if not isinstance(payload, dict):
        raise VoiceHubError("JSON object is required")
    return payload


def _validate_fields(
    payload: Mapping[str, Any], *, required: set[str], allowed: set[str]
) -> None:
    if set(payload) - allowed or not required.issubset(payload):
        raise VoiceHubError("Request fields are invalid")


@web.middleware
async def error_middleware(request: web.Request, handler: Callable[[web.Request], Awaitable[web.StreamResponse]]) -> web.StreamResponse:
    try:
        return await handler(request)
    except VoiceHubError as exc:
        return _json_error(exc.code, exc.status)
    except web.HTTPException:
        raise
    except Exception:
        return _json_error("internal_error", 500)


def create_app(
    config: VoiceHubConfig,
    *,
    token_provider: RealtimeTokenProvider | None = None,
    persona_adapters: Mapping[str, PersonaAdapter] | None = None,
    store: VoiceHubStore | None = None,
    sidebands: SidebandManager | None = None,
) -> web.Application:
    adapters = dict(
        persona_adapters
        or {
            "naz": NazUnixPersonaAdapter(config.naz_adapter_socket),
            "void": VoidSqlitePersonaAdapter(config.void_db_path, PERSONAS["void"]["instructions"]),
        }
    )
    sideband_manager = sidebands or SidebandManager(config.openai_api_key)
    service = VoiceHubService(
        config,
        token_provider or OpenAIRealtimeTokenProvider(
            config.openai_api_key,
            ephemeral_ttl_seconds=config.ephemeral_token_ttl_seconds,
        ),
        verifiers={
            "telegram": TelegramLaunchVerifier(
                config.telegram_bot_token,
                max_age_seconds=config.telegram_launch_max_age_seconds,
            ),
            "vk": VkLaunchVerifier(),
        },
        persona_adapters=adapters,
        store=store or VoiceHubStore(config.state_db_path),
        sidebands=sideband_manager,
    )
    app = web.Application(client_max_size=32 * 1024, middlewares=[error_middleware])
    app["voice_hub_service"] = service
    static_dir = Path(__file__).with_name("voice_hub_web")

    async def index(_: web.Request) -> web.FileResponse:
        return web.FileResponse(static_dir / "index.html")

    async def start(request: web.Request) -> web.Response:
        payload = await _read_json(request)
        _validate_fields(
            payload,
            required={"platform", "launch_data", "persona"},
            allowed={"platform", "launch_data", "persona"},
        )
        result = await service.start_session(
            str(payload.get("platform") or "telegram"),
            str(payload.get("launch_data") or ""),
            str(payload.get("persona") or ""),
        )
        return web.json_response(result, status=201, headers={"Cache-Control": "no-store"})

    async def attach(request: web.Request) -> web.Response:
        payload = await _read_json(request)
        _validate_fields(
            payload,
            required={"platform", "launch_data", "call_id"},
            allowed={"platform", "launch_data", "call_id"},
        )
        result = await service.bind_call(
            request.match_info["session_id"],
            str(payload["platform"]),
            str(payload["launch_data"]),
            str(payload["call_id"]),
        )
        return web.json_response(result, headers={"Cache-Control": "no-store"})

    async def finish(request: web.Request) -> web.Response:
        payload = await _read_json(request)
        _validate_fields(
            payload,
            required={"platform", "launch_data"},
            allowed={"platform", "launch_data"},
        )
        result = await service.finish_session(
            request.match_info["session_id"],
            str(payload["platform"]),
            str(payload["launch_data"]),
        )
        return web.json_response(result, headers={"Cache-Control": "no-store"})

    app.router.add_get("/voice", index)
    app.router.add_post("/api/voice/sessions", start)
    app.router.add_post("/api/voice/sessions/{session_id}/attach", attach)
    app.router.add_post("/api/voice/sessions/{session_id}/finish", finish)
    app.router.add_static("/voice/static", static_dir, show_index=False)

    async def startup(_: web.Application) -> None:
        await service.recover()

    async def cleanup(_: web.Application) -> None:
        await sideband_manager.close()

    app.on_startup.append(startup)
    app.on_cleanup.append(cleanup)
    return app


def main() -> None:
    parser = argparse.ArgumentParser(description="Naz/VOID Realtime Voice Hub MVP")
    parser.parse_args()
    config = VoiceHubConfig.from_env()
    web.run_app(create_app(config), host=config.host, port=config.port)


if __name__ == "__main__":
    main()
