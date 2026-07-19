(() => {
    "use strict";

    const frame = document.getElementById("style-lab-frame");
    const status = document.getElementById("style-lab-status");
    const updated = document.getElementById("style-lab-updated");
    if (!frame || !status || !updated) return;

    const frameMarkup = `<!doctype html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        * { box-sizing: border-box; }
        html, body { min-height: 100%; }
        body { margin: 0; font-family: Inter, Segoe UI, sans-serif; }
        button { font: inherit; }
    </style>
    <style id="style-lab-user-css"></style>
</head>
<body>
    <main class="lab-stage">
        <div class="lab-orb lab-orb--one" aria-hidden="true"></div>
        <div class="lab-orb lab-orb--two" aria-hidden="true"></div>
        <article class="lab-card">
            <div class="lab-card__topline">
                <span class="lab-badge">LIVE SYSTEM</span>
                <span class="lab-id">NODE // 047</span>
            </div>
            <p class="lab-eyebrow">ISOLATED STYLE SURFACE</p>
            <h1>Design without fear.</h1>
            <p class="lab-copy">
                This component is real HTML inside a separate document. ZyleBot may change its colors,
                spacing, type, borders, animation, and layout—without reaching the chat interface.
            </p>
            <div class="lab-stats">
                <div><strong>01</strong><span>Fixed target</span></div>
                <div><strong>1s</strong><span>Live refresh</span></div>
                <div><strong>100%</strong><span>Contained</span></div>
            </div>
            <div class="lab-actions">
                <button class="lab-button lab-button--primary" type="button">Primary action</button>
                <button class="lab-button lab-button--secondary" type="button">Secondary</button>
            </div>
        </article>
    </main>
</body>
</html>`;

    let lastCss = null;
    let loading = false;

    function setUpdated(label, value) {
        const heading = document.createElement("b");
        heading.textContent = label;
        updated.replaceChildren(heading, document.createTextNode(` ${value}`));
    }

    function initializeFrame() {
        frame.srcdoc = frameMarkup;
    }

    function applyCss(css) {
        const style = frame.contentDocument?.getElementById("style-lab-user-css");
        if (!style) return false;
        style.textContent = css;
        return true;
    }

    async function refreshCss() {
        if (loading) return;
        loading = true;
        try {
            const response = await fetch("/static/style-lab.css", { cache: "no-store" });
            if (!response.ok) throw new Error(`HTTP ${response.status}`);
            const css = await response.text();
            if (css !== lastCss) {
                if (!applyCss(css)) return;
                const firstLoad = lastCss === null;
                lastCss = css;
                const now = new Date().toLocaleTimeString();
                setUpdated("UPDATED", now);
                status.textContent = firstLoad ? "WATCHING" : "CHANGE APPLIED";
                window.setTimeout(() => {
                    if (status.textContent === "CHANGE APPLIED") status.textContent = "WATCHING";
                }, 1600);
            } else {
                status.textContent = "WATCHING";
            }
        } catch (error) {
            status.textContent = "RETRYING";
            setUpdated("STATUS", error.message);
        } finally {
            loading = false;
        }
    }

    frame.addEventListener("load", refreshCss);
    document.addEventListener("visibilitychange", () => {
        if (!document.hidden) refreshCss();
    });
    initializeFrame();
    window.setInterval(refreshCss, 1000);
})();
