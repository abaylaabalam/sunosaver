document.addEventListener("DOMContentLoaded", () => {
  const trackCard = document.getElementById("track-card");
  const noTrackCard = document.getElementById("no-track-card");
  const trackTitle = document.getElementById("track-title");
  const trackAuthor = document.getElementById("track-author");
  const btnDownloadMp3 = document.getElementById("btn-download-mp3");
  const btnStems = document.getElementById("btn-stems");
  const btnOpenBot = document.getElementById("btn-open-bot");

  let activeTrack = null;

  // Ask background service worker for detected track
  chrome.runtime.sendMessage({ action: "get_active_track" }, (response) => {
    if (response && response.track) {
      displayTrack(response.track);
    } else {
      // Query active tab to see if we're on a suno song page
      chrome.tabs.query({ active: true, currentWindow: true }, (tabs) => {
        const tab = tabs[0];
        if (tab && tab.url && (tab.url.includes("suno.com") || tab.url.includes("suno.ai"))) {
          const match = tab.url.match(/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/i);
          const title = tab.title ? tab.title.replace(" | Suno", "").trim() : "Suno Song";
          
          if (match) {
            displayTrack({
              uuid: match[0],
              title: title,
              author: "Suno Creator",
              audioUrl: `https://audiopipe.suno.ai/?item_id=${match[0]}`
            });
            return;
          }
        }
        showNoTrack();
      });
    }
  });

  function displayTrack(track) {
    activeTrack = track;
    trackTitle.textContent = track.title || "Suno Track";
    trackAuthor.textContent = track.author ? `от @${track.author}` : "Suno AI";
    trackCard.style.display = "flex";
    noTrackCard.style.display = "none";
  }

  function showNoTrack() {
    trackCard.style.display = "none";
    noTrackCard.style.display = "flex";
  }

  // Handle MP3 Download
  btnDownloadMp3.addEventListener("click", () => {
    if (!activeTrack) return;
    btnDownloadMp3.textContent = "⏳ Скачивание...";
    btnDownloadMp3.disabled = true;

    chrome.runtime.sendMessage(
      {
        action: "download_audio",
        url: activeTrack.audioUrl || `https://audiopipe.suno.ai/?item_id=${activeTrack.uuid}`,
        title: activeTrack.title,
        author: activeTrack.author
      },
      (res) => {
        btnDownloadMp3.disabled = false;
        if (res && res.success) {
          btnDownloadMp3.textContent = "✅ Скачано!";
          setTimeout(() => {
            btnDownloadMp3.innerHTML = `
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5">
                <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path>
                <polyline points="7 10 12 15 17 10"></polyline>
                <line x1="12" y1="15" x2="12" y2="3"></line>
              </svg>
              Скачать MP3 (320k)
            `;
          }, 2000);
        } else {
          // Open in bot fallback
          const url = activeTrack.uuid 
            ? `https://t.me/sunosaver_bot?start=dl_${activeTrack.uuid}` 
            : "https://t.me/sunosaver_bot";
          chrome.tabs.create({ url });
        }
      }
    );
  });

  // Handle Stems / Karaoke (Opens bot with deeplink)
  btnStems.addEventListener("click", () => {
    const url = activeTrack && activeTrack.uuid 
      ? `https://t.me/sunosaver_bot?start=dl_${activeTrack.uuid}` 
      : "https://t.me/sunosaver_bot";
    chrome.tabs.create({ url });
  });

  // Handle Open Bot
  btnOpenBot.addEventListener("click", () => {
    chrome.tabs.create({ url: "https://t.me/sunosaver_bot" });
  });
});
