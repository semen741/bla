const startRange = document.getElementById("startRange");
const endRange = document.getElementById("endRange");
const startValue = document.getElementById("startValue");
const endValue = document.getElementById("endValue");
const muteToggle = document.getElementById("muteToggle");
const audioToggle = document.getElementById("audioToggle");
const submitButton = document.getElementById("submitButton");
const previewButton = document.getElementById("previewButton");

const tg = window.Telegram?.WebApp;
if (tg) {
  tg.expand();
}

function clampRanges() {
  let start = Number(startRange.value);
  let end = Number(endRange.value);
  if (end - start < 0.5) {
    end = Math.min(60, start + 0.5);
  }
  if (end > 60) {
    end = 60;
  }
  if (start < 0) {
    start = 0;
  }
  if (start >= end) {
    start = Math.max(0, end - 0.5);
  }
  startRange.value = start.toFixed(1);
  endRange.value = end.toFixed(1);
  startValue.textContent = `${start.toFixed(1)}s`;
  endValue.textContent = `${end.toFixed(1)}s`;
}

startRange.addEventListener("input", clampRanges);
endRange.addEventListener("input", clampRanges);

function toggleButton(button) {
  const active = button.dataset.active === "true";
  button.dataset.active = (!active).toString();
}

muteToggle.addEventListener("click", () => toggleButton(muteToggle));
audioToggle.addEventListener("click", () => toggleButton(audioToggle));

previewButton.addEventListener("click", () => {
  previewButton.classList.toggle("active");
});

submitButton.addEventListener("click", () => {
  const payload = {
    start: Number(startRange.value),
    end: Number(endRange.value),
    mute: muteToggle.dataset.active === "true",
    audioOnly: audioToggle.dataset.active === "true",
  };

  if (payload.audioOnly) {
    payload.mute = false;
  }

  if (tg) {
    tg.sendData(JSON.stringify(payload));
    tg.close();
  } else {
    alert(`Payload: ${JSON.stringify(payload, null, 2)}`);
  }
});

clampRanges();
