/**
 * SunoSaver Content Script
 * Injected on suno.com:
 * 1. Decrypts native Suno stream via Web Crypto API
 * 2. Decodes PCM via AudioContext
 * 3. Encodes to true, universal MP3 (via lamejs) with exact duration & timeline
 */

const UUID_REGEX = /[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/i;

function extractUUID(str) {
  if (!str) return null;
  const match = str.match(UUID_REGEX);
  return match ? match[0] : null;
}

function b64ToUint8Array(b64) {
  const bin = atob(b64);
  const arr = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) arr[i] = bin.charCodeAt(i);
  return arr;
}

/**
 * Safely send a message to runtime/background without throwing
 * "Receiving end does not exist" or "Cannot read properties of undefined"
 */
function safeSendMessage(message, callback) {
  try {
    if (typeof chrome !== "undefined" && chrome.runtime && chrome.runtime.id) {
      chrome.runtime.sendMessage(message, (response) => {
        // Must inspect lastError to prevent Chrome from logging Unchecked runtime.lastError
        const err = chrome.runtime.lastError;
        if (callback && !err) {
          callback(response);
        }
      });
    }
  } catch (e) {
    // Context invalidated due to extension update/reload; ignore gracefully
  }
}

/**
 * Converts an AudioBuffer into standard MP3 using lamejs
 */
function encodeAudioBufferToMp3(audioBuffer, bitrate = 256) {
  const channels = audioBuffer.numberOfChannels;
  const sampleRate = audioBuffer.sampleRate;
  
  if (typeof lamejs === "undefined") {
    throw new Error("lamejs encoder not loaded");
  }

  const mp3encoder = new lamejs.Mp3Encoder(channels, sampleRate, bitrate);
  const mp3Data = [];

  const left = audioBuffer.getChannelData(0);
  const right = channels > 1 ? audioBuffer.getChannelData(1) : left;
  const sampleBlockSize = 1152;

  // Convert Float32 [-1.0, 1.0] to Int16 [-32768, 32767]
  const leftInt = new Int16Array(left.length);
  const rightInt = new Int16Array(right.length);
  for (let i = 0; i < left.length; i++) {
    leftInt[i] = Math.max(-32768, Math.min(32767, left[i] * 32768));
    rightInt[i] = Math.max(-32768, Math.min(32767, right[i] * 32768));
  }

  for (let i = 0; i < left.length; i += sampleBlockSize) {
    const leftChunk = leftInt.subarray(i, i + sampleBlockSize);
    const rightChunk = rightInt.subarray(i, i + sampleBlockSize);
    let mp3buf;
    if (channels === 2) {
      mp3buf = mp3encoder.encodeBuffer(leftChunk, rightChunk);
    } else {
      mp3buf = mp3encoder.encodeBuffer(leftChunk);
    }
    if (mp3buf.length > 0) mp3Data.push(mp3buf);
  }

  const endBuf = mp3encoder.flush();
  if (endBuf.length > 0) mp3Data.push(endBuf);

  return new Blob(mp3Data, { type: "audio/mp3" });
}

/**
 * Encodes an AudioBuffer into standard 16-bit PCM WAV (lossless)
 */
function encodeAudioBufferToWav(buffer) {
  const numOfChan = buffer.numberOfChannels;
  const length = buffer.length * numOfChan * 2 + 44;
  const out = new DataView(new ArrayBuffer(length));
  const channels = [];
  let sample;
  let offset = 0;
  let pos = 0;

  function setUint16(data) {
    out.setUint16(pos, data, true);
    pos += 2;
  }
  function setUint32(data) {
    out.setUint32(pos, data, true);
    pos += 4;
  }

  setUint32(0x46464952); // "RIFF"
  setUint32(length - 8);
  setUint32(0x45564157); // "WAVE"

  setUint32(0x20746d66); // "fmt "
  setUint32(16);
  setUint16(1); // PCM
  setUint16(numOfChan);
  setUint32(buffer.sampleRate);
  setUint32(buffer.sampleRate * 2 * numOfChan);
  setUint16(numOfChan * 2);
  setUint16(16); // 16-bit

  setUint32(0x61746164); // "data"
  setUint32(length - pos - 4);

  for (let i = 0; i < buffer.numberOfChannels; i++) {
    channels.push(buffer.getChannelData(i));
  }

  while (offset < buffer.length) {
    for (let i = 0; i < numOfChan; i++) {
      sample = Math.max(-1, Math.min(1, channels[i][offset]));
      sample = (0.5 + sample < 0 ? sample * 32768 : sample * 32767) | 0;
      out.setInt16(pos, sample, true);
      pos += 2;
    }
    offset++;
  }

  return new Blob([out.buffer], { type: "audio/wav" });
}

