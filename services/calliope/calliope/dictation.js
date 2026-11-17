/* Shared review-first dictation. UI/editor state stays with the host page. */
(() => {
  "use strict";
  const SPEECH_MIME_TYPES = ["audio/webm;codecs=opus", "audio/webm", "audio/mp4"];
  function speechInsertion(value, start, end, transcript) {
    const text = String(transcript || "").replace(/\r\n?/g, "\n").trim();
    if (!text) return "";
    const before = value.slice(0, start);
    const after = value.slice(end);
    const prefix = before && !/\s$/.test(before) && !/^[,.;:!?)}\]]/.test(text) ? " " : "";
    const suffix = after && !/^\s/.test(after) && !/[\s([{]$/.test(text) ? " " : "";
    return `${prefix}${text}${suffix}`;
  }


  function create(options) {
    const speech = options.state || { phase: "idle", chunks: [] };
    const state = {speech, get config() { return {speech_to_text: options.config()}; }};
    const transcripts = new Map();
    const authenticatedFetch = options.fetch || window.fetch.bind(window);
    const syncSpeechControls = () => options.onChange?.(speech);
    const syncSpeechPreviews = syncSpeechControls;
    const toast = (message, error) => options.onMessage?.(message, error);
    const speechSupported = () => Boolean(options.config()?.enabled && navigator.mediaDevices?.getUserMedia && window.MediaRecorder);
    const realtimeSpeechSupported = () => speechSupported() && options.config()?.realtime?.enabled && window.RTCPeerConnection;
  function clearSpeechTimers() {
    clearInterval(state.speech.timer);
    clearTimeout(state.speech.timeout);
    state.speech.timer = null;
    state.speech.timeout = null;
  }

  function stopSpeechStream(stream = state.speech.stream) {
    (stream?.getTracks?.() || []).forEach((track) => track.stop());
  }

  function settleRealtimeTranscript(value = null) {
    const resolve = state.speech.resolveFinal;
    state.speech.resolveFinal = null;
    if (resolve) resolve(value);
  }

  function releaseRealtimeConnection() {
    const controller = state.speech.realtimeController;
    const channel = state.speech.dataChannel;
    const peer = state.speech.peerConnection;
    state.speech.realtimeController = null;
    state.speech.dataChannel = null;
    state.speech.peerConnection = null;
    state.speech.realtimeConnected = false;
    controller?.abort();
    try { channel?.close(); } catch { /* already closed */ }
    try { peer?.close(); } catch { /* already closed */ }
  }

  function markRealtimeSpeechFailed(message = "Live preview was interrupted.") {
    if (["idle", "cancelling"].includes(state.speech.phase) || state.speech.finalTranscript) return;
    state.speech.realtimeFailed = true;
    state.speech.realtimeError = String(message || "Live preview was interrupted.");
    settleRealtimeTranscript(null);
    releaseRealtimeConnection();
    syncSpeechControls();
  }

  function resetSpeechState() {
    clearSpeechTimers();
    state.speech.controller?.abort();
    settleRealtimeTranscript(null);
    releaseRealtimeConnection();
    stopSpeechStream();
    state.speech.phase = "idle";
    state.speech.recorder = null;
    state.speech.stream = null;
    state.speech.chunks = [];
    state.speech.target = null;
    state.speech.startedAt = 0;
    state.speech.cancelled = false;
    state.speech.controller = null;
    state.speech.realtimeAttempted = false;
    state.speech.realtimeFailed = false;
    state.speech.realtimeError = "";
    transcripts.clear();
    state.speech.liveTranscript = "";
    state.speech.finalTranscript = "";
    state.speech.finalPromise = null;
    syncSpeechControls();
    options.onReset?.();
  }

  function realtimeEventMessage(event) {
    return String(
      event?.error?.message
      || event?.message
      || "Live preview was interrupted.",
    ).slice(0, 300);
  }

  function handleRealtimeSpeechEvent(event) {
    if (!event || ["idle", "cancelling"].includes(state.speech.phase)) return;
    if (event.type === "conversation.item.input_audio_transcription.delta") {
      const delta = String(event.delta || "");
      if (delta) {
        const key = event.item_id || "current";
        transcripts.set(key, `${transcripts.get(key) || ""}${delta}`.slice(0, 40_000));
        state.speech.liveTranscript = [...transcripts.values()].join(" ").slice(0, 40_000);
        syncSpeechPreviews();
      }
      return;
    }
    if (event.type === "conversation.item.input_audio_transcription.completed") {
      const transcript = String(event.transcript || "").trim();
      if (transcript) {
        state.speech.finalTranscript = transcript.slice(0, 40_000);
        state.speech.liveTranscript = state.speech.finalTranscript;
        settleRealtimeTranscript(state.speech.finalTranscript);
        syncSpeechPreviews();
      } else {
        markRealtimeSpeechFailed("No speech was detected in the live recording.");
      }
      return;
    }
    if (
      event.type === "conversation.item.input_audio_transcription.failed"
      || event.type === "error"
    ) {
      markRealtimeSpeechFailed(realtimeEventMessage(event));
    }
  }

  function waitForRealtimeChannel(channel, timeoutMs = 20_000) {
    if (channel.readyState === "open") return Promise.resolve();
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => finish(new Error("Live transcription took too long to connect.")), timeoutMs);
      const finish = (error) => {
        clearTimeout(timer);
        channel.removeEventListener("open", opened);
        channel.removeEventListener("error", failed);
        channel.removeEventListener("close", failed);
        if (error) reject(error);
        else resolve();
      };
      const opened = () => finish();
      const failed = () => finish(new Error("Live transcription could not open its event channel."));
      channel.addEventListener("open", opened, { once: true });
      channel.addEventListener("error", failed, { once: true });
      channel.addEventListener("close", failed, { once: true });
    });
  }

  async function connectRealtimeSpeech(stream, target) {
    state.speech.realtimeAttempted = true;
    state.speech.realtimeFailed = false;
    state.speech.realtimeError = "";
    transcripts.clear();
    state.speech.liveTranscript = "";
    state.speech.finalTranscript = "";
    state.speech.finalPromise = new Promise((resolve) => {
      state.speech.resolveFinal = resolve;
    });
    const track = stream.getAudioTracks?.()[0];
    if (!track) throw new Error("No microphone audio track was available.");
    track.enabled = false;
    const peer = new RTCPeerConnection();
    const channel = peer.createDataChannel("oai-events");
    state.speech.peerConnection = peer;
    state.speech.dataChannel = channel;
    peer.addTrack(track, stream);
    channel.addEventListener("message", (message) => {
      if (state.speech.dataChannel !== channel) return;
      try {
        handleRealtimeSpeechEvent(JSON.parse(message.data));
      } catch { /* ignore malformed provider events */ }
    });
    channel.addEventListener("close", () => {
      if (
        state.speech.dataChannel === channel
        && !state.speech.finalTranscript
        && ["recording", "transcribing"].includes(state.speech.phase)
      ) {
        markRealtimeSpeechFailed("The live transcript channel closed early.");
      }
    });
    peer.addEventListener("connectionstatechange", () => {
      if (
        state.speech.peerConnection === peer
        && peer.connectionState === "failed"
      ) {
        markRealtimeSpeechFailed("The live audio connection was interrupted.");
      }
    });
    const offer = await peer.createOffer();
    await peer.setLocalDescription(offer);
    const form = new FormData();
    form.append("sdp", offer.sdp || "");
    form.append("surface", target.kind);
    if (target.sessionId) form.append("session_id", target.sessionId);
    const controller = new AbortController();
    state.speech.realtimeController = controller;
    const connectionTimeout = setTimeout(() => controller.abort(), 20_000);
    let response;
    let responseBody;
    try {
      response = await authenticatedFetch("/api/calliope/realtime-transcription", {
        method: "POST",
        body: form,
        signal: controller.signal,
        headers: { Accept: "application/sdp, application/json" },
      });
      responseBody = await response.text();
    } finally {
      clearTimeout(connectionTimeout);
      if (state.speech.realtimeController === controller) {
        state.speech.realtimeController = null;
      }
    }
    if (!response.ok) {
      let message = `Live transcription could not start (${response.status}).`;
      try { message = JSON.parse(responseBody)?.error?.message || message; } catch { /* use status */ }
      throw new Error(message);
    }
    if (!responseBody.startsWith("v=0")) {
      throw new Error("Live transcription returned an invalid connection answer.");
    }
    await peer.setRemoteDescription({ type: "answer", sdp: responseBody });
    await waitForRealtimeChannel(channel);
    if (state.speech.cancelled) {
      const error = new Error("Live transcription was cancelled.");
      error.name = "AbortError";
      throw error;
    }
    state.speech.realtimeConnected = true;
    return true;
  }

  async function finishRealtimeTranscript() {
    const channel = state.speech.dataChannel;
    const pending = state.speech.finalPromise;
    if (!state.speech.realtimeConnected || channel?.readyState !== "open" || !pending) {
      return null;
    }
    await new Promise((resolve) => setTimeout(resolve, 140));
    try {
      channel.send(JSON.stringify({ type: "input_audio_buffer.commit" }));
      stopSpeechStream();
      state.speech.stream = null;
    } catch {
      markRealtimeSpeechFailed("The live transcript could not be finalized.");
      return null;
    }
    let finalTimer;
    const transcript = await Promise.race([
      pending,
      new Promise((resolve) => { finalTimer = setTimeout(() => resolve(null), 12_000); }),
    ]).finally(() => clearTimeout(finalTimer));
    if (!transcript) {
      markRealtimeSpeechFailed("The live transcript did not finalize in time.");
      return null;
    }
    return String(transcript).trim();
  }

  function acceptSpeechTranscript(target, transcript) {
    const text = String(transcript || "").trim();
    if (!text) throw new Error("No speech was detected. Check that your microphone is on.");
    if (!state.speech.cancelled) options.onTranscript(target, text);
  }

  async function transcribeSpeechRecording(blob, target, startedAt) {
    const maxBytes = Number(state.config?.speech_to_text?.max_audio_bytes || 0);
    if (!blob.size) throw new Error("That recording was empty. Try again and speak after the microphone appears.");
    if (maxBytes && blob.size > maxBytes) throw new Error("That recording is larger than this installation allows.");
    const extension = blob.type.includes("mp4") ? "m4a" : "webm";
    const body = new FormData();
    body.append("file", blob, `calliope-recording.${extension}`);
    body.append("surface", target.kind);
    body.append("duration_seconds", String(Math.max(0, (Date.now() - startedAt) / 1000)));
    state.speech.controller = new AbortController();
    const response = await authenticatedFetch("/api/calliope/transcriptions", {
      method: "POST",
      body,
      signal: state.speech.controller.signal,
      headers: { Accept: "application/json" },
    });
    let data = {};
    try { data = await response.json(); } catch { /* handled by the status below */ }
    if (!response.ok) {
      throw new Error(data?.error?.message || `Speech transcription failed (${response.status})`);
    }
    acceptSpeechTranscript(target, data.text);
  }

  async function finishSpeechRecording() {
    const recorder = state.speech.recorder;
    const target = state.speech.target;
    const chunks = [...state.speech.chunks];
    const startedAt = state.speech.startedAt;
    const cancelled = state.speech.cancelled;
    const mediaType = recorder?.mimeType || chunks.find((chunk) => chunk.type)?.type || "audio/webm";
    clearSpeechTimers();
    state.speech.recorder = null;
    state.speech.chunks = [];
    if (cancelled || !target) {
      stopSpeechStream();
      resetSpeechState();
      return;
    }
    state.speech.phase = "transcribing";
    syncSpeechControls();
    try {
      let transcript = null;
      if (state.speech.realtimeConnected) {
        transcript = await finishRealtimeTranscript();
      }
      stopSpeechStream();
      state.speech.stream = null;
      if (state.speech.cancelled) return;
      if (transcript) {
        acceptSpeechTranscript(target, transcript);
      } else {
        await transcribeSpeechRecording(
          new Blob(chunks, { type: mediaType }), target, startedAt,
        );
      }
    } catch (error) {
      if (error?.name !== "AbortError") toast(error.message || "Speech transcription failed", true);
    } finally {
      resetSpeechState();
    }
  }

  function stopSpeechRecording({ cancel = false } = {}) {
    if (state.speech.phase === "transcribing") {
      if (cancel) {
        state.speech.cancelled = true;
        state.speech.phase = "cancelling";
        state.speech.controller?.abort();
        settleRealtimeTranscript(null);
        releaseRealtimeConnection();
        stopSpeechStream();
        syncSpeechControls();
      }
      return;
    }
    if (state.speech.phase === "requesting") {
      if (cancel) {
        state.speech.cancelled = true;
        state.speech.phase = "cancelling";
        state.speech.realtimeController?.abort();
        settleRealtimeTranscript(null);
        releaseRealtimeConnection();
        stopSpeechStream();
        syncSpeechControls();
      }
      return;
    }
    const recorder = state.speech.recorder;
    if (!recorder || !["recording", "paused"].includes(recorder.state)) return;
    state.speech.cancelled = cancel;
    state.speech.phase = cancel ? "cancelling" : "transcribing";
    clearSpeechTimers();
    (state.speech.stream?.getAudioTracks?.() || []).forEach((track) => {
      track.enabled = false;
    });
    recorder.stop();
    if (cancel) {
      settleRealtimeTranscript(null);
      releaseRealtimeConnection();
      stopSpeechStream();
    }
    syncSpeechControls();
  }

  function cancelSpeechRecording() {
    if (state.speech.phase !== "idle") stopSpeechRecording({ cancel: true });
  }

  async function startSpeechRecording(target) {
    if (!target || !speechSupported() || state.speech.phase !== "idle") return;
    state.speech.target = target;
    state.speech.phase = "requesting";
    state.speech.cancelled = false;
    syncSpeechControls();
    let stream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
      });
      state.speech.stream = stream;
      syncSpeechControls();
      if (state.speech.cancelled) {
        stopSpeechStream(stream);
        resetSpeechState();
        return;
      }
      let recorder = null;
      for (const mimeType of SPEECH_MIME_TYPES) {
        if (MediaRecorder.isTypeSupported && !MediaRecorder.isTypeSupported(mimeType)) continue;
        try {
          recorder = new MediaRecorder(stream, { mimeType, audioBitsPerSecond: 64_000 });
          break;
        } catch { /* try the next browser format */ }
      }
      if (!recorder) recorder = new MediaRecorder(stream, { audioBitsPerSecond: 64_000 });
      state.speech.recorder = recorder;
      state.speech.chunks = [];
      state.speech.startedAt = Date.now();
      recorder.addEventListener("dataavailable", (event) => {
        if (event.data?.size) state.speech.chunks.push(event.data);
      });
      recorder.addEventListener("stop", () => finishSpeechRecording());
      recorder.addEventListener("error", () => {
        state.speech.cancelled = true;
        toast("The browser stopped the microphone recording.", true);
        if (recorder.state !== "inactive") recorder.stop();
        else resetSpeechState();
      });
      if (realtimeSpeechSupported()) {
        try {
          await connectRealtimeSpeech(stream, target);
        } catch (error) {
          if (state.speech.cancelled) {
            resetSpeechState();
            return;
          }
          state.speech.realtimeAttempted = true;
          state.speech.realtimeFailed = true;
          state.speech.realtimeError = error?.message || "Live preview is unavailable.";
          settleRealtimeTranscript(null);
          releaseRealtimeConnection();
        }
      }
      if (state.speech.cancelled) {
        resetSpeechState();
        return;
      }
      (stream.getAudioTracks?.() || []).forEach((track) => {
        track.enabled = true;
      });
      recorder.start(500);
      state.speech.startedAt = Date.now();
      state.speech.phase = "recording";
      state.speech.timer = setInterval(syncSpeechControls, 500);
      const maxSeconds = Number(state.config?.speech_to_text?.max_audio_seconds || 600);
      state.speech.timeout = setTimeout(() => {
        toast("Recording limit reached · finishing your dictation.");
        stopSpeechRecording();
      }, maxSeconds * 1000);
      syncSpeechControls();
    } catch (error) {
      const cancelled = state.speech.cancelled;
      stopSpeechStream(stream);
      resetSpeechState();
      if (cancelled) return;
      const message = error?.name === "NotAllowedError"
        ? "Microphone access was not granted."
        : error?.name === "NotFoundError"
          ? "No microphone was found on this device."
          : "This browser could not start a supported microphone recording.";
      toast(message, true);
    }
  }


    return {state: speech, supported: speechSupported, start: startSpeechRecording,
            stop: stopSpeechRecording, cancel: cancelSpeechRecording};
  }
  window.CalliopeDictation = {create, insertion: speechInsertion};
})();
