const personaPicker = document.getElementById("persona-picker");
const switchPersonaLink = document.getElementById("switch-persona-link");

if (personaPicker) {
  personaPicker.addEventListener("click", async (event) => {
    const card = event.target.closest(".persona-card");
    if (!card) {
      return;
    }
    card.disabled = true;
    try {
      await fetch("/api/select-persona", {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ persona_id: card.dataset.personaId }),
      });
      window.location.reload();
    } catch (err) {
      card.disabled = false;
    }
  });
}

if (switchPersonaLink) {
  switchPersonaLink.addEventListener("click", async (event) => {
    event.preventDefault();
    await fetch("/api/clear-persona", { method: "POST", credentials: "same-origin" });
    window.location.reload();
  });
}

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
  entry.innerHTML = `<strong>Assistant:</strong> <span></span>`;
  entry.querySelector("span").innerHTML = linkify(text);
}

function renderPendingAction(entry, pendingId, description) {
  entry.classList.add("pending-action");
  entry.innerHTML = `
    <strong>Assistant:</strong>
    <p class="pending-action-description"></p>
    <div class="pending-action-buttons">
      <button type="button" class="confirm-btn">Confirm</button>
      <button type="button" class="cancel-btn">Cancel</button>
    </div>
  `;
  entry.querySelector(".pending-action-description").textContent = description;

  const resolve = async (decision) => {
    entry.querySelectorAll("button").forEach((b) => (b.disabled = true));
    try {
      const res = await fetch("/api/confirm-action", {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ pending_id: pendingId, decision }),
      });
      const data = await res.json();
      entry.classList.remove("pending-action");
      if (!res.ok) {
        entry.classList.add("error-text");
        entry.innerHTML = `<strong>Assistant:</strong> <span></span>`;
        entry.querySelector("span").textContent = data.error || "Something went wrong.";
        return;
      }
      renderResult(entry, data);
    } catch (err) {
      entry.classList.remove("pending-action");
      entry.classList.add("error-text");
      entry.innerHTML = `<strong>Assistant:</strong> <span></span>`;
      entry.querySelector("span").textContent = String(err);
    } finally {
      transcript.scrollTop = transcript.scrollHeight;
    }
  };

  entry.querySelector(".confirm-btn").addEventListener("click", () => resolve("confirm"));
  entry.querySelector(".cancel-btn").addEventListener("click", () => resolve("cancel"));
}

function renderResult(entry, data) {
  if (data.type === "pending_action") {
    renderPendingAction(entry, data.pending_id, data.description);
  } else {
    renderAnswer(entry, data.answer);
  }
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

if (starters) {
  starters.addEventListener("click", (event) => {
    const chip = event.target.closest(".starter-chip");
    if (!chip) {
      return;
    }
    input.value = chip.textContent;
    form.requestSubmit();
  });
}

if (form) form.addEventListener("submit", async (event) => {
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

    renderResult(pending, data);
  } catch (err) {
    pending.classList.add("error-text");
    pending.querySelector("span").textContent = String(err);
  } finally {
    button.disabled = false;
    transcript.scrollTop = transcript.scrollHeight;
  }
});
