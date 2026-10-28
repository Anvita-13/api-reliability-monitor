/* API Reliability Monitor - dashboard behaviour (vanilla JS) */
(function () {
    "use strict";

    function toast(message, kind) {
        var box = document.getElementById("toast");
        if (!box) return;
        var el = document.createElement("div");
        el.className = "toast " + (kind || "");
        el.textContent = message;
        box.appendChild(el);
        setTimeout(function () { el.remove(); }, 5000);
    }

    // FastAPI returns either {detail: "text"} or {detail: [{loc, msg}, ...]}
    function errorText(data, status) {
        if (data && typeof data.detail === "string") return data.detail;
        if (data && Array.isArray(data.detail)) {
            return data.detail.map(function (d) {
                var field = (d.loc || []).filter(function (p) { return p !== "body"; }).join(".");
                return (field ? field + ": " : "") + d.msg;
            }).join("; ");
        }
        return "Request failed (HTTP " + status + ").";
    }

    function api(method, url, body) {
        var options = { method: method, credentials: "same-origin", headers: {} };
        if (body !== undefined) {
            options.headers["Content-Type"] = "application/json";
            options.body = JSON.stringify(body);
        }
        return fetch(url, options).then(function (res) {
            return res.text().then(function (text) {
                var data = null;
                try { data = text ? JSON.parse(text) : null; } catch (e) { /* not JSON */ }
                return { ok: res.ok, status: res.status, data: data };
            });
        });
    }

    // ---- login ----
    var login = document.getElementById("login-form");
    if (login) {
        login.addEventListener("submit", function (e) {
            e.preventDefault();
            var out = document.getElementById("login-error");
            out.textContent = "";
            api("POST", "/auth/login", {
                username: login.username.value.trim(),
                password: login.password.value
            }).then(function (r) {
                if (r.ok) { window.location.href = "/dashboard"; }
                else { out.textContent = errorText(r.data, r.status); }
            });
        });
    }

    var logout = document.getElementById("logout");
    if (logout) {
        logout.addEventListener("click", function () {
            api("POST", "/auth/logout").then(function () { window.location.href = "/login"; });
        });
    }

    // ---- add / edit endpoint forms ----
    function readForm(form, method) {
        var body = {};
        Array.prototype.forEach.call(form.elements, function (el) {
            if (!el.name) return;
            var value = el.value.trim();
            if (el.type === "number") {
                if (value === "") {
                    // blank "slow threshold" on an edit means: switch it off
                    if (el.name === "latency_threshold_ms" && method === "PATCH") body.clear_latency_threshold = true;
                    return;
                }
                body[el.name] = parseInt(value, 10);
            } else {
                body[el.name] = value;
            }
        });
        return body;
    }

    document.querySelectorAll("form[data-api-form]").forEach(function (form) {
        form.addEventListener("submit", function (e) {
            e.preventDefault();
            var err = form.querySelector(".form-error");
            var btn = form.querySelector("button[type=submit]");
            if (err) err.textContent = "";
            btn.disabled = true;
            var method = form.getAttribute("data-method");
            api(method, form.getAttribute("data-url"), readForm(form, method)).then(function (r) {
                btn.disabled = false;
                if (r.ok) {
                    toast(method === "POST" ? "Endpoint added." : "Changes saved.", "success");
                    if (method === "POST" && r.data && r.data.id) {
                        window.location.href = "/dashboard/endpoints/" + r.data.id;
                    } else { window.location.reload(); }
                } else if (err) { err.textContent = errorText(r.data, r.status); }
            });
        });
    });

    // ---- endpoint page actions ----
    var actions = document.getElementById("actions");
    if (actions) {
        var id = actions.getAttribute("data-endpoint");
        actions.addEventListener("click", function (e) {
            var btn = e.target.closest("button[data-action]");
            if (!btn) return;
            var action = btn.getAttribute("data-action");
            if (action === "check") {
                btn.disabled = true; btn.textContent = "Checking…";
                api("POST", "/endpoints/" + id + "/check").then(function (r) {
                    btn.disabled = false; btn.textContent = "Check now";
                    if (!r.ok) { toast(errorText(r.data, r.status), "error"); return; }
                    var c = r.data.check;
                    toast((c.success ? "Passed" : "Failed") + (c.latency_ms !== null ? " in " + c.latency_ms + " ms" : "") +
                        (c.error_message ? " — " + c.error_message : ""), c.success ? "success" : "error");
                    refreshLive();
                });
            } else if (action === "toggle") {
                var enabled = actions.getAttribute("data-enabled") === "true";
                api("PATCH", "/endpoints/" + id, { enabled: !enabled }).then(function (r) {
                    if (r.ok) window.location.reload(); else toast(errorText(r.data, r.status), "error");
                });
            } else if (action === "delete") {
                if (!window.confirm("Delete this endpoint and all of its history? This cannot be undone.")) return;
                api("DELETE", "/endpoints/" + id).then(function (r) {
                    if (r.ok) window.location.href = "/dashboard"; else toast(errorText(r.data, r.status), "error");
                });
            }
        });
    }

    // ---- export ----
    document.querySelectorAll("form[data-export-form]").forEach(function (form) {
        form.addEventListener("submit", function (e) {
            e.preventDefault();
            var err = form.querySelector(".form-error");
            err.textContent = "";
            api("POST", "/reports/export", {
                endpoint_id: parseInt(form.getAttribute("data-endpoint"), 10),
                hours: parseInt(form.hours.value, 10),
                format: form.format.value
            }).then(function (r) {
                if (!r.ok) { err.textContent = errorText(r.data, r.status); return; }
                var link = form.querySelector(".export-link");
                link.textContent = "";
                var a = document.createElement("a");
                a.href = r.data.download_url;
                a.textContent = "Download (" + r.data.rows + " checks)";
                link.appendChild(a);
            });
        });
    });

    // ---- live refresh: swap in fresh server-rendered HTML every few seconds ----
    var live = document.getElementById("live");
    function refreshLive() {
        if (!live || document.hidden) return;
        fetch(live.getAttribute("data-live-url"), { credentials: "same-origin" }).then(function (res) {
            if (res.redirected || !res.ok) return null;  // signed out / error: leave the page as is
            return res.text();
        }).then(function (html) { if (html) live.innerHTML = html; });
    }
    if (live) setInterval(refreshLive, 10000);
})();
