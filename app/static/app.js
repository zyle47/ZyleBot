const transcript = document.getElementById("transcript");
const form = document.getElementById("chat-form");
const input = document.getElementById("message-input");
const sendBtn = form.querySelector("button[type=submit]");
const statusEl = document.getElementById("status");
const ctxFill = document.getElementById("context-bar-fill");
const ctxText = document.getElementById("context-text");
const convListEl = document.getElementById("conversation-list");
const newChatBtn = document.getElementById("new-chat");

let contextMax = null;
let currentConversationId = null;

// --- Context gauge -------------------------------------------------------

function fmtTokens(n) {
    if (n == null) return "?";
    return n >= 1000 ? (n / 1000).toFixed(n >= 10000 ? 0 : 1) + "k" : String(n);
}

function updateContextGauge(used) {
    if (used == null) {
        ctxFill.style.width = "0%";
        ctxText.textContent = contextMax ? `0 / ${fmtTokens(contextMax)}` : "—";
        return;
    }
    if (contextMax) {
        const pct = Math.min(100, (used / contextMax) * 100);
        ctxFill.style.width = pct + "%";
        ctxFill.className =
            "context-bar-fill" + (pct >= 90 ? " high" : pct >= 70 ? " warn" : "");
        ctxText.textContent = `${fmtTokens(used)} / ${fmtTokens(contextMax)}`;
    } else {
        ctxText.textContent = `${fmtTokens(used)} tokens`;
    }
}

async function refreshHealth() {
    try {
        const res = await fetch("/api/health");
        const data = await res.json();
        statusEl.textContent = data.lmstudio_reachable
            ? `connected (${data.model})`
            : `LM Studio unreachable — start it with "lms server start"`;
        statusEl.className = "status " + (data.lmstudio_reachable ? "ok" : "bad");
        contextMax = data.context_length ?? null;
        updateContextGauge(0);
    } catch (err) {
        statusEl.textContent = "health check failed";
        statusEl.className = "status bad";
    }
}

// --- Transcript rendering helpers ---------------------------------------

function addBubble(role) {
    const el = document.createElement("div");
    el.className = "bubble " + role;
    transcript.appendChild(el);
    transcript.scrollTop = transcript.scrollHeight;
    return el;
}

function addToolBlock(name, argsOrResult, label) {
    const details = document.createElement("details");
    details.className = "tool-block";
    const summary = document.createElement("summary");
    summary.textContent = `${label}: ${name}`;
    const pre = document.createElement("pre");
    pre.textContent = JSON.stringify(argsOrResult, null, 2);
    details.appendChild(summary);
    details.appendChild(pre);
    transcript.appendChild(details);
    transcript.scrollTop = transcript.scrollHeight;
}

function setComposerEnabled(enabled) {
    input.disabled = !enabled;
    sendBtn.disabled = !enabled;
}

// --- Conversations -------------------------------------------------------

async function loadConversationList() {
    const res = await fetch("/api/conversations");
    const convs = await res.json();
    convListEl.innerHTML = "";
    for (const conv of convs) {
        const li = document.createElement("li");
        li.className = "conv-item" + (conv.id === currentConversationId ? " active" : "");
        li.dataset.id = conv.id;

        const title = document.createElement("span");
        title.className = "conv-title";
        title.textContent = conv.title;

        const tokens = document.createElement("span");
        tokens.className = "conv-tokens";
        tokens.textContent = conv.last_total_tokens ? fmtTokens(conv.last_total_tokens) : "";

        const del = document.createElement("button");
        del.className = "conv-delete";
        del.textContent = "×";
        del.title = "Delete conversation";
        del.addEventListener("click", (e) => {
            e.stopPropagation();
            deleteConversation(conv.id);
        });

        li.appendChild(title);
        li.appendChild(tokens);
        li.appendChild(del);
        li.addEventListener("click", () => openConversation(conv.id));
        convListEl.appendChild(li);
    }
}

function renderPastMessages(messages) {
    transcript.innerHTML = "";
    for (const msg of messages) {
        if (msg.role === "user") {
            addBubble("user").textContent = msg.content;
        } else if (msg.role === "assistant") {
            if (msg.content) addBubble("assistant").textContent = msg.content;
            if (msg.tool_calls) {
                for (const tc of msg.tool_calls) {
                    addToolBlock(tc.name, tc.arguments, "calling");
                }
            }
        } else if (msg.role === "tool") {
            // Tool result rows carry only the result payload; name is on the call.
            addToolBlock("tool", msg.result, "result");
        }
    }
}

async function openConversation(id) {
    currentConversationId = id;
    const res = await fetch(`/api/conversations/${id}/messages`);
    if (!res.ok) return;
    const data = await res.json();
    renderPastMessages(data.messages);
    // If a turn is paused awaiting confirmation, re-show the approve/deny card.
    if (data.pending_confirmation) {
        renderConfirmCard(data.pending_confirmation);
    }
    updateContextGauge(data.last_total_tokens ?? 0);
    setComposerEnabled(true);
    input.focus();
    // Update active highlight without a full reload.
    for (const li of convListEl.children) {
        li.classList.toggle("active", Number(li.dataset.id) === id);
    }
}

async function newConversation() {
    const res = await fetch("/api/conversations", { method: "POST" });
    const conv = await res.json();
    await loadConversationList();
    await openConversation(conv.id);
}

async function deleteConversation(id) {
    await fetch(`/api/conversations/${id}`, { method: "DELETE" });
    if (id === currentConversationId) {
        currentConversationId = null;
        transcript.innerHTML = "";
        updateContextGauge(0);
        setComposerEnabled(false);
    }
    await loadConversationList();
}

