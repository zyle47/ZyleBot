const transcript = document.getElementById("transcript");
const form = document.getElementById("chat-form");
const input = document.getElementById("message-input");
const sendBtn = form.querySelector("button[type=submit]");
const micBtn = document.getElementById("mic-btn");
const attachBtn = document.getElementById("attach-btn");
const fileInput = document.getElementById("file-input");
const attachmentsEl = document.getElementById("attachments");
const statusEl = document.getElementById("status");
const startServerBtn = document.getElementById("start-server-btn");
const ctxFill = document.getElementById("context-bar-fill");
const ctxText = document.getElementById("context-text");
const convListEl = document.getElementById("conversation-list");
const newChatBtn = document.getElementById("new-chat");
const modelSelect = document.getElementById("model-select");

let contextMax = null;
let contextUsed = 0;
let currentConversationId = null;
// Base64 data URLs staged for the next send (from paste or the 📎 button).
let pendingImages = [];

// --- Context gauge -------------------------------------------------------

function fmtTokens(n) {
    if (n == null) return "?";
    return n >= 1000 ? (n / 1000).toFixed(n >= 10000 ? 0 : 1) + "k" : String(n);
}

function updateContextGauge(used) {
    contextUsed = used ?? 0;
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

function modelLabel(m) {
    const name = m.alias || m.id;
    return name + (m.state === "loaded" ? " ●" : "");
}

async function loadModels() {
    try {
        const res = await fetch("/api/models");
        const data = await res.json();
        modelSelect.innerHTML = "";
        for (const m of data.models) {
            const opt = document.createElement("option");
            opt.value = m.id;
            opt.textContent = modelLabel(m);
            if (m.id === data.active) opt.selected = true;
            modelSelect.appendChild(opt);
        }
    } catch (err) {
        /* leave the dropdown empty if LM Studio is unreachable */
    }
}

modelSelect.addEventListener("change", async () => {
    const model = modelSelect.value;
    const label = modelSelect.options[modelSelect.selectedIndex].textContent.replace(" ●", "");
    // Switching now loads the model via LM Studio (unload old + load new), which
    // blocks ~15-30s. Lock the UI and show progress.
    modelSelect.disabled = true;
    setComposerEnabled(false);
    statusEl.textContent = `loading ${label}… (this takes ~15-30s)`;
    statusEl.className = "status";
    try {
        const res = await fetch("/api/model", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ model }),
        });
        if (!res.ok) {
            const err = await res.json().catch(() => ({}));
            addBubble("error").textContent =
                "Failed to switch model: " + (err.detail || res.statusText);
        }
    } catch (err) {
        addBubble("error").textContent = "Failed to switch model: " + err.message;
    }
    modelSelect.disabled = false;
    setComposerEnabled(true);
    await refreshHealth();
    await loadModels();
});

async function refreshHealth() {
    try {
        const res = await fetch("/api/health");
        const data = await res.json();
        statusEl.textContent = data.lmstudio_reachable
            ? `connected (${data.model_alias || data.model})`
            : "LM Studio unreachable";
        statusEl.className = "status " + (data.lmstudio_reachable ? "ok" : "bad");
        startServerBtn.hidden = data.lmstudio_reachable;
        contextMax = data.context_length ?? null;
        // Redraw with the last-known usage — this runs on a timer now, and
        // resetting to 0 would wipe the gauge mid-conversation.
        updateContextGauge(contextUsed);
    } catch (err) {
        // ZyleBot's own backend is down — the button can't help here.
        statusEl.textContent = "health check failed";
        statusEl.className = "status bad";
        startServerBtn.hidden = true;
    }
}

startServerBtn.addEventListener("click", async () => {
    startServerBtn.disabled = true;
    statusEl.textContent = "starting LM Studio server…";
    statusEl.className = "status";
    try {
        const res = await fetch("/api/server/start", { method: "POST" });
        if (!res.ok) {
            const err = await res.json().catch(() => ({}));
            addBubble("error").textContent =
                "Failed to start LM Studio server: " + (err.detail || res.statusText);
        }
    } catch (err) {
        addBubble("error").textContent = "Failed to start LM Studio server: " + err.message;
    }
    startServerBtn.disabled = false;
    // Reflect the new state: status goes green, dropdown fills with models.
    await refreshHealth();
    await loadModels();
});

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
    micBtn.disabled = !enabled;
    attachBtn.disabled = !enabled;
}

// Fills a user bubble with optional image thumbnails followed by the text.
function fillUserBubble(bubble, text, images) {
    if (images && images.length) {
        const strip = document.createElement("div");
        strip.className = "bubble-images";
        for (const url of images) {
            const im = document.createElement("img");
            im.src = url;
            im.className = "bubble-img";
            im.title = "Open full size";
            im.addEventListener("click", () => window.open(url, "_blank"));
            strip.appendChild(im);
        }
        bubble.appendChild(strip);
    }
    if (text) {
        const t = document.createElement("div");
        t.textContent = text;
        bubble.appendChild(t);
    }
    transcript.scrollTop = transcript.scrollHeight;
}

// --- Voice input (mic -> /api/transcribe -> message input) --------------

let mediaRecorder = null;
let audioChunks = [];

function isRecording() {
    return mediaRecorder != null && mediaRecorder.state === "recording";
}

