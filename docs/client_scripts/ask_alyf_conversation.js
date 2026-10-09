/* Client Script: Ask ALYF Conversation | View: Form | Enabled: yes.
 * Replace ALL contents of the Script field with this file, including the default
 * frappe.ui.form.on / refresh scaffold. Do not paste inside an existing refresh.
 * No custom fields are required.
 * Read-only viewer: it never saves or changes messages_json.
 */
(() => {
    const escape = (value) => String(value ?? "").replace(/[&<>"']/g, (char) => ({
        "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    }[char]));

    function parseMessages(raw) {
        if (raw == null || raw === "") return [];
        let messages = raw;
        for (let layer = 0; layer < 2 && typeof messages === "string"; layer++) {
            messages = JSON.parse(messages);
        }
        if (!Array.isArray(messages)) throw new Error("messages_json debe contener una lista de mensajes.");
        return messages.map((message) => {
            if (!message || typeof message !== "object" || Array.isArray(message)) {
                return {role: "unknown", content: String(message ?? "")};
            }
            return message;
        });
    }

    function contentText(content) {
        if (typeof content === "string") return content;
        if (Array.isArray(content)) {
            return content.map((part) => typeof part === "string" ? part
                : typeof part?.text === "string" ? part.text : JSON.stringify(part)).join("\n");
        }
        return content == null ? "" : JSON.stringify(content, null, 2);
    }

    function decodeEscapes(text) {
        // One pass only. The checkbox lets the reader preserve literal code/paths.
        return text.replace(/\\(u[0-9a-fA-F]{4}|n|r|t|\\)/g, (_, token) => {
            if (token[0] === "u") return String.fromCharCode(parseInt(token.slice(1), 16));
            return {n: "\n", r: "\r", t: "\t", "\\": "\\"}[token];
        });
    }

    function dateLabel(value) {
        // Naive timestamps are already in the source's local time: do not shift them.
        const match = String(value || "").match(/^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}:\d{2})(?::\d{2})?(?:\.\d+)?(Z|[+-]\d{2}:\d{2})?$/);
        return match ? `${match[3]}/${match[2]}/${match[1]} ${match[4]}${match[5] ? " " + match[5] : ""}` : String(value || "");
    }

    function markdown(text) {
        if (!text) return '<span class="text-muted">Sin contenido</span>';
        if (typeof frappe.markdown !== "function") return `<div style="white-space:pre-wrap">${escape(text)}</div>`;
        try {
            // The template is inert; allow formatting, never executable HTML or images.
            const template = document.createElement("template");
            template.innerHTML = frappe.markdown(escape(text));
            const allowed = new Set(["P", "BR", "HR", "H1", "H2", "H3", "H4", "H5", "H6",
                "UL", "OL", "LI", "BLOCKQUOTE", "PRE", "CODE", "EM", "STRONG", "DEL", "B", "I",
                "A", "TABLE", "THEAD", "TBODY", "TR", "TH", "TD"]);
            const walk = (node) => {
                for (const element of [...node.children]) {
                    if (!allowed.has(element.tagName)) { element.remove(); continue; }
                    const href = element.tagName === "A" ? element.getAttribute("href") || "" : "";
                    for (const attr of [...element.attributes]) element.removeAttribute(attr.name);
                    if (/^(https?:\/\/|mailto:|#|\/(?!\/))/i.test(href) && !/[\u0000-\u0020\\]/.test(href)) {
                        element.setAttribute("href", href);
                        element.setAttribute("target", "_blank");
                        element.setAttribute("rel", "noopener noreferrer");
                    }
                    walk(element);
                }
            };
            walk(template.content);
            return template.innerHTML;
        } catch (_) {
            return `<div style="white-space:pre-wrap">${escape(text)}</div>`;
        }
    }

    function messageHTML(message, interpret) {
        const role = String(message.role || "unknown");
        const labels = {user: "Usuario", assistant: "Asistente", system: "Sistema", tool: "Herramienta"};
        const side = role === "user" ? "user" : "assistant";
        let text = contentText(message.content);
        if (interpret) text = decodeEscapes(text);
        const calls = Array.isArray(message.metadata?.tool_calls) ? message.metadata.tool_calls : [];
        const tools = calls.length ? `<details class="alyf-chat-tools"><summary>Herramientas consultadas (${calls.length})</summary>
            <ul>${calls.map((call) => `<li>${escape(call?.label || call?.name || "Herramienta")}
                ${call?.status ? ` · ${escape(call.status)}` : ""}</li>`).join("")}</ul></details>` : "";
        return `<article class="alyf-chat-message alyf-chat-${side}">
            <header><strong>${escape(labels[role] || role)}</strong><time>${escape(dateLabel(message.created_at))}</time></header>
            <div class="alyf-chat-content">${markdown(text)}</div>${tools}</article>`;
    }

    function showConversation(frm) {
        let messages;
        try {
            messages = parseMessages(frm.doc.messages_json);
        } catch (error) {
            frappe.msgprint({title: "No se pudo abrir la conversación", indicator: "red",
                message: `Revise el formato de messages_json.<br>${escape(error.message)}`});
            return;
        }
        const dialog = new frappe.ui.Dialog({
            title: "Conversación", size: "extra-large",
            fields: [{fieldtype: "HTML", fieldname: "conversation"}],
            primary_action_label: "Ir al último mensaje",
            primary_action() {
                const scroller = dialog.fields_dict.conversation.$wrapper.find(".alyf-chat-messages")[0];
                if (scroller) scroller.scrollTop = scroller.scrollHeight;
            },
            secondary_action_label: "Cerrar",
            secondary_action() { dialog.hide(); },
        });
        const wrapper = dialog.fields_dict.conversation.$wrapper;
        wrapper.html(`<style>
            .alyf-chat-view .alyf-chat-title {font-size:16px;font-weight:600;margin-bottom:6px;overflow-wrap:anywhere}
            .alyf-chat-view .alyf-chat-toolbar {display:flex;flex-wrap:wrap;gap:12px;justify-content:space-between;margin-bottom:14px;color:var(--text-muted,#68737d)}
            .alyf-chat-view .alyf-chat-toolbar label {margin:0;cursor:pointer}
            .alyf-chat-view .alyf-chat-messages {max-height:65vh;overflow:auto;padding:16px;border-radius:12px;background:var(--control-bg,#f4f5f7)}
            .alyf-chat-view .alyf-chat-message {max-width:90%;padding:14px 18px;margin:0 0 16px;border:1px solid var(--border-color,#dfe3e8);border-radius:14px;background:var(--card-bg,#fff);color:var(--text-color,#202733)}
            .alyf-chat-view .alyf-chat-user {margin-left:auto;background:var(--blue-50,#eef6ff);border-color:var(--blue-200,#bad6f4);color:var(--gray-900,#182333)}
            .alyf-chat-view .alyf-chat-message header {display:flex;flex-wrap:wrap;gap:8px;justify-content:space-between;margin-bottom:10px;font-size:12px}
            .alyf-chat-view time {opacity:.7}
            .alyf-chat-view .alyf-chat-content {line-height:1.6;overflow-wrap:anywhere;overflow-x:auto}
            .alyf-chat-view .alyf-chat-content p:last-child {margin-bottom:0}
            .alyf-chat-view .alyf-chat-content h1,.alyf-chat-view .alyf-chat-content h2,.alyf-chat-view .alyf-chat-content h3 {font-size:17px;margin:16px 0 8px}
            .alyf-chat-view pre {white-space:pre;overflow:auto;padding:12px;border-radius:8px}
            .alyf-chat-view blockquote {border-left:3px solid var(--border-color,#ccd4dc);padding-left:12px}
            .alyf-chat-view table {border-collapse:collapse;width:100%}
            .alyf-chat-view th,.alyf-chat-view td {padding:6px;border:1px solid var(--border-color,#ccd4dc)}
            .alyf-chat-view .alyf-chat-tools {margin-top:12px;font-size:12px;opacity:.8}
            .alyf-chat-view summary {cursor:pointer}
            @media(max-width:640px) {.alyf-chat-view .alyf-chat-message {max-width:100%;padding:12px}.alyf-chat-view .alyf-chat-messages {padding:8px}}
        </style><section class="alyf-chat-view">
            <div class="alyf-chat-title">${escape(frm.doc.title || frm.doc.name || "Conversación")}</div>
            <div class="alyf-chat-toolbar"><span>${messages.length} mensajes</span>
                <label><input type="checkbox" class="alyf-chat-decode"> Interpretar escapes (\\n, \\u…)</label></div>
            <div class="alyf-chat-messages" aria-label="Historial de conversación"></div>
        </section>`);
        const checkbox = wrapper.find(".alyf-chat-decode");
        checkbox.prop("checked", messages.some((message) => /\\u[0-9a-fA-F]{4}|\\n/.test(contentText(message.content))));
        const render = () => wrapper.find(".alyf-chat-messages").html(messages.length
            ? messages.map((message) => messageHTML(message, checkbox.prop("checked"))).join("")
            : '<p class="text-muted">Esta conversación todavía no tiene mensajes.</p>');
        checkbox.on("change", render);
        render();
        dialog.show();
        dialog.$wrapper.one("hidden.bs.modal", () => dialog.$wrapper.remove());
    }

    frappe.ui.form.on("Ask ALYF Conversation", {
        refresh(frm) {
            frm.add_custom_button("Ver conversación", () => showConversation(frm));
        },
    });
})();