/**
 * Main Download Function:
 * 1. Metadata -> 2. Rights -> 3. Decrypt AES-CTR -> 4. Decode PCM -> 5. MP3 Encode
 */
async function downloadSunoTrack(uuid, fallbackTitle, fallbackAuthor, format = "mp3") {
  let title = fallbackTitle || "Suno Track";
  let author = fallbackAuthor || "Suno AI";

  // 1. Fetch metadata
  try {
    const metaResp = await fetch(`https://studio-api.prod.suno.com/api/clip/${uuid}`);
    if (metaResp.ok) {
      const meta = await metaResp.json();
      if (meta.title) title = meta.title;
      if (meta.display_name) author = meta.display_name;
      else if (meta.handle) author = meta.handle;
    }
  } catch (e) {
    console.warn("[SunoSaver] Meta fetch error:", e);
  }

  const cleanTitle = (title || "Suno Track").replace(/[\\/:*?"<>|]/g, "_").trim();
  const cleanAuthor = (author || "Suno AI").replace(/[\\/:*?"<>|]/g, "_").trim();

  // 2. Request rights
  const rightsResp = await fetch("https://studio-api.prod.suno.com/api/mango/rights", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ content_params: { content_id: uuid, content_type: "clip" } })
  });

  if (!rightsResp.ok) {
    throw new Error(`Rights request failed: HTTP ${rightsResp.status}`);
  }

  const rights = await rightsResp.json();
  const glt = rights.glt;
  const wrappedKeyBytes = b64ToUint8Array(rights.key);
  const wrappedIvBytes = b64ToUint8Array(rights.iv);

  // 3. User key via SHA-256
  const userKeyRaw = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(glt));
  const userKey = await crypto.subtle.importKey("raw", userKeyRaw, { name: "AES-GCM" }, false, ["decrypt"]);
  const aad = new TextEncoder().encode(uuid);

  async function decryptGcm(wrapped) {
    const iv = wrapped.slice(0, 12);
    const ctAndTag = wrapped.slice(12);
    return await crypto.subtle.decrypt(
      { name: "AES-GCM", iv: iv, additionalData: aad, tagLength: 128 },
      userKey,
      ctAndTag
    );
  }

  const contentKeyRaw = await decryptGcm(wrappedKeyBytes);
  const contentIvRaw = await decryptGcm(wrappedIvBytes);

  // 4. Download encrypted audio stream
  const streamResp = await fetch(`https://d2lwuy8qc234o3.cloudfront.net/1/clip/${uuid}.m4a`);
  if (!streamResp.ok) {
    throw new Error(`Stream download failed: HTTP ${streamResp.status}`);
  }
  const encBuffer = await streamResp.arrayBuffer();

  // 5. Decrypt stream using AES-CTR
  const ctrKey = await crypto.subtle.importKey("raw", contentKeyRaw, { name: "AES-CTR" }, false, ["decrypt"]);
  const decAudio = await crypto.subtle.decrypt(
    { name: "AES-CTR", counter: new Uint8Array(contentIvRaw), length: 64 },
    ctrKey,
    encBuffer
  );

  // 6. Decode into PCM with Web Audio API (Gets exact duration and full uncompressed audio)
  const AudioContextClass = window.AudioContext || window.webkitAudioContext;
  const audioCtx = new AudioContextClass();
  const audioBuffer = await audioCtx.decodeAudioData(decAudio);

  let finalBlob;
  let filename;

  if (format === "wav") {
    finalBlob = encodeAudioBufferToWav(audioBuffer);
    filename = `${cleanTitle} - ${cleanAuthor}.wav`;
  } else {
    try {
      finalBlob = encodeAudioBufferToMp3(audioBuffer, 256);
      filename = `${cleanTitle} - ${cleanAuthor}.mp3`;
    } catch (encErr) {
      console.warn("[SunoSaver] MP3 encoder fallback to WAV:", encErr);
      finalBlob = encodeAudioBufferToWav(audioBuffer);
      filename = `${cleanTitle} - ${cleanAuthor}.wav`;
    }
  }

  // 7. Trigger browser download with real MP3 / WAV
  const blobUrl = URL.createObjectURL(finalBlob);
  const a = document.createElement("a");
  a.href = blobUrl;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);

  setTimeout(() => URL.revokeObjectURL(blobUrl), 30000);
  return { success: true, filename: filename };
}

