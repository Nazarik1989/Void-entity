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
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Mapping, Protocol
from urllib.parse import parse_qsl

from aiohttp import ClientError, ClientSession, ClientTimeout, web
from dotenv import load_dotenv


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
    host: str = "127.0.0.1"
    port: int = 8080

    @classmethod
    def from_env(cls) -> "VoiceHubConfig":
        load_dotenv()
        model_override = os.getenv("OPENAI_REALTIME_MODEL", REALTIME_MODEL).strip()
        if model_override != REALTIME_MODEL:
            raise ValueError(f"OPENAI_REALTIME_MODEL must be exactly {REALTIME_MODEL}")
        allowed_values = frozenset(
            item.strip()
            for item in os.getenv("VOICE_HUB_ALLOWED_TELEGRAM_USER_IDS", "").split(",")
            if item.strip()
        )
        if any(not item.isdigit() or int(item) <= 0 for item in allowed_values):
            raise ValueError("VOICE_HUB_ALLOWED_TELEGRAM_USER_IDS must contain positive integers")
        allowed = frozenset(str(int(item)) for item in allowed_values)
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


class RealtimeTokenProvider(Protocol):
    async def mint(self, *, persona: str, safety_identifier: str) -> EphemeralToken:
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

    def session_payload(self, persona: str) -> dict[str, Any]:
        persona_config = PERSONAS.get(persona)
        if persona_config is None:
            raise AuthorizationError("Persona is not allowed")
        return {
            "expires_after": {"anchor": "created_at", "seconds": self._ephemeral_ttl_seconds},
            "session": {
                "type": "realtime",
                "model": REALTIME_MODEL,
                "instructions": persona_config["instructions"],
                "output_modalities": ["audio"],
                "audio": {
                    "input": {"turn_detection": {"type": "server_vad"}},
                    "output": {"voice": persona_config["voice"]},
                },
            },
        }

    async def mint(self, *, persona: str, safety_identifier: str) -> EphemeralToken:
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
                json=self.session_payload(persona),
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
        except (KeyError, TypeError, ValueError) as exc:
            raise UpstreamError("OpenAI response did not contain token expiry") from exc
        if not value.startswith("ek_"):
            raise UpstreamError("OpenAI response did not contain an ephemeral token")
        return EphemeralToken(value=value, expires_at=expires_at)


@dataclass(frozen=True)
class SummaryEnvelope:
    idempotency_key: str
    platform: str
    user_id: str
    persona: str
    summary: str
    started_at: int
    ended_at: int


class PersonaSummaryAdapter(Protocol):
    async def deliver(self, envelope: SummaryEnvelope) -> str:
        ...


class AcceptedSummaryAdapter:
    """Safe default adapter seam; replace with a Naz/VOID persistence adapter at integration."""

    async def deliver(self, envelope: SummaryEnvelope) -> str:
        return f"accepted:{envelope.persona}:{envelope.idempotency_key}"


@dataclass
class VoiceSession:
    session_id: str
    identity: LaunchIdentity
    persona: str
    started_at: int
    expires_at: int
    state: str = "minting"
    summary_receipt: str | None = None
    delivery_future: asyncio.Future[str] | None = field(default=None, repr=False)


class SessionRegistry:
    def __init__(self, *, clock: Callable[[], float] = time.time) -> None:
        self._clock = clock
        self._lock = asyncio.Lock()
        self._sessions: dict[str, VoiceSession] = {}
        self._active_by_user: dict[tuple[str, str], str] = {}

    async def reserve(self, identity: LaunchIdentity, persona: str, duration_seconds: int) -> VoiceSession:
        now = int(self._clock())
        user_key = (identity.platform, identity.user_id)
        async with self._lock:
            active_id = self._active_by_user.get(user_key)
            if active_id:
                active = self._sessions[active_id]
                if active.state in {"minting", "active", "delivering"} and active.expires_at > now:
                    raise ActiveSessionError("User already has an active voice session")
                self._active_by_user.pop(user_key, None)
            session = VoiceSession(
                session_id=secrets.token_urlsafe(24),
                identity=identity,
                persona=persona,
                started_at=now,
                expires_at=now + duration_seconds,
            )
            self._sessions[session.session_id] = session
            self._active_by_user[user_key] = session.session_id
            return session

    async def activate(self, session_id: str) -> None:
        async with self._lock:
            self._sessions[session_id].state = "active"

    async def release_failed_reservation(self, session_id: str) -> None:
        async with self._lock:
            session = self._sessions.pop(session_id, None)
            if session:
                self._active_by_user.pop((session.identity.platform, session.identity.user_id), None)

    async def get_owned(self, session_id: str, identity: LaunchIdentity) -> VoiceSession:
        async with self._lock:
            session = self._sessions.get(session_id)
            if session is None or session.identity != identity:
                raise SessionNotFoundError("Voice session was not found")
            return session

    async def begin_delivery(self, session: VoiceSession) -> tuple[asyncio.Future[str], bool]:
        async with self._lock:
            if session.summary_receipt is not None:
                future = asyncio.get_running_loop().create_future()
                future.set_result(session.summary_receipt)
                return future, False
            if session.delivery_future is not None:
                return session.delivery_future, False
            future = asyncio.get_running_loop().create_future()
            session.delivery_future = future
            session.state = "delivering"
            return future, True

    async def complete_delivery(self, session: VoiceSession, receipt: str) -> None:
        async with self._lock:
            session.summary_receipt = receipt
            session.state = "finished"
            self._active_by_user.pop((session.identity.platform, session.identity.user_id), None)
            future = session.delivery_future
            session.delivery_future = None
            if future is not None and not future.done():
                future.set_result(receipt)

    async def fail_delivery(self, session: VoiceSession, exc: Exception) -> None:
        async with self._lock:
            session.state = "active"
            future = session.delivery_future
            session.delivery_future = None
            if future is not None and not future.done():
                future.set_exception(exc)