async function startRecording() {
    let stream;
    try {
        stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch (err) {
        addBubble("error").textContent = "Microphone access denied or unavailable: " + err.message;
        return;
    }
    audioChunks = [];
    mediaRecorder = new MediaRecorder(stream);
    mediaRecorder.addEventListener("dataavailable", (e) => {
        if (e.data.size > 0) audioChunks.push(e.data);
    });
    mediaRecorder.addEventListener("stop", async () => {
        stream.getTracks().forEach((track) => track.stop());
        micBtn.classList.remove("recording");
        const blob = new Blob(audioChunks, { type: mediaRecorder.mimeType || "audio/webm" });
        await transcribeAndFill(blob);
    });
    mediaRecorder.start();
    micBtn.classList.add("recording");
    micBtn.title = "Recording... click to stop";
}

function stopRecording() {
    if (isRecording()) mediaRecorder.stop();
}

async function transcribeAndFill(blob) {
    micBtn.disabled = true;
    micBtn.title = "Transcribing...";
    const formData = new FormData();
    formData.append("audio", blob, "speech.webm");
    try {
        const res = await fetch("/api/transcribe", { method: "POST", body: formData });
        if (!res.ok) {
            const err = await res.json().catch(() => ({}));
            addBubble("error").textContent = "Transcription failed: " + (err.detail || res.statusText);
        } else {
            const data = await res.json();
            if (data.text) {
                input.value = input.value ? input.value + " " + data.text : data.text;
            }
        }
    } catch (err) {
        addBubble("error").textContent = "Transcription request failed: " + err.message;
    }
    micBtn.disabled = false;
    micBtn.title = "Speak your message";
    input.focus();
}

micBtn.addEventListener("click", () => {
    if (isRecording()) {
        stopRecording();
    } else {
        startRecording();
    }
});

// --- Image attachments (paste / 📎 -> preview -> send) ------------------

// Downscale client-side so a pasted screenshot doesn't bloat the request, the
// SQLite row, or the model's (small) context. JPEG keeps the payload compact.
function downscaleImage(file, maxDim = 1024, quality = 0.85) {
    return new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onerror = () => reject(new Error("could not read file"));
        reader.onload = () => {
            const img = new Image();
            img.onerror = () => reject(new Error("could not decode image"));
            img.onload = () => {
                let { width, height } = img;
                const scale = Math.min(1, maxDim / Math.max(width, height));
                width = Math.round(width * scale);
                height = Math.round(height * scale);
                const canvas = document.createElement("canvas");
                canvas.width = width;
                canvas.height = height;
                canvas.getContext("2d").drawImage(img, 0, 0, width, height);
                resolve(canvas.toDataURL("image/jpeg", quality));
            };
            img.src = reader.result;
        };
        reader.readAsDataURL(file);
    });
}

async function addImageFile(file) {
    if (!file || !file.type.startsWith("image/")) return;
    try {
        pendingImages.push(await downscaleImage(file));
        renderAttachments();
    } catch (err) {
        addBubble("error").textContent = "Could not attach image: " + err.message;
    }
}

function renderAttachments() {
    attachmentsEl.innerHTML = "";
    attachmentsEl.classList.toggle("has-items", pendingImages.length > 0);
    pendingImages.forEach((url, i) => {
        const wrap = document.createElement("div");
        wrap.className = "attachment";
        const im = document.createElement("img");
        im.src = url;
        const rm = document.createElement("button");
        rm.type = "button";
        rm.className = "attachment-remove";
        rm.textContent = "×";
        rm.title = "Remove";
        rm.addEventListener("click", () => {
            pendingImages.splice(i, 1);
            renderAttachments();
        });
        wrap.appendChild(im);
        wrap.appendChild(rm);
        attachmentsEl.appendChild(wrap);
    });
}

// Paste an image straight into the composer (like pasting into a chat with me).
input.addEventListener("paste", (e) => {
    const items = (e.clipboardData && e.clipboardData.items) || [];
    for (const item of items) {
        if (item.kind === "file" && item.type.startsWith("image/")) {
            e.preventDefault();
            addImageFile(item.getAsFile());
        }
    }
});

attachBtn.addEventListener("click", () => fileInput.click());
fileInput.addEventListener("change", async () => {
    for (const file of fileInput.files) await addImageFile(file);
    fileInput.value = "";
});

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
            fillUserBubble(addBubble("user"), msg.content, msg.images);
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
            // The failure may be LM Studio having gone down since page load —
            // re-check so the status (and the Start-server button) match reality.
            refreshHealth();
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
    const images = pendingImages;
    if ((!message && images.length === 0) || currentConversationId == null) return;
    const convId = currentConversationId;
    input.value = "";
    pendingImages = [];
    renderAttachments();
    setComposerEnabled(false);
    fillUserBubble(addBubble("user"), message, images);

    try {
        await postAndRead(`/api/conversations/${convId}/chat`, { message, images });
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
    await loadModels();
    await loadConversationList();
    // Auto-open the most recent conversation, or start a fresh one.
    if (convListEl.children.length > 0) {
        openConversation(Number(convListEl.children[0].dataset.id));
    } else {
        newConversation();
    }
}

init();

// Poll so the header notices LM Studio going down (or coming back) without a
// page refresh. Skipped while a model switch or server start is in flight —
// their progress text owns the status line (the disabled control is the flag).
setInterval(() => {
    if (modelSelect.disabled || startServerBtn.disabled) return;
    refreshHealth();
}, 10000);