// Track active audio
function monitorAudioPlayer() {
  const audios = document.querySelectorAll("audio");
  audios.forEach((audio) => {
    if (audio.dataset.sunosaverHooked) return;
    audio.dataset.sunosaverHooked = "true";

    const updatePlayingTrack = () => {
      const src = audio.src || audio.currentSrc;
      const uuid = extractUUID(src) || extractUUID(window.location.pathname);
      
      let title = document.title.replace(" | Suno", "").trim();
      let author = "Suno Creator";

      const titleEl = document.querySelector('h1, [data-testid="song-title"], .song-title');
      if (titleEl && titleEl.textContent) title = titleEl.textContent.trim();

      const authorEl = document.querySelector('[data-testid="song-artist"], a[href^="/@"]');
      if (authorEl && authorEl.textContent) author = authorEl.textContent.trim().replace("@", "");

      safeSendMessage({
        action: "track_detected",
        data: {
          uuid: uuid,
          title: title,
          author: author,
          pageUrl: window.location.href
        }
      });
    };

    audio.addEventListener("play", updatePlayingTrack);
    audio.addEventListener("loadedmetadata", updatePlayingTrack);
  });
}

// Injects floating download button on song pages
function injectFloatingDownloadButton() {
  const uuid = extractUUID(window.location.pathname);
  if (!uuid) {
    const existing = document.getElementById("sunosaver-floating-widget");
    if (existing) existing.remove();
    return;
  }

  if (document.getElementById("sunosaver-floating-widget")) return;

  const widget = document.createElement("div");
  widget.id = "sunosaver-floating-widget";
  widget.className = "sunosaver-floating-widget";

  let titleText = document.title.replace(" | Suno", "").trim() || "Suno Track";

  widget.innerHTML = `
    <div style="display: flex; align-items: center; gap: 8px;">
      <span style="font-size: 16px;">⚡️</span>
      <div id="sunosaver-display-title" class="sunosaver-widget-title">${escapeHTML(titleText)}</div>
    </div>
    <div class="sunosaver-widget-actions">
      <button id="sunosaver-download-btn" class="sunosaver-btn">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5">
          <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path>
          <polyline points="7 10 12 15 17 10"></polyline>
          <line x1="12" y1="15" x2="12" y2="3"></line>
        </svg>
        MP3
      </button>
      <button id="sunosaver-tg-btn" class="sunosaver-btn" style="border-color: #8b5cf6; background: linear-gradient(135deg, #2e1065 0%, #0f172a 100%);">
        🎙 Вокал / WAV
      </button>
    </div>
  `;

  document.body.appendChild(widget);

  // Hook Download
  document.getElementById("sunosaver-download-btn").addEventListener("click", async (e) => {
    const btn = e.currentTarget;
    btn.classList.add("loading");
    btn.textContent = "⏳ Конвертация MP3...";

    try {
      await downloadSunoTrack(uuid, titleText, "Suno AI", "mp3");
      btn.classList.remove("loading");
      btn.classList.add("success");
      btn.textContent = "✅ Скачано!";
      setTimeout(() => {
        btn.classList.remove("success");
        btn.innerHTML = `
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5">
            <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path>
            <polyline points="7 10 12 15 17 10"></polyline>
            <line x1="12" y1="15" x2="12" y2="3"></line>
          </svg>
          MP3
        `;
      }, 3000);
    } catch (err) {
      console.error("[SunoSaver] Download error:", err);
      btn.classList.remove("loading");
      btn.textContent = "🤖 Открыть в боте...";
      setTimeout(() => {
        window.open(`https://t.me/sunosaver_bot?start=dl_${uuid}`, "_blank");
      }, 500);
    }
  });

  // Hook Telegram Bot
  document.getElementById("sunosaver-tg-btn").addEventListener("click", () => {
    window.open(`https://t.me/sunosaver_bot?start=dl_${uuid}`, "_blank");
  });
}

function escapeHTML(str) {
  return str.replace(/[&<>'"]/g, 
    tag => ({
      '&': '&amp;',
      '<': '&lt;',
      '>': '&gt;',
      "'": '&#39;',
      '"': '&quot;'
    }[tag] || tag)
  );
}

// Listen for messages from popup
if (typeof chrome !== "undefined" && chrome.runtime && chrome.runtime.onMessage) {
  chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
    if (request.action === "download_current_page_track") {
      const uuid = extractUUID(window.location.pathname) || request.uuid;
      if (uuid) {
        downloadSunoTrack(uuid, request.title, request.author, request.format || "mp3")
          .then((res) => sendResponse({ success: true, ...res }))
          .catch((err) => sendResponse({ success: false, error: err.message }));
        return true; // Keep async channel open
      }
    }
  });
}

// Observe dynamic DOM changes (SPA navigation)
const observer = new MutationObserver(() => {
  monitorAudioPlayer();
  injectFloatingDownloadButton();
});

observer.observe(document.body, { childList: true, subtree: true });

monitorAudioPlayer();
injectFloatingDownloadButton();
