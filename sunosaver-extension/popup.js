document.addEventListener("DOMContentLoaded", async () => {
  const trackCard = document.getElementById("track-card");
  const noTrackCard = document.getElementById("no-track-card");
  const trackTitle = document.getElementById("track-title");
  const trackAuthor = document.getElementById("track-author");
  const btnDownloadMp3 = document.getElementById("btn-download-mp3");
  const btnDownloadWav = document.getElementById("btn-download-wav");
  const btnStems = document.getElementById("btn-stems");
  const btnOpenBot = document.getElementById("btn-open-bot");

  const planBadge = document.getElementById("plan-badge");
  const usageBox = document.getElementById("usage-box");
  const usageText = document.getElementById("usage-text");
  const progressFill = document.getElementById("progress-fill");

  const proUpgradeCard = document.getElementById("pro-upgrade-card");
  const proActiveCard = document.getElementById("pro-active-card");
  const toggleLicense = document.getElementById("toggle-license");
  const licenseSection = document.getElementById("license-section");
  const licenseInput = document.getElementById("license-input");
  const btnActivateLicense = document.getElementById("btn-activate-license");
  const licenseMsg = document.getElementById("license-msg");

  let activeTrack = null;

  async function updateSubscriptionUI() {
    if (typeof getSubscriptionStatus !== "function") return;
    const status = await getSubscriptionStatus();

    if (status.isPro) {
      planBadge.textContent = "👑 PRO Active";
      planBadge.className = "plan-badge badge-pro";
      if (usageBox) usageBox.style.display = "none";
      if (proUpgradeCard) proUpgradeCard.style.display = "none";
      if (proActiveCard) proActiveCard.style.display = "block";
    } else {
      planBadge.textContent = `Free: ${status.remaining}/${status.limit}`;
      planBadge.className = "plan-badge badge-free";
      if (usageBox) usageBox.style.display = "flex";
      if (proUpgradeCard) proUpgradeCard.style.display = "flex";
      if (proActiveCard) proActiveCard.style.display = "none";

      const pct = Math.min(100, Math.round((status.used / status.limit) * 100));
      if (usageText) usageText.textContent = `${status.used} / ${status.limit}`;
      if (progressFill) progressFill.style.width = `${pct}%`;
    }
  }

  function displayTrack(track) {
    activeTrack = track;
    trackTitle.textContent = track.title || "Suno Track";
    trackAuthor.textContent = track.author ? `by @${track.author}` : "Suno AI";
    trackCard.style.display = "flex";
    noTrackCard.style.display = "none";
  }

  function showNoTrack() {
    trackCard.style.display = "none";
    noTrackCard.style.display = "flex";
  }

  // Initial check
  await updateSubscriptionUI();

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

  // Trigger download with limits check
  async function triggerDownload(format = "mp3") {
    if (!activeTrack || !activeTrack.uuid) return;

    // Check permissions / limits
    const check = await canDownload(format);
    if (!check.allowed) {
      if (check.reason === "wav_pro_only") {
        if (licenseSection) licenseSection.style.display = "block";
        alert("⭐️ Studio WAV is a PRO feature. Enter your PRO license or upgrade below to unlock!");
      } else {
        alert(check.message);
      }
      return;
    }

    const targetBtn = format === "wav" ? btnDownloadWav : btnDownloadMp3;
    const originalContent = targetBtn.innerHTML;
    targetBtn.disabled = true;
    targetBtn.textContent = format === "wav" ? "⏳ WAV..." : "⏳ MP3...";

    try {
      await downloadSunoTrack(activeTrack.uuid, activeTrack.title, activeTrack.author, format);
      targetBtn.textContent = format === "wav" ? "✅ WAV Done!" : "✅ Downloaded!";
      await updateSubscriptionUI();
      setTimeout(() => {
        targetBtn.disabled = false;
        targetBtn.innerHTML = originalContent;
      }, 3000);
    } catch (err) {
      console.warn(`[SunoSaver] Download error:`, err);
      alert(err.message || "Download failed. Redirecting to bot...");
      targetBtn.textContent = "🤖 Telegram...";
      setTimeout(() => {
        targetBtn.disabled = false;
        targetBtn.innerHTML = originalContent;
        chrome.tabs.create({ url: `https://t.me/sunosaver_bot?start=dl_${activeTrack.uuid}` });
      }, 600);
    }
  }

  // Hook MP3 & WAV
  if (btnDownloadMp3) {
    btnDownloadMp3.addEventListener("click", () => triggerDownload("mp3"));
  }

  if (btnDownloadWav) {
    btnDownloadWav.addEventListener("click", () => triggerDownload("wav"));
  }

  // Toggle license section
  if (toggleLicense) {
    toggleLicense.addEventListener("click", () => {
      const isVisible = licenseSection.style.display === "block";
      licenseSection.style.display = isVisible ? "none" : "block";
      if (!isVisible && licenseInput) licenseInput.focus();
    });
  }

  // Activate license key
  if (btnActivateLicense) {
    btnActivateLicense.addEventListener("click", async () => {
      const key = licenseInput.value.trim();
      if (!key) {
        licenseMsg.textContent = "Please enter a key.";
        licenseMsg.className = "license-msg error";
        return;
      }

      btnActivateLicense.textContent = "⏳...";
      btnActivateLicense.disabled = true;

      try {
        const res = await activateLicenseKey(key);
        licenseMsg.textContent = "✅ " + res.message;
        licenseMsg.className = "license-msg success";
        await updateSubscriptionUI();
      } catch (err) {
        licenseMsg.textContent = "❌ " + (err.message || "Activation failed.");
        licenseMsg.className = "license-msg error";
      } finally {
        btnActivateLicense.textContent = "Activate";
        btnActivateLicense.disabled = false;
      }
    });
  }

  // Stems & Telegram
  if (btnStems) {
    btnStems.addEventListener("click", () => {
      const url = activeTrack && activeTrack.uuid 
        ? `https://t.me/sunosaver_bot?start=dl_${activeTrack.uuid}` 
        : "https://t.me/sunosaver_bot";
      chrome.tabs.create({ url });
    });
  }

  if (btnOpenBot) {
    btnOpenBot.addEventListener("click", () => {
      chrome.tabs.create({ url: "https://t.me/sunosaver_bot" });
    });
  }
});
