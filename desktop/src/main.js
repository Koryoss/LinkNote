const API_URL = "http://127.0.0.1:8000";

const statusEl = document.getElementById("status");
const openBtn = document.getElementById("open-backend");
const checkBtn = document.getElementById("check-backend");

async function checkBackend({ autoOpen = false } = {}) {
  statusEl.textContent = "Checking local API...";
  try {
    const response = await fetch(`${API_URL}/health`);
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const data = await response.json();
    statusEl.textContent = `Local API is running (${data.ok ? "healthy" : "ready"}).`;
    if (autoOpen) window.location.href = API_URL;
  } catch (error) {
    statusEl.textContent = "Local API is not reachable. The app will try to start it automatically on launch.";
  }
}

openBtn.addEventListener("click", () => {
  window.location.href = API_URL;
});

checkBtn.addEventListener("click", () => checkBackend());

setTimeout(() => checkBackend({ autoOpen: true }), 500);
