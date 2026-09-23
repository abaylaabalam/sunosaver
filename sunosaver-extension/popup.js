document.addEventListener("DOMContentLoaded", () => {
  const trackCard = document.getElementById("track-card");
  const noTrackCard = document.getElementById("no-track-card");
  const trackTitle = document.getElementById("track-title");
  const trackAuthor = document.getElementById("track-author");
  const btnDownloadMp3 = document.getElementById("btn-download-mp3");
  const btnStems = document.getElementById("btn-stems");
  const btnOpenBot = document.getElementById("btn-open-bot");

  let activeTrack = null;

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

  // Safely query active tab
  if (typeof chrome !== "undefined" && chrome.tabs && chrome.tabs.query) {
    chrome.tabs.query({ active: true, currentWindow: true }, (tabs) => {
      if (chrome.runtime.lastError || !tabs || tabs.length === 0) {
        showNoTrack();
        return;
      }

      const tab = tabs[0];
      if (tab && tab.url && (tab.url.includes("suno.com") || tab.url.includes("suno.ai"))) {
        const match = tab.url.match(/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/i);
        const title = tab.title ? tab.title.replace(" | Suno", "").trim() : "Suno Track";

        if (match) {
          displayTrack({
            uuid: match[0],
            title: title,
            author: "Suno Creator",
            tabId: tab.id
          });
          return;
        }
      }

      // Try background active track
      if (chrome.runtime && chrome.runtime.sendMessage) {
        chrome.runtime.sendMessage({ action: "get_active_track" }, (response) => {
          if (chrome.runtime.lastError) {
            showNoTrack();
            return;
          }
          if (response && response.track && response.track.uuid) {
            displayTrack(response.track);
          } else {
            showNoTrack();
          }
        });
      } else {
        showNoTrack();
      }
    });
  } else {
    showNoTrack();
  }

  // Handle Download Click
  btnDownloadMp3.addEventListener("click", () => {
    if (!activeTrack || !activeTrack.uuid) return;
    btnDownloadMp3.textContent = "⏳ Конвертация MP3...";
    btnDownloadMp3.disabled = true;

    chrome.tabs.query({ active: true, currentWindow: true }, (tabs) => {
      const activeTab = tabs && tabs[0];
      if (activeTab && activeTab.id) {
        chrome.tabs.sendMessage(
          activeTab.id,
          {
            action: "download_current_page_track",
            uuid: activeTrack.uuid,
            title: activeTrack.title,
            author: activeTrack.author,
            format: "mp3"
          },
          (res) => {
            btnDownloadMp3.disabled = false;
            const err = chrome.runtime.lastError;
            if (err || !res || !res.success) {
              // Page may need a refresh after extension update -> redirect to bot
              btnDownloadMp3.textContent = "🤖 В Telegram...";
              setTimeout(() => {
                chrome.tabs.create({ url: `https://t.me/sunosaver_bot?start=dl_${activeTrack.uuid}` });
                btnDownloadMp3.innerHTML = `
                  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5">
                    <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path>
                    <polyline points="7 10 12 15 17 10"></polyline>
                    <line x1="12" y1="15" x2="12" y2="3"></line>
                  </svg>
                  Скачать MP3
                `;
              }, 600);
            } else {
              btnDownloadMp3.textContent = "✅ Скачано!";
              setTimeout(() => {
                btnDownloadMp3.innerHTML = `
                  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5">
                    <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path>
                    <polyline points="7 10 12 15 17 10"></polyline>
                    <line x1="12" y1="15" x2="12" y2="3"></line>
                  </svg>
                  Скачать MP3
                `;
              }, 2500);
            }
          }
        );
      } else {
        btnDownloadMp3.disabled = false;
        chrome.tabs.create({ url: `https://t.me/sunosaver_bot?start=dl_${activeTrack.uuid}` });
      }
    });
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
