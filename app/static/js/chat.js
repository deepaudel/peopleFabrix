const form = document.getElementById("ask-form");
const input = document.getElementById("question-input");
const button = document.getElementById("ask-button");
const transcript = document.getElementById("transcript");
const starters = document.getElementById("starters");

let hasEntries = false;

function clearPlaceholder() {
  if (!hasEntries) {
    transcript.innerHTML = "";
    hasEntries = true;
  }
}

function escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = str;
  return div.innerHTML;
}

function linkify(text) {
  let html = escapeHtml(text);

  // Markdown-style links: [title](url)
  html = html.replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, (_match, title, url) => {
    return `<a href="${url}" target="_blank" rel="noopener noreferrer">${title}</a>`;
  });

  // Any remaining bare URLs (not already inside an href="...") get linkified too
  html = html.replace(/(?<!href=")(https?:\/\/[^\s)<]+)/g, (url) => {
    return `<a href="${url}" target="_blank" rel="noopener noreferrer">${url}</a>`;
  });

  return html;
}

function renderAnswer(entry, text) {
  entry.querySelector("span").innerHTML = linkify(text);
}

function addEntry(role, text, isError = false) {
  clearPlaceholder();
  const entry = document.createElement("div");
  entry.className = `transcript-entry ${role}${isError ? " error-text" : ""}`;
  const label = role === "user" ? "You" : "Assistant";
  entry.innerHTML = `<strong>${label}:</strong> <span></span>`;
  entry.querySelector("span").textContent = text;
  transcript.appendChild(entry);
  transcript.scrollTop = transcript.scrollHeight;
  return entry;
}

starters.addEventListener("click", (event) => {
  const chip = event.target.closest(".starter-chip");
  if (!chip) {
    return;
  }
  input.value = chip.textContent;
  form.requestSubmit();
});

form.addEventListener("submit", async (event) => {
  event.preventDefault();

  const question = input.value.trim();
  if (!question) {
    return;
  }

  button.disabled = true;
  addEntry("user", question);
  input.value = "";
  const pending = addEntry("assistant", "Thinking…");

  try {
    const res = await fetch("/api/ask", {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question }),
    });

    const data = await res.json();

    if (!res.ok) {
      pending.classList.add("error-text");
      pending.querySelector("span").textContent = data.error || "Something went wrong.";
      return;
    }

    renderAnswer(pending, data.answer);
  } catch (err) {
    pending.classList.add("error-text");
    pending.querySelector("span").textContent = String(err);
  } finally {
    button.disabled = false;
    transcript.scrollTop = transcript.scrollHeight;
  }
});
