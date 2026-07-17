const REALTIME_CALLS_URL = "https://api.openai.com/v1/realtime/calls";
const tg = window.Telegram?.WebApp;
const launchData = tg?.initData || "";
const startButton = document.querySelector("#start");
const stopButton = document.querySelector("#stop");
const statusNode = document.querySelector("#status");
const remoteAudio = document.querySelector("#remote-audio");

let active = null;
let ending = false;
let durationTimer = null;

tg?.ready();
tg?.expand();

function setStatus(message) {
  statusNode.textContent = message;
}

async function postJson(url, body) {
  const response = await fetch(url, {
    method: "POST",
    credentials: "same-origin",
    cache: "no-store",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const payload = await response.json().catch(() => ({ error: "invalid_response" }));
  if (!response.ok) throw new Error(payload.error || `http_${response.status}`);
  return payload;
}

function closeMedia() {
  if (!active) return;
  for (const track of active.stream.getTracks()) track.stop();
  active.channel?.close();
  active.peer.close();
  remoteAudio.srcObject = null;
}

async function finishSession(reason = "user") {
  if (!active || ending) return;
  ending = true;
  clearTimeout(durationTimer);
  startButton.disabled = true;
  stopButton.disabled = true;
  for (const track of active.stream.getAudioTracks()) track.enabled = false;
  setStatus(reason === "limit" ? "Лимит времени достигнут. Завершаю…" : "Завершаю сессию…");
  const current = active;
  try {
    await postJson(`/api/voice/sessions/${encodeURIComponent(current.sessionId)}/finish`, {
      platform: "telegram",
      launch_data: launchData,
    });
    setStatus("Сессия завершена.");
  } catch {
    setStatus("Связь закрыта; серверное завершение не удалось подтвердить.");
  } finally {
    closeMedia();
    active = null;
    ending = false;
    startButton.disabled = false;
  }
}

async function startSession() {
  if (active || ending) return;
  if (!launchData) {
    setStatus("Откройте Voice Hub из Telegram Mini App.");
    return;
  }
  startButton.disabled = true;
  setStatus("Запрашиваю микрофон…");
  let stream;
  try {
    stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    const persona = document.querySelector('input[name="persona"]:checked').value;
    const session = await postJson("/api/voice/sessions", {
      platform: "telegram",
      launch_data: launchData,
      persona,
    });
    const peer = new RTCPeerConnection();
    const channel = peer.createDataChannel("oai-events");
    peer.addEventListener("track", (event) => { remoteAudio.srcObject = event.streams[0]; });
    for (const track of stream.getAudioTracks()) peer.addTrack(track, stream);
    active = { sessionId: session.session_id, stream, peer, channel };

    peer.addEventListener("connectionstatechange", () => {
      if (["failed", "closed"].includes(peer.connectionState) && active && !ending) {
        void finishSession("connection");
      }
    });
    const offer = await peer.createOffer();
    await peer.setLocalDescription(offer);
    const answerResponse = await fetch(REALTIME_CALLS_URL, {
      method: "POST",
      headers: {
        "Authorization": `Bearer ${session.ephemeral_token}`,
        "Content-Type": "application/sdp",
      },
      body: offer.sdp,
    });
    if (!answerResponse.ok) throw new Error("realtime_connection_failed");
    const location = answerResponse.headers.get("Location") || "";
    const callId = location.split("/").filter(Boolean).at(-1) || "";
    if (!/^rtc_[A-Za-z0-9_-]{8,200}$/.test(callId)) throw new Error("realtime_call_id_missing");
    await postJson(`/api/voice/sessions/${encodeURIComponent(session.session_id)}/attach`, {
      platform: "telegram",
      launch_data: launchData,
      call_id: callId,
    });
    await peer.setRemoteDescription({ type: "answer", sdp: await answerResponse.text() });
    durationTimer = setTimeout(() => void finishSession("limit"), session.max_duration_seconds * 1000);
    stopButton.disabled = false;
    setStatus(`Подключено: ${session.persona === "naz" ? "Naz" : "VOID"}.`);
  } catch (error) {
    if (active) {
      await finishSession("connection");
    } else {
      for (const track of stream?.getTracks() || []) track.stop();
      startButton.disabled = false;
      setStatus(error.message === "active_session_exists" ? "У вас уже есть активная сессия." : "Не удалось подключиться.");
    }
  }
}

startButton.addEventListener("click", () => void startSession());
stopButton.addEventListener("click", () => void finishSession("user"));

window.addEventListener("pagehide", () => {
  if (!active) return;
  clearTimeout(durationTimer);
  const body = new Blob([JSON.stringify({
    platform: "telegram",
    launch_data: launchData,
  })], { type: "application/json" });
  navigator.sendBeacon(`/api/voice/sessions/${encodeURIComponent(active.sessionId)}/finish`, body);
  closeMedia();
});
