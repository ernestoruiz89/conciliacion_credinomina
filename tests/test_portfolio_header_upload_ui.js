const assert = require("node:assert/strict"), fs = require("node:fs"), vm = require("node:vm");
let handlers, rpc, messages = [], calls = [], savedHeader;
const context = vm.createContext({__: (text, args = []) => text.replace(/\{(\d+)\}/g, (_, i) => args[i]), frappe: {
    ui: {form: {on: (_, events) => {handlers = events;}}},
    call(args) {calls.push(args); return rpc(args);}, msgprint(message) {messages.push(message);},
}});
vm.runInContext(fs.readFileSync("credinomina_reconciliation/conciliacion_credinomina/doctype/cn_credit_portfolio_snapshot/cn_credit_portfolio_snapshot.js", "utf8"), context);
const rows = Array(10000).fill({credit_number: "123-1", raw_data: "x".repeat(3000)});
function form() {
    const frm = {doc: {name: "CUT", source_file: "/private/files/original.xlsx", file_hash: "IMPORTED",
        notes: "", disabled: 0, modified: "OLD", modified_by: "USER", rows, row_count: rows.length},
        is_new: () => false, is_dirty() {return !!this.doc.__unsaved;},
        attachments: {files: ["/private/files/original.xlsx"], update_attachment(file) {this.files.push(file.file_url);}},
        save() {throw new Error("Saving the full imported document exceeds the request limit");},
        refresh_field() {}, refresh_header() {}, reload_doc: async () => {},
        add_custom_button(_label, action) {this.importButton = action;},
    };
    const control = {clear_attachment() {throw new Error("Do not delete the previous file");}, on_attach_click() {},
        on_upload_complete() {throw new Error("Native callback saves all portfolio rows");},
        async parse_validate_and_set_in_model(url) {frm.doc.source_file = url; frm.doc.__unsaved = 1;},
    };
    frm.fields_dict = {source_file: control};
    handlers.refresh(frm);
    return frm;
}
const attachment = {file_url: "/private/files/corrected.xlsx", file_size: 3111765};
function headerResponse(args) {
    savedHeader = {...args.args, modified: "NEW", modified_by: "USER"};
    return {message: savedHeader};
}
const tick = () => new Promise(resolve => setImmediate(resolve));
(async () => {
    let frm = form();
    assert.ok(Buffer.byteLength(JSON.stringify(frm.doc)) > 25 * 1024 * 1024);
    rpc = async args => headerResponse(args);
    await frm.fields_dict.source_file.on_upload_complete(attachment);
    assert.equal(calls.length, 1);
    assert.ok(calls[0].method.endsWith(".save_portfolio_header"));
    assert.ok(Buffer.byteLength(JSON.stringify(calls[0].args)) < 512, "Upload callback must send only the header");
    assert.equal(calls[0].args.source_file, attachment.file_url);
    assert.equal(calls[0].args.modified, "OLD");
    assert.equal(frm.doc.modified, "NEW");
    assert.equal(frm.doc.__unsaved, 0);
    assert.equal(frm.doc.rows, rows);
    assert.equal(frm.doc.file_hash, "IMPORTED");
    assert.deepEqual(frm.attachments.files, ["/private/files/original.xlsx", attachment.file_url]);

    // Import waits for the compact save already in progress, without starting
    // a second full save or importing against the previous attachment.
    calls = []; messages = []; frm = form();
    let finish;
    rpc = args => args.method.endsWith(".save_portfolio_header")
        ? new Promise(resolve => {finish = () => resolve(headerResponse(args));})
        : Promise.resolve({message: {snapshot_name: "CUT", row_count: rows.length}});
    const uploading = frm.fields_dict.source_file.on_upload_complete(attachment);
    await tick();
    const importing = frm.importButton();
    await tick();
    assert.equal(calls.length, 1);
    finish(); await uploading; await importing;
    assert.equal(calls.length, 2);
    assert.ok(calls[1].method.endsWith(".import_portfolio_snapshot"));

    // A header error preserves pending edits and aborts an import waiting on it.
    calls = []; messages = []; frm = form();
    let fail;
    rpc = () => new Promise((_resolve, reject) => {fail = reject;});
    const failedUpload = frm.fields_dict.source_file.on_upload_complete(attachment);
    await tick();
    const abortedImport = frm.importButton();
    await tick(); fail(new Error("stale document"));
    await failedUpload; await abortedImport;
    assert.equal(calls.length, 1);
    assert.equal(frm.doc.__unsaved, 1);
    assert.equal(frm.doc.modified, "OLD");
    assert.equal(frm.doc.rows, rows);

    // Notes edited during the save remain pending and are sent by the import
    // button in another compact request with the fresh modification timestamp.
    calls = []; frm = form();
    rpc = args => new Promise(resolve => {finish = () => resolve(headerResponse(args));});
    const pending = frm.fields_dict.source_file.on_upload_complete(attachment);
    await tick(); frm.doc.notes = "Nota nueva"; finish(); await pending;
    assert.equal(frm.doc.notes, "Nota nueva");
    assert.equal(frm.doc.__unsaved, 1);
    rpc = async args => args.method.endsWith(".save_portfolio_header") ? headerResponse(args)
        : {message: {snapshot_name: "CUT", row_count: rows.length}};
    await frm.importButton();
    assert.equal(calls[1].args.notes, "Nota nueva");
    assert.equal(calls[1].args.modified, "NEW");
    assert.ok(Buffer.byteLength(JSON.stringify(calls[1].args)) < 512);
    assert.ok(calls[2].method.endsWith(".import_portfolio_snapshot"));
    console.log("OK: a >25 MB portfolio and 3,111,765-byte upload use compact header saves; imports wait and failed/concurrent edits are preserved.");
})().catch(error => {console.error(error); process.exitCode = 1;});
