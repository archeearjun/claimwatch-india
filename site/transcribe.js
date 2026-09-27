const MODEL_ID = "onnx-community/whisper-tiny";
const CHUNK_MS = 18000;

const $ = selector => document.querySelector(selector);

let captureStream = null;
let recorder = null;
let segmentTimer = null;
let running = false;
let processingChain = Promise.resolve();
let transcriberPromise = null;
let transcriptText = "";
let segmentCount = 0;
let audioContext = null;

function setStatus(message, kind = "") {
  const el = $("#live-status");
  if (!el) return;
  el.textContent = message;
  el.dataset.kind = kind;
}

function setProgress(value) {
  const bar = $("#model-progress-bar");
  if (!bar) return;
  const clamped = Math.max(0, Math.min(100, Number(value) || 0));
  bar.style.width = `${clamped}%`;
}

function formatProgress(progress) {
  if (!progress || typeof progress !== "object") return null;
  if (progress.status === "progress" && Number.isFinite(progress.progress)) {
    return Math.round(progress.progress);
  }
  if (progress.status === "ready") return 100;
  return null;
}

async function getTranscriber() {
  if (transcriberPromise) return transcriberPromise;

  transcriberPromise = (async () => {
    setStatus("Loading the Whisper model… first run may take a few minutes.", "loading");

    const { pipeline, env } = await import(
      "https://cdn.jsdelivr.net/npm/@huggingface/transformers@3.8.1/+esm"
    );

    env.allowLocalModels = false;
    env.useBrowserCache = true;

    const options = {
      progress_callback: progress => {
        const pct = formatProgress(progress);
        if (pct !== null) {
          setProgress(pct);
          setStatus(`Loading Whisper model… ${pct}%`, "loading");
        }
      }
    };

    if ("gpu" in navigator) {
      options.device = "webgpu";
    } else {
      options.dtype = "q8";
    }

    try {
      const pipe = await pipeline(
        "automatic-speech-recognition",
        MODEL_ID,
        options
      );
      setProgress(100);
      setStatus(
        `Whisper ready on ${options.device === "webgpu" ? "WebGPU" : "CPU/WASM"}.`,
        "ready"
      );
      return pipe;
    } catch (firstError) {
      if (options.device !== "webgpu") throw firstError;

      setStatus("WebGPU model load failed; retrying with CPU/WASM…", "loading");
      const pipe = await pipeline(
        "automatic-speech-recognition",
        MODEL_ID,
        {
          dtype: "q8",
          progress_callback: options.progress_callback
        }
      );
      setProgress(100);
      setStatus("Whisper ready on CPU/WASM.", "ready");
      return pipe;
    }
  })().catch(error => {
    transcriberPromise = null;
    setProgress(0);
    throw error;
  });

  return transcriberPromise;
}

function chooseMimeType() {
  const candidates = [
    "audio/webm;codecs=opus",
    "audio/webm",
    "audio/ogg;codecs=opus"
  ];
  return candidates.find(type => MediaRecorder.isTypeSupported(type)) || "";
}

function stopTracks() {
  if (!captureStream) return;
  captureStream.getTracks().forEach(track => track.stop());
  captureStream = null;
}

async function decodeAudio(blob) {
  const buffer = await blob.arrayBuffer();

  if (!audioContext || audioContext.state === "closed") {
    const AudioCtx = window.AudioContext || window.webkitAudioContext;
    audioContext = new AudioCtx({ sampleRate: 16000 });
  }

  if (audioContext.state === "suspended") {
    await audioContext.resume();
  }

  const decoded = await audioContext.decodeAudioData(buffer.slice(0));
  const channels = decoded.numberOfChannels;
  const length = decoded.length;
  const mono = new Float32Array(length);

  for (let channel = 0; channel < channels; channel += 1) {
    const data = decoded.getChannelData(channel);
    for (let i = 0; i < length; i += 1) {
      mono[i] += data[i] / channels;
    }
  }

  if (decoded.sampleRate === 16000) return mono;
  return resampleLinear(mono, decoded.sampleRate, 16000);
}

function resampleLinear(input, sourceRate, targetRate) {
  if (sourceRate === targetRate) return input;

  const ratio = sourceRate / targetRate;
  const outputLength = Math.max(1, Math.floor(input.length / ratio));
  const output = new Float32Array(outputLength);

  for (let i = 0; i < outputLength; i += 1) {
    const sourceIndex = i * ratio;
    const left = Math.floor(sourceIndex);
    const right = Math.min(left + 1, input.length - 1);
    const mix = sourceIndex - left;
    output[i] = input[left] * (1 - mix) + input[right] * mix;
  }

  return output;
}

function appendTranscript(text) {
  const clean = String(text || "").replace(/\s+/g, " ").trim();
  if (!clean) return;

  transcriptText = [transcriptText, clean].filter(Boolean).join(" ").trim();
  segmentCount += 1;

  $("#live-transcript").textContent = transcriptText;
  $("#live-chunk-count").textContent =
    `${segmentCount} segment${segmentCount === 1 ? "" : "s"}`;

  const manual = $("#transcript");
  if (manual) manual.value = transcriptText;

  renderLiveClaims();
}