// --- Streaming a turn ----------------------------------------------------

// Parses the `event: X\ndata: Y\n\n` SSE framing manually, since EventSource
// only supports GET and these endpoints must be POSTed to.
async function readSSE(response, handlers) {
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        let boundary;
        while ((boundary = buffer.indexOf("\n\n")) !== -1) {
            const rawEvent = buffer.slice(0, boundary);
            buffer = buffer.slice(boundary + 2);
            let eventType = "message";
            let dataLine = "";
            for (const line of rawEvent.split("\n")) {
                if (line.startsWith("event:")) eventType = line.slice(6).trim();
                else if (line.startsWith("data:")) dataLine = line.slice(5).trim();
            }
            if (!dataLine) continue;
            const handler = handlers[eventType];
            if (handler) handler(JSON.parse(dataLine));
        }
    }
}

// Builds a fresh set of stream handlers with their own segment state. Reused by
// both the chat stream and the post-confirmation resume stream.
function makeHandlers() {
    let openReasoningPre = null;
    let openAssistantBubble = null;
    const closeSegments = () => {
        openReasoningPre = null;
        openAssistantBubble = null;
    };
    return {
        reasoning_token: (data) => {
            if (!openReasoningPre) {
                openAssistantBubble = null;
                const block = document.createElement("details");
                block.className = "reasoning-block";
                const summary = document.createElement("summary");
                summary.textContent = "reasoning";
                openReasoningPre = document.createElement("pre");
                block.appendChild(summary);
                block.appendChild(openReasoningPre);
                transcript.appendChild(block);
            }
            openReasoningPre.textContent += data.text;
            transcript.scrollTop = transcript.scrollHeight;
        },
        assistant_token: (data) => {
            if (!openAssistantBubble) {
                openReasoningPre = null;
                openAssistantBubble = addBubble("assistant");
            }
            openAssistantBubble.textContent += data.text;
            transcript.scrollTop = transcript.scrollHeight;
        },
        tool_call: (data) => {
            closeSegments();
            addToolBlock(data.name, data.arguments, "calling");
        },
        tool_result: (data) => {
            closeSegments();
            addToolBlock(data.name, data.result, "result");
        },
        confirmation_required: (data) => {
            closeSegments();
            renderConfirmCard(data.calls);
        },
        final: (data) => {
            if (data.truncated_max_steps) {
                addBubble("error").textContent = "[stopped: max tool-call steps reached]";
            }
            closeSegments();
        },
        context: (data) => {
            contextMax = data.max ?? contextMax;
            updateContextGauge(data.used);
            if (contextMax && data.used >= contextMax) {
                addBubble("error").textContent =
                    "⚠ Context window full — the model may start forgetting the oldest messages. Consider starting a new chat.";
            }
        },
        error: (data) => {
            closeSegments();
            addBubble("error").textContent = "Error: " + data.message;
        },
        done: () => {
            setComposerEnabled(true);
            input.focus();
        },
    };
}

async function postAndRead(url, body) {
    const res = await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
    });
    await readSSE(res, makeHandlers());
}

// Renders the approve/deny card for a paused confirm_required action.
function renderConfirmCard(calls) {
    const card = document.createElement("div");
    card.className = "confirm-card";

    const heading = document.createElement("div");
    heading.className = "confirm-heading";
    heading.textContent = "⚠ ZyleBot wants to run an action — approve?";
    card.appendChild(heading);

    for (const c of calls) {
        const row = document.createElement("pre");
        row.className = "confirm-call";
        row.textContent = c.name + "(" + JSON.stringify(c.arguments, null, 2) + ")";
        card.appendChild(row);
    }

    const btnRow = document.createElement("div");
    btnRow.className = "confirm-buttons";
    const status = document.createElement("span");
    status.className = "confirm-status";

    const approve = document.createElement("button");
    approve.textContent = "Approve & run";
    approve.className = "approve";
    const deny = document.createElement("button");
    deny.textContent = "Deny";
    deny.className = "deny";

    const decide = async (approved) => {
        approve.disabled = true;
        deny.disabled = true;
        status.textContent = approved ? "Approved — running…" : "Denied.";
        const convId = currentConversationId;
        try {
            await postAndRead(`/api/conversations/${convId}/confirm`, { approved });
        } catch (err) {
            addBubble("error").textContent = "Connection error: " + err.message;
            setComposerEnabled(true);
        }
        await loadConversationList();
    };
    approve.addEventListener("click", () => decide(true));
    deny.addEventListener("click", () => decide(false));

    btnRow.appendChild(approve);
    btnRow.appendChild(deny);
    btnRow.appendChild(status);
    card.appendChild(btnRow);
    transcript.appendChild(card);
    transcript.scrollTop = transcript.scrollHeight;
}

form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const message = input.value.trim();
    if (!message || currentConversationId == null) return;
    const convId = currentConversationId;
    input.value = "";
    setComposerEnabled(false);
    addBubble("user").textContent = message;

    try {
        await postAndRead(`/api/conversations/${convId}/chat`, { message });
    } catch (err) {
        addBubble("error").textContent = "Connection error: " + err.message;
        setComposerEnabled(true);
    }
    // Title/token count may have changed; refresh the sidebar.
    await loadConversationList();
});

newChatBtn.addEventListener("click", newConversation);

// --- Startup -------------------------------------------------------------

async function init() {
    await refreshHealth();
    await loadConversationList();
    // Auto-open the most recent conversation, or start a fresh one.
    if (convListEl.children.length > 0) {
        openConversation(Number(convListEl.children[0].dataset.id));
    } else {
        newConversation();
    }
}

init();
