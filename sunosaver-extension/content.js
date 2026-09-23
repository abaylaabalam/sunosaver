/**
 * SunoSaver Content Script
 * Injected on suno.com to detect music, inject download buttons, and listen to the player.
 */

// UUID RegEx
const UUID_REGEX = /[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/i;

// Extract UUID from string
function extractUUID(str) {
  if (!str) return null;
  const match = str.match(UUID_REGEX);
  return match ? match[0] : null;
}

// Track player state
function monitorAudioPlayer() {
  const audios = document.querySelectorAll("audio");
  audios.forEach((audio) => {
    if (audio.dataset.sunosaverHooked) return;
    audio.dataset.sunosaverHooked = "true";

    const updatePlayingTrack = () => {
      const src = audio.src || audio.currentSrc;
      if (!src) return;

      const uuid = extractUUID(src) || extractUUID(window.location.pathname);
      
      // Try to find track title & author from page or player
      let title = document.title.replace(" | Suno", "").trim();
      let author = "Suno Creator";

      const titleEl = document.querySelector('h1, [data-testid="song-title"], .song-title');
      if (titleEl && titleEl.textContent) {
        title = titleEl.textContent.trim();
      }

      const authorEl = document.querySelector('[data-testid="song-artist"], a[href^="/@"]');
      if (authorEl && authorEl.textContent) {
        author = authorEl.textContent.trim().replace("@", "");
      }

      chrome.runtime.sendMessage({
        action: "track_detected",
        data: {
          uuid: uuid,
          audioUrl: src,
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

// Injects a floating download bar on song pages
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

  const titleText = document.title.replace(" | Suno", "").trim() || "Suno Track";

  widget.innerHTML = `
    <div style="display: flex; align-items: center; gap: 8px;">
      <span style="font-size: 16px;">⚡️</span>
      <div class="sunosaver-widget-title">${escapeHTML(titleText)}</div>
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

  // Hook MP3 download
  document.getElementById("sunosaver-download-btn").addEventListener("click", async (e) => {
    const btn = e.currentTarget;
    btn.classList.add("loading");
    btn.textContent = "⏳ Скачиваем...";

    try {
      // Find audio source
      let audioUrl = null;
      const audio = document.querySelector("audio");
      if (audio && (audio.src || audio.currentSrc)) {
        audioUrl = audio.src || audio.currentSrc;
      }

      // If not in audio tag, fallback to Suno CDN url
      if (!audioUrl && uuid) {
        audioUrl = `https://audiopipe.suno.ai/?item_id=${uuid}`;
      }

      chrome.runtime.sendMessage(
        {
          action: "download_audio",
          url: audioUrl,
          title: titleText,
          author: "Suno AI"
        },
        (res) => {
          btn.classList.remove("loading");
          if (res && res.success) {
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
            }, 2500);
          } else {
            // Fallback to Telegram Bot
            window.open(`https://t.me/sunosaver_bot?start=dl_${uuid}`, "_blank");
          }
        }
      );
    } catch (err) {
      console.error(err);
      btn.classList.remove("loading");
      window.open(`https://t.me/sunosaver_bot?start=dl_${uuid}`, "_blank");
    }
  });

  // Hook Telegram Bot button (Stems / WAV)
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

// Observe dynamic DOM changes (SPA navigation)
const observer = new MutationObserver(() => {
  monitorAudioPlayer();
  injectFloatingDownloadButton();
});

observer.observe(document.body, { childList: true, subtree: true });

// Initial run
monitorAudioPlayer();
injectFloatingDownloadButton();
