/**
 * SunoSaver Content Script
 * Injected on suno.com & suno.ai:
 * - Detects currently playing track from HTML5 audio elements and URL
 * - Injects floating download widget directly into song pages
 * - Uses shared downloader.js engine (AES decryption + Web Audio decoding + MP3/WAV encoding)
 */

function safeSendMessage(message, callback) {
  try {
    if (typeof chrome !== "undefined" && chrome.runtime && chrome.runtime.id) {
      chrome.runtime.sendMessage(message, (response) => {
        const err = chrome.runtime.lastError;
        if (callback && !err) {
          callback(response);
        }
      });
    }
  } catch (e) {}
}

function escapeHTML(str) {
  if (!str) return "";
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

// Track active audio player
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
      <button id="sunosaver-download-btn" class="sunosaver-btn" title="Download MP3 256k (Free: 10/day)">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5">
          <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path>
          <polyline points="7 10 12 15 17 10"></polyline>
          <line x1="12" y1="15" x2="12" y2="3"></line>
        </svg>
        MP3
      </button>
      <button id="sunosaver-download-wav-btn" class="sunosaver-btn" style="border-color: #06b6d4; background: linear-gradient(135deg, #0e7490 0%, #0f172a 100%);" title="Download uncompressed 48kHz studio WAV (PRO)">
        🎵 WAV
      </button>
      <button id="sunosaver-tg-btn" class="sunosaver-btn" style="border-color: #8b5cf6; background: linear-gradient(135deg, #2e1065 0%, #0f172a 100%);" title="Open in Telegram Bot (Vocals, Karaoke, Video)">
        🎙 Vocals
      </button>
    </div>
  `;

  document.body.appendChild(widget);

  // Hook MP3 Download
  document.getElementById("sunosaver-download-btn").addEventListener("click", async (e) => {
    const btn = e.currentTarget;
    btn.classList.add("loading");
    btn.textContent = "⏳ MP3...";

    try {
      await downloadSunoTrack(uuid, titleText, "Suno AI", "mp3");
      btn.classList.remove("loading");
      btn.classList.add("success");
      btn.textContent = "✅ Saved!";
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
      console.warn("[SunoSaver] Download error:", err);
      btn.classList.remove("loading");
      if (err.message && (err.message.includes("limit") || err.message.includes("PRO"))) {
        alert(err.message);
      } else {
        window.open(`https://t.me/sunosaver_bot?start=dl_${uuid}`, "_blank");
      }
    }
  });

  // Hook WAV Download
  document.getElementById("sunosaver-download-wav-btn").addEventListener("click", async (e) => {
    const btn = e.currentTarget;
    btn.classList.add("loading");
    btn.textContent = "⏳ WAV...";

    try {
      await downloadSunoTrack(uuid, titleText, "Suno AI", "wav");
      btn.classList.remove("loading");
      btn.classList.add("success");
      btn.textContent = "✅ WAV Saved!";
      setTimeout(() => {
        btn.classList.remove("success");
        btn.textContent = "🎵 WAV";
      }, 3000);
    } catch (err) {
      console.warn("[SunoSaver] WAV error:", err);
      btn.classList.remove("loading");
      if (err.message && (err.message.includes("limit") || err.message.includes("PRO"))) {
        alert(err.message);
      } else {
        window.open(`https://t.me/sunosaver_bot?start=dl_${uuid}`, "_blank");
      }
    }
  });

  // Hook Telegram Bot
  document.getElementById("sunosaver-tg-btn").addEventListener("click", () => {
    window.open(`https://t.me/sunosaver_bot?start=dl_${uuid}`, "_blank");
  });
}

// Listen for messages from popup (fallback)
if (typeof chrome !== "undefined" && chrome.runtime && chrome.runtime.onMessage) {
  chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
    if (request.action === "download_current_page_track") {
      const uuid = extractUUID(window.location.pathname) || request.uuid;
      if (uuid) {
        downloadSunoTrack(uuid, request.title, request.author, request.format || "mp3")
          .then((res) => sendResponse({ success: true, ...res }))
          .catch((err) => sendResponse({ success: false, error: err.message }));
        return true;
      }
    }
  });
}

const observer = new MutationObserver(() => {
  monitorAudioPlayer();
  injectFloatingDownloadButton();
});

observer.observe(document.body, { childList: true, subtree: true });

monitorAudioPlayer();
injectFloatingDownloadButton();
