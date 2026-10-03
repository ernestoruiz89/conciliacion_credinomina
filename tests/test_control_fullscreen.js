const assert = require("node:assert/strict");
const fs = require("node:fs"), path = require("node:path"), vm = require("node:vm");
const source = fs.readFileSync(path.join(__dirname,
    "../credinomina_reconciliation/conciliacion_credinomina/page/control_credinomina/control_credinomina.js"), "utf8");

function setup() {
    const listeners = {}, lifecycle = {}, changes = [], classes = () => new Set();
    let modalVisible = false, nativeRequests = 0, nativeExits = 0, focused = false;
    const button = {isConnected: true, focus() {focused = true;}, getClientRects() {return [1];}};
    const home = {};
    const body = {classList: classes(), appendChild(node) {node.parent = body;}};
    for (const set of [body.classList]) set.remove = set.delete.bind(set);
    const root = {parent: home, classList: classes(), scrollLeft: 420,
        before(marker) {marker.parent = this.parent;},
        querySelector() {return button;}, querySelectorAll() {return [button];}, contains() {return true;}};
    root.classList.remove = root.classList.delete.bind(root.classList);
    const doc = {body, activeElement: button, fullscreenElement: null,
        createComment() {return {replaceWith(node) {node.parent = this.parent;}};},
        addEventListener(name, fn) {listeners[name] = fn;}, removeEventListener(name) {delete listeners[name];},
        querySelectorAll() {return modalVisible ? [{getClientRects() {return [1];}}] : [];},
        documentElement: {async requestFullscreen() {nativeRequests++; doc.fullscreenElement = this;}},
        async exitFullscreen() {nativeExits++; doc.fullscreenElement = null;},
    };
    const context = vm.createContext({document: doc, frappe: {pages: {"control-credinomina": {}}},
        $: () => ({on(name, fn) {lifecycle[name] = fn;}, off(name) {delete lifecycle[name];}})});
    vm.runInContext(source.replace(/\}\)\(\);\s*$/, "globalThis.makeFullscreen = createCalendarFullscreen; })();"), context);
    const controller = context.makeFullscreen(root, {}, value => changes.push(value));
    return {root, body, home, doc, controller, listeners, lifecycle, changes,
        modal(value) {modalVisible = value;}, get requests() {return nativeRequests;},
        get exits() {return nativeExits;}, get focused() {return focused;}};
}
const escape = () => ({key: "Escape", prevented: false, preventDefault() {this.prevented = true;}, stopPropagation() {}});
(async () => {
    const s = setup();
    await s.controller.enter();
    assert.equal(s.root.parent, s.body, "Portal avoids transformed page ancestors");
    assert.equal(s.doc.fullscreenElement, s.doc.documentElement, "Body dialogs must stay inside the fullscreen element");
    assert.equal(s.root.scrollLeft, 420, "Preserve calendar DOM and scroll position");
    assert.equal(s.focused, true);
    assert.deepEqual(s.changes, [true]);
    s.modal(true);
    const modalEscape = escape(); s.listeners.keydown(modalEscape);
    assert.equal(s.controller.active, true, "Escape belongs to the modal while it is open");
    assert.equal(modalEscape.prevented, false);
    s.modal(false);
    const calendarEscape = escape(); s.listeners.keydown(calendarEscape);
    assert.equal(calendarEscape.prevented, true);
    assert.equal(s.controller.active, false);
    assert.equal(s.root.parent, s.home);
    assert.equal(s.body.classList.size, 0);
    assert.equal(s.root.classList.size, 0);
    assert.equal(s.exits, 1);
    assert.deepEqual(Object.keys(s.listeners), []);
    assert.deepEqual(Object.keys(s.lifecycle), []);
    await s.controller.enter();
    s.doc.fullscreenElement = null;
    s.listeners.fullscreenchange();
    assert.equal(s.root.parent, s.home, "Browser Escape also restores the layout");
    await s.controller.enter();
    await s.lifecycle["hide.cnCalendarFullscreen"]();
    assert.equal(s.root.parent, s.home, "Leaving the page cannot leave a full-screen overlay behind");
    const fallback = setup();
    fallback.doc.documentElement.requestFullscreen = async () => {throw new Error("Denied");};
    await fallback.controller.enter();
    assert.equal(fallback.controller.active, true);
    fallback.listeners.keydown(escape());
    assert.equal(fallback.root.parent, fallback.home);
    const race = setup(); let finish;
    race.doc.documentElement.requestFullscreen = () => new Promise(resolve => {finish = () => {
        race.doc.fullscreenElement = race.doc.documentElement; resolve();
    };});
    const entering = race.controller.enter();
    await race.controller.exit(); finish(); await entering;
    assert.equal(race.doc.fullscreenElement, null, "A late browser approval cannot reopen fullscreen after exit");
    assert.equal(race.root.parent, race.home);
    console.log("OK: document fullscreen, modal Escape, viewport fallback, focus, route cleanup, scroll and async exit.");
})().catch(error => {console.error(error); process.exitCode = 1;});
