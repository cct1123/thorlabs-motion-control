/* A held control is a short lease, never a queued or latched motion command. */
(() => {
    let active = null;
    function message(text) {
        if (window.dash_clientside?.set_props) {
            window.dash_clientside.set_props("request-result", {children: text});
        }
    }
    async function send(action, current) {
        try {
            const response = await fetch("/api/hold", {
                method: "POST", headers: {"Content-Type": "application/json"}, keepalive: true,
                body: JSON.stringify({action, token: current.token,
                    command: current.command, index: current.index, reason: current.reason})
            });
            const result = await response.json();
            if (action === "start") message(result.message);
        } catch (_) {
            message("Control connection lost. The owner will stop when the heartbeat expires.");
        }
    }
    function release(reason) {
        if (!active) return;
        const current = active;
        active = null;
        current.reason = reason;
        clearInterval(current.timer);
        void send("release", current);
    }
    function start(button) {
        if (active || button.disabled) return;
        const current = {button, token: crypto.randomUUID(), command: button.dataset.hold,
            index: Number(button.dataset.index || 0)};
        active = current;
        void send("start", current);
        current.timer = setInterval(() => void send("heartbeat", current), 200);
    }
    document.addEventListener("pointerdown", event => {
        const button = event.target.closest("button[data-hold]");
        if (!button || event.button !== 0) return;
        event.preventDefault();
        button.focus();
        button.setPointerCapture(event.pointerId);
        start(button);
    });
    document.addEventListener("pointerup", () => release("pointer-up"));
    document.addEventListener("pointercancel", () => release("pointer-cancel"));
    document.addEventListener("keydown", event => {
        if (event.key === "Escape") { release("escape"); return; }
        if (event.key !== " " && event.key !== "Enter") return;
        const button = event.target.closest("button[data-hold]");
        if (!button) return;
        event.preventDefault();
        if (!event.repeat) start(button);
    });
    document.addEventListener("keyup", event => {
        if (event.key === " " || event.key === "Enter") release("key-up");
    });
    document.addEventListener("focusout", event => {
        if (event.target === active?.button) release("focus-out");
    });
    document.addEventListener("visibilitychange", () => { if (document.hidden) release("hidden"); });
    window.addEventListener("blur", () => release("window-blur"));
    window.addEventListener("pagehide", () => release("page-hide"));
})();
