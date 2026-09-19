const POEMS_URL = "poems.json";
const TARGET_DAYS = 100;

async function loadPoems() {
  const statusEl = document.getElementById("status");
  const timelineEl = document.getElementById("timeline");
  const countEl = document.getElementById("poem-count");

  const showStatus = (message) => {
    statusEl.textContent = message;
    statusEl.hidden = false;
  };

  try {
    const response = await fetch(`${POEMS_URL}?_=${Date.now()}`, { cache: "no-store" });
    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`);
    }
    const poems = await response.json();

    if (!Array.isArray(poems) || poems.length === 0) {
      showStatus(
        "अजून एकही कविता प्रकाशित झालेली नाही. उद्या सकाळी पहिली कविता इथे दिसेल! " +
          "(No poems published yet — check back tomorrow morning.)"
      );
      return;
    }

    statusEl.hidden = true;
    timelineEl.innerHTML = poems.map(renderPoemCard).join("");

    countEl.textContent = `Day ${poems.length} of ${TARGET_DAYS}`;
    countEl.hidden = false;
  } catch (err) {
    console.error("Failed to load poems.json", err);
    showStatus(
      "कविता लोड करताना त्रुटी आली. कृपया नंतर पुन्हा प्रयत्न करा. " +
        "(Failed to load poems — please try again later.)"
    );
  }
}

function renderPoemCard(entry) {
  const displayDate = escapeHtml(entry.display_date || entry.date || "");
  const poemText = escapeHtml(entry.poem || "");
  const headlines = Array.isArray(entry.headlines) ? entry.headlines : [];

  const headlinesHtml = headlines.length
    ? `
      <div class="poem-card__headlines">
        <p class="poem-card__headlines-title">स्रोत बातम्या (Source headlines)</p>
        <ul>
          ${headlines.map(renderHeadline).join("")}
        </ul>
      </div>
    `
    : "";

  return `
    <li class="poem-card">
      <span class="poem-card__date">${displayDate}</span>
      <p class="poem-card__poem">${poemText}</p>
      ${headlinesHtml}
    </li>
  `;
}

function renderHeadline(headline) {
  const title = escapeHtml(headline.title || "");
  const link = headline.link ? escapeHtml(headline.link) : "";
  if (link) {
    return `<li><a href="${link}" target="_blank" rel="noopener noreferrer">${title}</a></li>`;
  }
  return `<li>${title}</li>`;
}

function escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = str;
  return div.innerHTML;
}

document.addEventListener("DOMContentLoaded", loadPoems);
