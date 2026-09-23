/**
 * SunoSaver Background Service Worker (Manifest V3)
 * Handles audio downloads, cross-origin requests, and Telegram redirection.
 */

// In-memory cache of currently detected track
let currentPlayingTrack = null;

// Listen for messages from content scripts and popup
chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message.action === "track_detected") {
    currentPlayingTrack = message.data;
    sendResponse({ success: true });
    return true;
  }

  if (message.action === "get_active_track") {
    sendResponse({ track: currentPlayingTrack });
    return true;
  }

  if (message.action === "download_audio") {
    const { url, title, author } = message;
    if (!url) {
      sendResponse({ success: false, error: "No URL provided" });
      return true;
    }

    // Clean filename
    let cleanTitle = (title || "Suno_Track").replace(/[\\/:*?"<>|]/g, "_").trim();
    let cleanAuthor = (author || "Suno").replace(/[\\/:*?"<>|]/g, "_").trim();
    let filename = `${cleanTitle} - ${cleanAuthor}.mp3`;

    chrome.downloads.download(
      {
        url: url,
        filename: filename,
        saveAs: false,
        conflictAction: "uniquify"
      },
      (downloadId) => {
        if (chrome.runtime.lastError) {
          console.error("Download failed:", chrome.runtime.lastError);
          sendResponse({ success: false, error: chrome.runtime.lastError.message });
        } else {
          sendResponse({ success: true, downloadId: downloadId });
        }
      }
    );
    return true; // Keep channel open for async response
  }

  if (message.action === "open_telegram") {
    const url = message.url || "https://t.me/sunosaver_bot";
    chrome.tabs.create({ url: url });
    sendResponse({ success: true });
    return true;
  }
});
