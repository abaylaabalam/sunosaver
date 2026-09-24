/**
 * SunoSaver Core Downloader Engine
 * Decrypts Suno AES-CTR audio streams, decodes via Web Audio API,
 * and encodes to standard MP3 (via lamejs) or lossless PCM WAV.
 * Includes Daily Limits (10 tracks/day Free) & PRO License Manager.
 */

const UUID_REGEX = /[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/i;
const FREE_DAILY_LIMIT = 10;
const DEV_MASTER_KEY = "SUNO-VIP-PRO-2026";

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
 * Subscription & Usage Manager
 */
async function getSubscriptionStatus() {
  try {
    if (typeof chrome !== "undefined" && chrome.storage && chrome.storage.sync) {
      const syncData = await chrome.storage.sync.get(["isPro", "licenseKey"]);
      if (syncData && syncData.isPro) {
        return { isPro: true, licenseKey: syncData.licenseKey };
      }
    }
  } catch (e) {}

  const today = new Date().toISOString().slice(0, 10);
  let count = 0;
  try {
    if (typeof chrome !== "undefined" && chrome.storage && chrome.storage.local) {
      const localData = await chrome.storage.local.get(["usageDate", "downloadCount"]);
      if (localData && localData.usageDate === today) {
        count = localData.downloadCount || 0;
      } else {
        await chrome.storage.local.set({ usageDate: today, downloadCount: 0 });
      }
    }
  } catch (e) {}

  return {
    isPro: false,
    remaining: Math.max(0, FREE_DAILY_LIMIT - count),
    used: count,
    limit: FREE_DAILY_LIMIT
  };
}

async function recordDownloadUsage() {
  const status = await getSubscriptionStatus();
  if (status.isPro) return true;

  const today = new Date().toISOString().slice(0, 10);
  try {
    if (typeof chrome !== "undefined" && chrome.storage && chrome.storage.local) {
      const localData = await chrome.storage.local.get(["usageDate", "downloadCount"]);
      let count = 0;
      if (localData && localData.usageDate === today) {
        count = localData.downloadCount || 0;
      }
      count++;
      await chrome.storage.local.set({ usageDate: today, downloadCount: count });
    }
  } catch (e) {}
  return true;
}

async function canDownload(format = "mp3") {
  const status = await getSubscriptionStatus();
  if (status.isPro) return { allowed: true, isPro: true };

  if (format === "wav") {
    return {
      allowed: false,
      reason: "wav_pro_only",
      message: "Studio WAV (Lossless) is a PRO feature. Upgrade to PRO to unlock uncompressed audio!"
    };
  }

  if (status.remaining <= 0) {
    return {
      allowed: false,
      reason: "limit_reached",
      message: `You have reached your daily limit of ${FREE_DAILY_LIMIT} free tracks. Upgrade to PRO for unlimited downloads!`
    };
  }

  return { allowed: true, isPro: false, remaining: status.remaining };
}

async function activateLicenseKey(key) {
  const cleanKey = (key || "").trim();
  if (!cleanKey) {
    throw new Error("Please enter a license key.");
  }

  // Developer / Admin bypass test key
  if (cleanKey.toUpperCase() === DEV_MASTER_KEY) {
    await chrome.storage.sync.set({ isPro: true, licenseKey: cleanKey });
    return { success: true, message: "PRO activated successfully!" };
  }

  // Lemon Squeezy License Validation API
  try {
    const resp = await fetch("https://api.lemonsqueezy.com/v1/licenses/validate", {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: new URLSearchParams({ license_key: cleanKey })
    });

    if (!resp.ok) {
      throw new Error(`Validation server returned HTTP ${resp.status}`);
    }

    const data = await resp.json();
    if (data.valid) {
      await chrome.storage.sync.set({ isPro: true, licenseKey: cleanKey });
      return { success: true, message: "PRO activated successfully!" };
    } else {
      throw new Error(data.error || "Invalid or expired license key.");
    }
  } catch (err) {
    throw new Error(err.message || "License validation failed. Please check your internet connection.");
  }
}

/**
 * Converts an AudioBuffer into standard MP3 using lamejs
 */
function encodeAudioBufferToMp3(audioBuffer, bitrate = 256) {
  const channels = audioBuffer.numberOfChannels;
  const sampleRate = audioBuffer.sampleRate;

  const lame = (typeof lamejs !== "undefined" ? lamejs : (typeof window !== "undefined" && window.lamejs ? window.lamejs : (typeof globalThis !== "undefined" ? globalThis.lamejs : null)));
  if (!lame || !lame.Mp3Encoder) {
    throw new Error("lamejs encoder not loaded");
  }

  const mp3encoder = new lame.Mp3Encoder(channels, sampleRate, bitrate);
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

  const channels = [];
  for (let i = 0; i < buffer.numberOfChannels; i++) {
    channels.push(buffer.getChannelData(i));
  }

  while (offset < buffer.length) {
    for (let i = 0; i < numOfChan; i++) {
      let sample = Math.max(-1, Math.min(1, channels[i][offset]));
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
 * Enforces limits -> Decrypts Suno AES-CTR -> Decodes to AudioBuffer -> Encodes to MP3 or WAV -> Records usage
 */
async function downloadSunoTrack(uuid, fallbackTitle, fallbackAuthor, format = "mp3") {
  // Check daily limit or PRO feature access
  const check = await canDownload(format);
  if (!check.allowed) {
    throw new Error(check.message);
  }

  let title = fallbackTitle || "Suno Track";
  let author = fallbackAuthor || "Suno AI";

  // 1. Fetch real track metadata from Suno studio API
  try {
    const metaResp = await fetch(`https://studio-api.prod.suno.com/api/clip/${uuid}`, {
      credentials: "include"
    });
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

  // 2. Request mango rights
  const rightsResp = await fetch("https://studio-api.prod.suno.com/api/mango/rights", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    credentials: "include",
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

  let finalBlob;
  let filename;

  // 6. Decode with Web Audio API to produce standard MP3 / WAV
  const AudioContextClass = window.AudioContext || window.webkitAudioContext;
  if (!AudioContextClass) {
    throw new Error("Web Audio API not supported in this browser");
  }

  const audioCtx = new AudioContextClass();
  let audioBuffer;
  try {
    audioBuffer = await audioCtx.decodeAudioData(decAudio.slice(0));
  } finally {
    try { audioCtx.close(); } catch (e) {}
  }

  if (format === "wav") {
    finalBlob = encodeAudioBufferToWav(audioBuffer);
    filename = `${cleanTitle} - ${cleanAuthor}.wav`;
  } else {
    try {
      finalBlob = encodeAudioBufferToMp3(audioBuffer, 256);
      filename = `${cleanTitle} - ${cleanAuthor}.mp3`;
    } catch (mp3Err) {
      console.warn("[SunoSaver] MP3 encoder fallback to WAV:", mp3Err);
      finalBlob = encodeAudioBufferToWav(audioBuffer);
      filename = `${cleanTitle} - ${cleanAuthor}.wav`;
    }
  }

  // 7. Trigger browser download
  const blobUrl = URL.createObjectURL(finalBlob);
  const a = document.createElement("a");
  a.href = blobUrl;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);

  setTimeout(() => URL.revokeObjectURL(blobUrl), 30000);

  // Record successful usage
  await recordDownloadUsage();

  return { success: true, filename: filename };
}
