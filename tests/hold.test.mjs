import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import {test} from "node:test";
import {runInNewContext} from "node:vm";

function heldControl() {
    const documentEvents = new Map();
    const windowEvents = new Map();
    const requests = [];
    const timers = new Map();
    const button = {dataset: {hold: "jog-forward"}, disabled: false,
        focus() {}, setPointerCapture(id) { this.capturedPointer = id; }};
    button.closest = () => button;
    runInNewContext(readFileSync(new URL("../src/k10cr2/assets/hold.js", import.meta.url), "utf8"), {
        document: {addEventListener: (name, fn) => documentEvents.set(name, fn)},
        window: {addEventListener: (name, fn) => windowEvents.set(name, fn)},
        crypto: {randomUUID: () => "hold-token"},
        fetch: async (_url, options) => {
            requests.push(JSON.parse(options.body));
            return {json: async () => ({message: "ok"})};
        },
        setInterval: fn => { timers.set(1, fn); return 1; },
        clearInterval: id => timers.delete(id),
    });
    return {documentEvents, windowEvents, requests, timers, button};
}

test("leaving a held control releases once and stops heartbeats", async () => {
    const {documentEvents, windowEvents, requests, timers, button} = heldControl();
    documentEvents.get("keydown")({key: " ", target: button, preventDefault() {}});
    timers.get(1)();
    // Focus on some unrelated element must not release the active control.
    documentEvents.get("focusout")({target: {}});
    assert.equal(requests.length, 2);
    documentEvents.get("focusout")({target: button});
    documentEvents.get("keyup")({key: " "});
    windowEvents.get("blur")();
    await Promise.resolve();
    assert.deepEqual(requests.map(r => r.action), ["start", "heartbeat", "release"]);
    assert.equal(requests[2].reason, "focus-out");
    assert.equal(requests[2].token, requests[0].token);
    assert.equal(timers.size, 0);
});

test("losing the active pointer capture releases only that pointer hold", () => {
    const {documentEvents, requests, timers, button} = heldControl();
    documentEvents.get("pointerdown")({button: 0, pointerId: 7, target: button, preventDefault() {}});
    assert.equal(button.capturedPointer, 7);
    documentEvents.get("lostpointercapture")?.({pointerId: 8, target: button});
    documentEvents.get("lostpointercapture")?.({pointerId: 7, target: {}});
    assert.equal(requests.length, 1);
    documentEvents.get("lostpointercapture")?.({pointerId: 7, target: button});
    assert.deepEqual(requests.map(r => r.action), ["start", "release"]);
    documentEvents.get("pointerup")({pointerId: 7});
    assert.deepEqual(requests.map(r => r.action), ["start", "release"]);
    assert.equal(requests[1].reason, "capture-lost");
    assert.equal(timers.size, 0);
    // A delayed capture event must not release a subsequent keyboard hold.
    documentEvents.get("keydown")({key: " ", target: button, preventDefault() {}});
    documentEvents.get("lostpointercapture")?.({pointerId: 7, target: button});
    assert.equal(requests.length, 3);
    documentEvents.get("keyup")({key: " "});
});