function renderLiveClaims() {
  const root = $("#live-claim-list");
  const count = $("#live-claim-count");
  if (!root || !count) return;

  const extractor = window.ClaimWatch?.extractCandidates;
  if (!extractor) {
    count.textContent = "waiting for extractor";
    return;
  }

  const candidates = extractor(transcriptText).slice(0, 20);
  count.textContent =
    `${candidates.length} candidate${candidates.length === 1 ? "" : "s"}`;

  if (!candidates.length) {
    root.innerHTML =
      '<div class="quiet-note">No high-confidence checkable claim has been detected yet.</div>';
    return;
  }

  root.innerHTML = candidates.map((item, index) => `
    <article class="candidate-card">
      <span class="candidate-number">${String(index + 1).padStart(2, "0")}</span>
      <div>
        <p>${escapeHtml(item.sentence)}</p>
        <div class="candidate-reasons">
          ${item.reasons.map(reason => `<span>${escapeHtml(reason)}</span>`).join("")}
        </div>
      </div>
    </article>
  `).join("");
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>'"]/g, char => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    "'": "&#39;",
    '"': "&quot;"
  }[char]));
}

async function transcribeBlob(blob) {
  if (!blob || blob.size < 1024) return;

  try {
    setStatus("Decoding captured audio…", "working");
    const audio = await decodeAudio(blob);
    const transcriber = await getTranscriber();

    const language = $("#live-language")?.value || "auto";
    setStatus("Transcribing locally…", "working");

    const options = {
      task: "transcribe",
      return_timestamps: false,
      chunk_length_s: 20,
      stride_length_s: 3
    };

    if (language !== "auto") {
      options.language = language;
    }

    const output = await transcriber(audio, options);
    appendTranscript(output?.text || "");
    setStatus(
      running
        ? "Listening. The next audio segment is being captured…"
        : "Transcription complete.",
      "ready"
    );
  } catch (error) {
    console.error("ClaimWatch live transcription error", error);
    setStatus(
      `Could not transcribe this segment: ${error?.message || error}. Capture continues.`,
      "error"
    );
  }
}

function beginSegment() {
  if (!running || !captureStream) return;

  const chunks = [];
  const mimeType = chooseMimeType();
  recorder = mimeType
    ? new MediaRecorder(captureStream, { mimeType })
    : new MediaRecorder(captureStream);

  recorder.addEventListener("dataavailable", event => {
    if (event.data?.size) chunks.push(event.data);
  });

  recorder.addEventListener("stop", () => {
    clearTimeout(segmentTimer);
    const blob = new Blob(chunks, {
      type: recorder?.mimeType || mimeType || "audio/webm"
    });

    processingChain = processingChain.then(() => transcribeBlob(blob));

    if (running && captureStream?.active) {
      window.setTimeout(beginSegment, 120);
    }
  }, { once: true });

  recorder.start();
  segmentTimer = window.setTimeout(() => {
    if (recorder?.state === "recording") recorder.stop();
  }, CHUNK_MS);
}

async function startCapture() {
  if (running) return;

  if (!navigator.mediaDevices?.getDisplayMedia) {
    setStatus("This browser does not support tab/system audio capture.", "error");
    return;
  }

  try {
    setStatus("Choose the browser tab containing the speech and enable Share tab audio.", "working");

    captureStream = await navigator.mediaDevices.getDisplayMedia({
      video: true,
      audio: true
    });

    if (!captureStream.getAudioTracks().length) {
      stopTracks();
      setStatus(
        "No shared audio track was detected. Start again and enable Share tab audio.",
        "error"
      );
      return;
    }

    running = true;
    $("#live-start").disabled = true;
    $("#live-stop").disabled = false;

    captureStream.getTracks().forEach(track => {
      track.addEventListener("ended", () => {
        if (running) stopCapture();
      }, { once: true });
    });

    getTranscriber().catch(error => {
      console.error(error);
      setStatus(
        `Whisper model failed to load: ${error?.message || error}`,
        "error"
      );
    });

    setStatus("Listening. Capturing the first 18-second segment…", "working");
    beginSegment();
  } catch (error) {
    stopTracks();
    running = false;
    $("#live-start").disabled = false;
    $("#live-stop").disabled = true;

    if (error?.name === "NotAllowedError") {
      setStatus("Tab sharing was cancelled or denied.", "error");
    } else {
      setStatus(
        `Could not start audio capture: ${error?.message || error}`,
        "error"
      );
    }
  }
}

function stopCapture() {
  if (!running && !captureStream) return;

  running = false;
  clearTimeout(segmentTimer);

  if (recorder?.state === "recording") {
    recorder.stop();
  }

  stopTracks();
  $("#live-start").disabled = false;
  $("#live-stop").disabled = true;
  setStatus("Capture stopped. Finishing any queued transcription…", "working");

  processingChain.finally(() => {
    if (!running) setStatus("Stopped. Transcript retained below.", "ready");
  });
}

function clearTranscript() {
  transcriptText = "";
  segmentCount = 0;
  $("#live-transcript").textContent = "Transcript will appear here…";
  $("#live-chunk-count").textContent = "0 segments";
  $("#live-claim-count").textContent = "0 candidates";
  $("#live-claim-list").innerHTML = "";
  const manual = $("#transcript");
  if (manual) manual.value = "";
}

$("#live-start")?.addEventListener("click", startCapture);
$("#live-stop")?.addEventListener("click", stopCapture);
$("#live-clear")?.addEventListener("click", clearTranscript);

window.addEventListener("beforeunload", () => {
  running = false;
  clearTimeout(segmentTimer);
  stopTracks();
});
