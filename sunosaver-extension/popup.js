document.addEventListener("DOMContentLoaded", () => {
  const trackCard = document.getElementById("track-card");
  const noTrackCard = document.getElementById("no-track-card");
  const trackTitle = document.getElementById("track-title");
  const trackAuthor = document.getElementById("track-author");
  const btnDownloadMp3 = document.getElementById("btn-download-mp3");
  const btnDownloadWav = document.getElementById("btn-download-wav");
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
        const uuid = extractUUID(tab.url);
        const title = tab.title ? tab.title.replace(" | Suno", "").trim() : "Suno Track";

        if (uuid) {
          displayTrack({
            uuid: uuid,
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

  // Helper for download execution (runs directly in popup using downloader.js)
  async function triggerDownload(format = "mp3") {
    if (!activeTrack || !activeTrack.uuid) return;

    const targetBtn = format === "wav" ? btnDownloadWav : btnDownloadMp3;
    const originalText = targetBtn.innerHTML;
    targetBtn.disabled = true;
    targetBtn.textContent = format === "wav" ? "⏳ WAV..." : "⏳ MP3...";

    try {
      if (typeof downloadSunoTrack === "function") {
        await downloadSunoTrack(activeTrack.uuid, activeTrack.title, activeTrack.author, format);
        targetBtn.textContent = format === "wav" ? "✅ WAV готов!" : "✅ Скачано!";
        setTimeout(() => {
          targetBtn.disabled = false;
          targetBtn.innerHTML = originalText;
        }, 3000);
      } else {
        throw new Error("downloader not loaded");
      }
    } catch (err) {
      console.warn(`[SunoSaver] Direct popup download failed, opening Telegram fallback:`, err);
      targetBtn.textContent = "🤖 В Telegram...";
      setTimeout(() => {
        targetBtn.disabled = false;
        targetBtn.innerHTML = originalText;
        chrome.tabs.create({ url: `https://t.me/sunosaver_bot?start=dl_${activeTrack.uuid}` });
      }, 500);
    }
  }

  // Hook MP3 Download
  if (btnDownloadMp3) {
    btnDownloadMp3.addEventListener("click", () => triggerDownload("mp3"));
  }

  // Hook WAV Download
  if (btnDownloadWav) {
    btnDownloadWav.addEventListener("click", () => triggerDownload("wav"));
  }

  // Handle Stems / Karaoke (Opens bot with deeplink)
  if (btnStems) {
    btnStems.addEventListener("click", () => {
      const url = activeTrack && activeTrack.uuid 
        ? `https://t.me/sunosaver_bot?start=dl_${activeTrack.uuid}` 
        : "https://t.me/sunosaver_bot";
      chrome.tabs.create({ url });
    });
  }

  // Handle Open Bot
  if (btnOpenBot) {
    btnOpenBot.addEventListener("click", () => {
      chrome.tabs.create({ url: "https://t.me/sunosaver_bot" });
    });
  }
});
