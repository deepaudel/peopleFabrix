const form = document.getElementById("ask-form");
const input = document.getElementById("question-input");
const button = document.getElementById("ask-button");
const transcript = document.getElementById("transcript");

let hasEntries = false;

function clearPlaceholder() {
  if (!hasEntries) {
    transcript.innerHTML = "";
    hasEntries = true;
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

    pending.querySelector("span").textContent = data.answer;
  } catch (err) {
    pending.classList.add("error-text");
    pending.querySelector("span").textContent = String(err);
  } finally {
    button.disabled = false;
    transcript.scrollTop = transcript.scrollHeight;
  }
});
