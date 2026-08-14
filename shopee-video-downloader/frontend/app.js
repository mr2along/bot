// Set this when frontend and backend are deployed separately.
// Example: window.SHOPEE_API_BASE = "https://your-api.example.com";
const API_BASE = (window.SHOPEE_API_BASE || "").replace(/\/$/, "");

const urlInput = document.getElementById("url");
const resolveButton = document.getElementById("resolve");
const pasteButton = document.getElementById("paste");
const statusBox = document.getElementById("status");
const result = document.getElementById("result");
const preview = document.getElementById("preview");
const title = document.getElementById("title");
const details = document.getElementById("details");
const download = document.getElementById("download");

function setStatus(message, type = "") {
  statusBox.textContent = message;
  statusBox.className = `status ${type}`;
}

function isShopeeUrl(value) {
  try {
    const host = new URL(value).hostname.toLowerCase();
    return host === "shopee.vn" || host === "www.shopee.vn" || host === "vn.shp.ee" || host.endsWith(".shopee.vn");
  } catch {
    return false;
  }
}

async function resolveVideo() {
  const url = urlInput.value.trim();
  if (!isShopeeUrl(url)) {
    setStatus("Vui lòng nhập link Shopee hợp lệ.", "error");
    return;
  }

  resolveButton.disabled = true;
  result.classList.add("hidden");
  setStatus("Đang phân tích link Shopee…", "loading");

  try {
    const response = await fetch(`${API_BASE}/api/resolve`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url })
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || "Không lấy được video.");

    preview.src = data.url;
    title.textContent = data.title || "Shopee video";
    const size = data.width && data.height ? `${data.width}×${data.height}` : "";
    details.textContent = [size, data.duration ? `${Math.round(data.duration)} giây` : ""].filter(Boolean).join(" · ");
    download.href = data.url;
    download.download = `${(data.title || "shopee-video").replace(/[^\p{L}\p{N}_-]+/gu, "-").slice(0, 80)}.mp4`;
    result.classList.remove("hidden");
    setStatus("Đã tìm thấy video.", "success");
  } catch (error) {
    setStatus(error.message || "Có lỗi xảy ra.", "error");
  } finally {
    resolveButton.disabled = false;
  }
}

pasteButton.addEventListener("click", async () => {
  try {
    urlInput.value = await navigator.clipboard.readText();
    setStatus("Đã dán link.");
  } catch {
    urlInput.focus();
    setStatus("Không đọc được clipboard. Hãy dán thủ công.", "error");
  }
});

resolveButton.addEventListener("click", resolveVideo);
urlInput.addEventListener("keydown", event => {
  if (event.key === "Enter") resolveVideo();
});

// Optional Android/share launcher flow: /?url=<encoded-shopee-url>
const sharedUrl = new URLSearchParams(location.search).get("url");
if (sharedUrl && isShopeeUrl(sharedUrl)) {
  urlInput.value = sharedUrl;
  resolveVideo();
}