class VoiceHubService:
    def __init__(
        self,
        config: VoiceHubConfig,
        token_provider: RealtimeTokenProvider,
        *,
        verifiers: Mapping[str, LaunchVerifier],
        summary_adapters: Mapping[str, PersonaSummaryAdapter],
        registry: SessionRegistry | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.config = config
        self._token_provider = token_provider
        self._verifiers = dict(verifiers)
        self._summary_adapters = dict(summary_adapters)
        self._registry = registry or SessionRegistry(clock=clock)
        self._clock = clock

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
        if normalized not in PERSONAS or normalized not in self._summary_adapters:
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
        session = await self._registry.reserve(
            identity, allowed_persona, self.config.max_session_duration_seconds
        )
        try:
            token = await self._token_provider.mint(
                persona=allowed_persona,
                safety_identifier=self._safety_identifier(identity),
            )
        except Exception:
            await self._registry.release_failed_reservation(session.session_id)
            raise
        await self._registry.activate(session.session_id)
        return {
            "session_id": session.session_id,
            "ephemeral_token": token.value,
            "token_expires_at": token.expires_at,
            "session_expires_at": session.expires_at,
            "max_duration_seconds": self.config.max_session_duration_seconds,
            "model": REALTIME_MODEL,
            "persona": allowed_persona,
        }

    async def finish_session(
        self,
        session_id: str,
        platform: str,
        launch_data: str,
        summary: str,
    ) -> dict[str, Any]:
        identity = self.authenticate(platform, launch_data)
        if not isinstance(summary, str):
            raise VoiceHubError("Summary must be text")
        clean_summary = " ".join(summary.split()).strip()
        if not clean_summary:
            clean_summary = "Сессия завершена без доступного текстового summary."
        if len(clean_summary) > MAX_SUMMARY_CHARS:
            raise VoiceHubError("Summary is too large")
        session = await self._registry.get_owned(session_id, identity)
        future, should_deliver = await self._registry.begin_delivery(session)
        if should_deliver:
            adapter = self._summary_adapters[session.persona]
            envelope = SummaryEnvelope(
                idempotency_key=session.session_id,
                platform=identity.platform,
                user_id=identity.user_id,
                persona=session.persona,
                summary=clean_summary,
                started_at=session.started_at,
                ended_at=int(self._clock()),
            )
            try:
                receipt = await adapter.deliver(envelope)
                await self._registry.complete_delivery(session, str(receipt))
            except Exception as exc:
                await self._registry.fail_delivery(session, exc)
                try:
                    await future
                except Exception:
                    pass
                raise
        receipt = await future
        return {"session_id": session.session_id, "status": "finished", "summary_receipt": receipt}


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
    summary_adapters: Mapping[str, PersonaSummaryAdapter] | None = None,
) -> web.Application:
    adapters = dict(summary_adapters or {name: AcceptedSummaryAdapter() for name in PERSONAS})
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
        summary_adapters=adapters,
    )
    app = web.Application(client_max_size=32 * 1024, middlewares=[error_middleware])
    app["voice_hub_service"] = service
    static_dir = Path(__file__).with_name("voice_hub_web")

    async def index(_: web.Request) -> web.FileResponse:
        return web.FileResponse(static_dir / "index.html")

    async def start(request: web.Request) -> web.Response:
        payload = await _read_json(request)
        result = await service.start_session(
            str(payload.get("platform") or "telegram"),
            str(payload.get("launch_data") or ""),
            str(payload.get("persona") or ""),
        )
        return web.json_response(result, status=201, headers={"Cache-Control": "no-store"})

    async def finish(request: web.Request) -> web.Response:
        payload = await _read_json(request)
        result = await service.finish_session(
            request.match_info["session_id"],
            str(payload.get("platform") or "telegram"),
            str(payload.get("launch_data") or ""),
            payload.get("summary", ""),
        )
        return web.json_response(result, headers={"Cache-Control": "no-store"})

    app.router.add_get("/voice", index)
    app.router.add_post("/api/voice/sessions", start)
    app.router.add_post("/api/voice/sessions/{session_id}/finish", finish)
    app.router.add_static("/voice/static", static_dir, show_index=False)
    return app


def main() -> None:
    parser = argparse.ArgumentParser(description="Naz/VOID Realtime Voice Hub MVP")
    parser.parse_args()
    config = VoiceHubConfig.from_env()
    web.run_app(create_app(config), host=config.host, port=config.port)


if __name__ == "__main__":
    main()
