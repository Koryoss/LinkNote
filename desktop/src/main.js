const API_URL = "http://127.0.0.1:8000";

const statusEl = document.getElementById("status");
const openBtn = document.getElementById("open-backend");
const checkBtn = document.getElementById("check-backend");

async function checkBackend() {
  statusEl.textContent = "Checking local API...";
  try {
    const response = await fetch(`${API_URL}/auth/config`);
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    statusEl.textContent = "Local API is running. The desktop window will show the gallery.";
  } catch (error) {
    statusEl.textContent = "Local API is not reachable. Run the backend first, then reopen the desktop app.";
  }
}

openBtn.addEventListener("click", () => {
  window.location.href = API_URL;
});

checkBtn.addEventListener("click", checkBackend);

checkBackend();
