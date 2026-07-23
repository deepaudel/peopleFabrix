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

const STEPS_STORAGE_KEY = "pf_show_steps";
const showStepsToggle = document.getElementById("show-steps-toggle");

function getShowSteps() {
  const stored = localStorage.getItem(STEPS_STORAGE_KEY);
  return stored === null ? true : stored === "true";
}

if (showStepsToggle) {
  showStepsToggle.checked = getShowSteps();
  showStepsToggle.addEventListener("change", () => {
    localStorage.setItem(STEPS_STORAGE_KEY, String(showStepsToggle.checked));
  });
}

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
    await streamRequest(
      "/api/confirm-action",
      { pending_id: pendingId, decision },
      makeStreamHandlers(entry, () => entry.classList.remove("pending-action"))
    );
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

function renderError(entry, message) {
  entry.classList.add("error-text");
  entry.innerHTML = `<strong>Assistant:</strong> <span></span>`;
  entry.querySelector("span").textContent = message || "Something went wrong.";
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

function addStreamingEntry() {
  clearPlaceholder();
  const entry = document.createElement("div");
  entry.className = "transcript-entry assistant";
  entry.innerHTML = `
    <strong>Assistant:</strong>
    <ul class="step-list"></ul>
    <span class="answer-text">Thinking…</span>
  `;
  transcript.appendChild(entry);
  transcript.scrollTop = transcript.scrollHeight;
  return entry;
}

// Parses one complete SSE message ("event: x\ndata: y") and dispatches it
// to the matching handler.
function dispatchSSEMessage(raw, handlers) {
  let eventName = "message";
  const dataLines = [];
  for (const line of raw.split("\n")) {
    if (line.startsWith("event:")) {
      eventName = line.slice(6).trim();
    } else if (line.startsWith("data:")) {
      dataLines.push(line.slice(5).trim());
    }
  }
  if (!dataLines.length) {
    return;
  }
  const data = JSON.parse(dataLines.join("\n"));
  if (eventName === "step") handlers.onStep(data);
  else if (eventName === "answer_reset") handlers.onAnswerReset();
  else if (eventName === "answer_delta") handlers.onAnswerDelta(data.text);
  else if (eventName === "result") handlers.onResult(data);
  else if (eventName === "error") handlers.onError(data.error);
}

// POSTs a JSON body and consumes the response as an SSE stream, dispatching
// each event to the matching handler. Fully consumes the stream regardless
// of whether steps are being rendered — the "Show steps" toggle only gates
// rendering, not how much of the stream is read.
async function streamRequest(url, body, handlers) {
  let res;
  try {
    res = await fetch(url, {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  } catch (err) {
    handlers.onError(String(err));
    return;
  }

  if (!res.ok) {
    let data = {};
    try {
      data = await res.json();
    } catch {
      // response wasn't JSON — fall through with an empty error message
    }
    handlers.onError(data.error || `Request failed (${res.status})`);
    return;
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) {
        break;
      }
      buffer += decoder.decode(value, { stream: true });
      let idx;
      while ((idx = buffer.indexOf("\n\n")) !== -1) {
        const rawMessage = buffer.slice(0, idx);
        buffer = buffer.slice(idx + 2);
        dispatchSSEMessage(rawMessage, handlers);
      }
    }
    if (buffer.trim()) {
      console.warn("SSE stream ended with unterminated message:", buffer);
    }
  } catch (err) {
    handlers.onError(String(err));
  }
}

// Builds the standard set of stream handlers for a transcript entry that's
// showing live steps + a streaming answer, finishing with the same
// renderResult() used everywhere else once the terminal event arrives.
function makeStreamHandlers(entry, onBeforeResult) {
  const stepList = entry.querySelector(".step-list");
  const answerText = entry.querySelector(".answer-text");
  let sawAnyDelta = false;

  return {
    onStep(data) {
      if (!getShowSteps() || !stepList) {
        return;
      }
      const li = document.createElement("li");
      li.textContent = data.label;
      stepList.appendChild(li);
      transcript.scrollTop = transcript.scrollHeight;
    },
    onAnswerReset() {
      sawAnyDelta = false;
      if (answerText) {
        answerText.textContent = "";
      }
    },
    onAnswerDelta(text) {
      if (!answerText) {
        return;
      }
      sawAnyDelta = true;
      answerText.textContent += text;
      transcript.scrollTop = transcript.scrollHeight;
    },
    onResult(data) {
      if (onBeforeResult) {
        onBeforeResult();
      }
      renderResult(entry, data);
      transcript.scrollTop = transcript.scrollHeight;
    },
    onError(message) {
      renderError(entry, message);
      transcript.scrollTop = transcript.scrollHeight;
    },
  };
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
  const pending = addStreamingEntry();

  try {
    await streamRequest("/api/ask", { question }, makeStreamHandlers(pending));
  } finally {
    button.disabled = false;
    transcript.scrollTop = transcript.scrollHeight;
  }
});
