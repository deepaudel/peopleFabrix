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

// Drag-to-resize split pane between the question and response panels.
// Width is expressed as the left (question) panel's percentage of the
// .panels container; the splitter's own width is subtracted so the two
// panels plus the splitter always sum to exactly 100%.
const PANEL_WIDTH_STORAGE_KEY = "pf_panel_width";
const PANEL_WIDTH_MIN = 20;
const PANEL_WIDTH_MAX = 70;
const panelsContainer = document.querySelector(".panels");
const splitter = document.getElementById("panel-splitter");
const questionPanel = document.getElementById("question-panel");
const responsePanel = document.getElementById("response-panel");

function applyPanelWidth(leftPercent) {
  const clamped = Math.min(PANEL_WIDTH_MAX, Math.max(PANEL_WIDTH_MIN, leftPercent));
  if (questionPanel) {
    questionPanel.style.width = `calc(${clamped}% - 4px)`;
  }
  if (responsePanel) {
    responsePanel.style.width = `calc(${100 - clamped}% - 4px)`;
  }
  return clamped;
}

if (splitter && panelsContainer && questionPanel && responsePanel) {
  const stored = localStorage.getItem(PANEL_WIDTH_STORAGE_KEY);
  applyPanelWidth(stored !== null ? Number(stored) : 40);

  let dragging = false;

  const onMove = (clientX) => {
    const rect = panelsContainer.getBoundingClientRect();
    const percent = ((clientX - rect.left) / rect.width) * 100;
    const clamped = applyPanelWidth(percent);
    localStorage.setItem(PANEL_WIDTH_STORAGE_KEY, String(clamped));
  };

  const stopDragging = () => {
    if (!dragging) {
      return;
    }
    dragging = false;
    splitter.classList.remove("dragging");
    document.body.style.cursor = "";
    document.body.style.userSelect = "";
  };

  splitter.addEventListener("mousedown", (event) => {
    event.preventDefault();
    dragging = true;
    splitter.classList.add("dragging");
    document.body.style.cursor = "col-resize";
    document.body.style.userSelect = "none";
  });

  document.addEventListener("mousemove", (event) => {
    if (dragging) {
      onMove(event.clientX);
    }
  });
  document.addEventListener("mouseup", stopDragging);

  // Keyboard accessibility: arrow keys nudge the split by 2% while the
  // splitter has focus, matching the ARIA separator role in index.html.
  splitter.addEventListener("keydown", (event) => {
    const current = questionPanel.getBoundingClientRect().width;
    const total = panelsContainer.getBoundingClientRect().width;
    const currentPercent = (current / total) * 100;
    if (event.key === "ArrowLeft") {
      const clamped = applyPanelWidth(currentPercent - 2);
      localStorage.setItem(PANEL_WIDTH_STORAGE_KEY, String(clamped));
    } else if (event.key === "ArrowRight") {
      const clamped = applyPanelWidth(currentPercent + 2);
      localStorage.setItem(PANEL_WIDTH_STORAGE_KEY, String(clamped));
    }
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

// Applies inline formatting (bold, markdown links, bare URLs) to a single
// already-trimmed line. Escapes first, then only ever injects real tags
// around already-escaped text, same safety order the old linkify() used.
function formatInline(line) {
  let html = escapeHtml(line);

  html = html.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");

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

// Renders the final, complete answer text as HTML: paragraphs, bold,
// headings, and bullet/numbered lists. Only ever called on the terminal,
// fully-arrived answer (see renderAnswer) — never on in-flight streaming
// deltas, since partial markdown (e.g. an unclosed "**") can't be parsed
// safely or predictably mid-stream.
function renderMarkdown(text) {
  const lines = text.split("\n");
  const htmlParts = [];
  let listItems = [];
  let listTag = null;

  function flushList() {
    if (listItems.length) {
      htmlParts.push(`<${listTag}>${listItems.join("")}</${listTag}>`);
      listItems = [];
      listTag = null;
    }
  }

  for (const rawLine of lines) {
    const line = rawLine.trim();
    const headingMatch = line.match(/^#{1,4}\s+(.*)$/);
    const bulletMatch = line.match(/^[-*]\s+(.*)$/);
    const numberedMatch = line.match(/^\d+\.\s+(.*)$/);

    if (headingMatch) {
      flushList();
      htmlParts.push(`<p class="answer-heading">${formatInline(headingMatch[1])}</p>`);
    } else if (bulletMatch) {
      if (listTag !== "ul") {
        flushList();
        listTag = "ul";
      }
      listItems.push(`<li>${formatInline(bulletMatch[1])}</li>`);
    } else if (numberedMatch) {
      if (listTag !== "ol") {
        flushList();
        listTag = "ol";
      }
      listItems.push(`<li>${formatInline(numberedMatch[1])}</li>`);
    } else if (line === "") {
      flushList();
    } else {
      flushList();
      htmlParts.push(`<p>${formatInline(line)}</p>`);
    }
  }
  flushList();

  return htmlParts.join("");
}

function renderFeedback(container, traceId) {
  const div = document.createElement("div");
  div.className = "feedback-buttons";
  div.innerHTML = `
    <button type="button" class="feedback-btn feedback-up" title="Good response" aria-label="Thumbs up">👍</button>
    <button type="button" class="feedback-btn feedback-down" title="Bad response" aria-label="Thumbs down">👎</button>
  `;

  const send = async (rating, btn) => {
    div.querySelectorAll("button").forEach((b) => (b.disabled = true));
    try {
      const res = await fetch("/api/feedback", {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ trace_id: traceId, rating }),
      });
      if (res.ok) {
        btn.classList.add("selected");
      } else {
        div.querySelectorAll("button").forEach((b) => (b.disabled = false));
      }
    } catch {
      div.querySelectorAll("button").forEach((b) => (b.disabled = false));
    }
  };

  div.querySelector(".feedback-up").addEventListener("click", (e) => send("up", e.currentTarget));
  div.querySelector(".feedback-down").addEventListener("click", (e) => send("down", e.currentTarget));
  container.appendChild(div);
}

function renderAttachments(container, attachments) {
  if (!attachments || !attachments.length) {
    return;
  }
  const div = document.createElement("div");
  div.className = "attachment-list";
  for (const att of attachments) {
    const a = document.createElement("a");
    a.className = "attachment-link";
    a.href = `/api/documents/${encodeURIComponent(att.download_token)}`;
    a.download = att.filename;
    a.textContent = `⬇ ${att.filename}`;
    div.appendChild(a);
  }
  container.appendChild(div);
}

function renderAnswer(entry, text, traceId, attachments) {
  const body = entry.querySelector(".entry-body");
  body.innerHTML = `<span class="answer-text"></span>`;
  body.querySelector(".answer-text").innerHTML = renderMarkdown(text);
  renderAttachments(body, attachments);
  if (traceId) {
    renderFeedback(body, traceId);
  }
}

function renderPendingAction(entry, pendingId, description, attachments) {
  entry.classList.add("pending-action");
  const body = entry.querySelector(".entry-body");
  body.innerHTML = `
    <p class="pending-action-description"></p>
    <div class="pending-action-buttons">
      <button type="button" class="confirm-btn">Confirm</button>
      <button type="button" class="cancel-btn">Cancel</button>
    </div>
  `;
  body.querySelector(".pending-action-description").textContent = description;
  renderAttachments(body, attachments);

  const resolve = async (decision) => {
    entry.querySelectorAll("button").forEach((b) => (b.disabled = true));
    // Reseed with a fresh answer-text span so the resumed generation's
    // steps/text render live too (into the same, already-populated
    // .step-list), instead of only appearing once the resume finishes.
    body.innerHTML = `<span class="answer-text">Thinking…</span>`;
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
    renderPendingAction(entry, data.pending_id, data.description, data.attachments);
  } else {
    renderAnswer(entry, data.answer, data.trace_id, data.attachments);
  }
}

function renderError(entry, message) {
  entry.classList.add("error-text");
  // If this error happened mid-confirm/cancel, don't leave the entry stuck:
  // strip the pending-action styling and re-enable any disabled buttons.
  entry.classList.remove("pending-action");
  entry.querySelectorAll("button").forEach((b) => (b.disabled = false));
  const body = entry.querySelector(".entry-body");
  body.innerHTML = `<span class="answer-text"></span>`;
  body.querySelector(".answer-text").textContent = message || "Something went wrong.";
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
    <div class="entry-body"><span class="answer-text">Thinking…</span></div>
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
