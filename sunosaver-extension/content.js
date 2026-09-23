/**
 * SunoSaver Content Script
 * Injected on suno.com: detects current song, decrypts audio via Web Crypto API,
 * and handles direct, instant high-quality downloads.
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
 * Native Suno AES-CTR stream decryption and direct download.
 */
async function downloadSunoTrack(uuid, fallbackTitle, fallbackAuthor) {
  let title = fallbackTitle || "Suno Track";
  let author = fallbackAuthor || "Suno AI";

  // 1. Fetch real track metadata from Suno studio API
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
  const filename = `${cleanTitle} - ${cleanAuthor}.m4a`;

  // 2. Request official mango rights for clip
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

  // 3. Compute user key via SHA-256
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
    throw new Error(`Audio stream download failed: HTTP ${streamResp.status}`);
  }
  const encBuffer = await streamResp.arrayBuffer();

  // 5. Decrypt stream using AES-CTR
  const ctrKey = await crypto.subtle.importKey("raw", contentKeyRaw, { name: "AES-CTR" }, false, ["decrypt"]);
  const decAudio = await crypto.subtle.decrypt(
    { name: "AES-CTR", counter: new Uint8Array(contentIvRaw), length: 64 },
    ctrKey,
    encBuffer
  );

  // 6. Trigger direct browser download
  const blob = new Blob([decAudio], { type: "audio/mp4" });
  const blobUrl = URL.createObjectURL(blob);

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

      chrome.runtime.sendMessage({
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
        Скачать
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
    btn.textContent = "⏳ Расшифровка...";

    try {
      await downloadSunoTrack(uuid, titleText);
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
          Скачать
        `;
      }, 3000);
    } catch (err) {
      console.error("[SunoSaver] Download error:", err);
      btn.classList.remove("loading");
      btn.textContent = "🤖 В Telegram...";
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
chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
  if (request.action === "download_current_page_track") {
    const uuid = extractUUID(window.location.pathname) || request.uuid;
    if (uuid) {
      downloadSunoTrack(uuid, request.title, request.author)
        .then((res) => sendResponse({ success: true, ...res }))
        .catch((err) => sendResponse({ success: false, error: err.message }));
      return true;
    }
  }
});

// Observe dynamic DOM changes (SPA navigation)
const observer = new MutationObserver(() => {
  monitorAudioPlayer();
  injectFloatingDownloadButton();
});

observer.observe(document.body, { childList: true, subtree: true });

monitorAudioPlayer();
injectFloatingDownloadButton();
