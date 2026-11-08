"use strict";
// Accounts in the page: who is signed in, the account menu, changing your password, the Users page
// (administrators) and the Share dialog. Loaded before app.js; it uses h(), api(), toast(), attempt() and
// dialog() from there, only when called.

const ME = { username: "", role: "user", must_change: false };
const isAdmin = () => ME.role === "admin";

async function loadMe() {
  Object.assign(ME, await api("/auth/me"));
  document.querySelectorAll("[data-admin]").forEach((el) => { el.hidden = !isAdmin(); });
  const box = $("#account");
  if (!box) return;
  box.replaceChildren(
    h("div", { class: "account-name" }, h("strong", {}, ME.username), isAdmin() ? h("span", { class: "tag amber", style: { marginLeft: "6px" } }, "admin") : null),
    h("div", { class: "row", style: { gap: "6px", marginTop: "6px" } },
      h("button", { class: "btn sm ghost", type: "button", onclick: changePasswordDialog }, "Password"),
      h("button", { class: "btn sm ghost", type: "button", onclick: signOut }, "Sign out")));
}

async function signOut() {
  try { await api("/auth/logout", { body: {} }); } catch (_) { /* leaving anyway */ }
  location.replace("/");
}

/** A small form in the dialog layer: fields = [{ id, label, type, value, hint, options }]; resolves with
 *  {id: value} or null. */
function formDialog({ title, text, fields, ok = "OK", danger = false, validate }) {
  return new Promise((resolve) => {
    const layer = h("div", { class: "modal dialog-layer" });
    const inputs = {};
    const msg = h("div", { class: "err-text small", role: "alert", style: { minHeight: "18px", marginTop: "8px" } });
    const rows = fields.map((f) => {
      let el;
      if (f.type === "select") {
        el = h("select", { id: "fd-" + f.id }, f.options.map((o) => h("option", { value: o.value, selected: o.value === f.value }, o.label)));
      } else if (f.type === "checkbox") {
        el = h("input", { id: "fd-" + f.id, type: "checkbox", checked: !!f.value });
      } else {
        el = h("input", { id: "fd-" + f.id, type: f.type || "text", value: f.value || "", autocomplete: f.autocomplete || "off", spellcheck: false, autocapitalize: "none" });
      }
      inputs[f.id] = el;
      return f.type === "checkbox"
        ? h("div", { class: "field" }, h("label", { class: "row", style: { gap: "8px" } }, el, f.label), f.hint ? h("div", { class: "hint" }, f.hint) : null)
        : h("div", { class: "field" }, h("label", { for: "fd-" + f.id }, f.label), el, f.hint ? h("div", { class: "hint" }, f.hint) : null);
    });
    const done = (v) => { layer.remove(); document.removeEventListener("keydown", onKey, true); resolve(v); };
    const submit = () => {
      const out = {};
      for (const f of fields) out[f.id] = f.type === "checkbox" ? inputs[f.id].checked : inputs[f.id].value;
      const problem = validate ? validate(out) : "";
      if (problem) { msg.textContent = problem; return; }
      done(out);
    };
    const onKey = (e) => {
      if (e.key === "Escape") { e.stopPropagation(); e.preventDefault(); done(null); }
      else if (e.key === "Enter" && e.target.tagName === "INPUT" && e.target.type !== "checkbox") { e.preventDefault(); submit(); }
    };
    layer.append(h("div", { class: "modal-box dialog-box", role: "dialog", "aria-modal": "true" },
      h("h2", {}, title), text ? h("p", { class: "dialog-text" }, text) : null, rows, msg,
      h("div", { class: "row", style: { marginTop: "10px", justifyContent: "flex-end" } },
        h("button", { class: "btn ghost", type: "button", onclick: () => done(null) }, "Cancel"),
        h("button", { class: "btn " + (danger ? "danger-solid" : "primary"), type: "button", onclick: submit }, ok))));
    layer.addEventListener("click", (e) => { if (e.target === layer) done(null); });
    document.addEventListener("keydown", onKey, true);
    document.body.append(layer);
    setTimeout(() => { const first = layer.querySelector("input, select"); if (first) first.focus(); }, 0);
  });
}

const PASSWORD_MIN = 10;

async function changePasswordDialog() {
  const v = await formDialog({
    title: "Change your password",
    text: `At least ${PASSWORD_MIN} characters. Changing it signs you out everywhere else.`,
    fields: [
      { id: "current", label: "Current password", type: "password", autocomplete: "current-password" },
      { id: "new", label: "New password", type: "password", autocomplete: "new-password" },
      { id: "again", label: "New password again", type: "password", autocomplete: "new-password" },
    ],
    ok: "Change it",
    validate: (o) => (o.new.length < PASSWORD_MIN ? `The new password needs ${PASSWORD_MIN} characters or more.` : o.new !== o.again ? "The two passwords differ." : ""),
  });
  if (v) await attempt(() => api("/auth/password", { body: { current: v.current, new: v.new } }), "Password changed");
}

/** A random password an administrator can give (shown once). */
function randomPassword() {
  const chars = "abcdefghijkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789";
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  return Array.from(bytes, (b) => chars[b % chars.length]).join("");
}

const fmtWhen = (t) => (t ? new Date(t * 1000).toLocaleString() : "never");

// ---------------------------------------------------------------- Users (administrators)

async function usersPage(main) {
  if (!isAdmin()) { main.append(h("div", { class: "note bad" }, "Only an administrator manages users.")); return; }
  let users = await api("/users");
  const box = h("div", { class: "panel" });
  const act = async (fn, okMsg) => {
    try { const r = await fn(); if (Array.isArray(r)) users = r; toast(okMsg); } catch (e) { toast(String(e.message || e), true); }
    draw();
  };

  const add = async () => {
    const v = await formDialog({
      title: "Add a user",
      text: "They will choose their own password the first time they sign in, unless you untick the box.",
      fields: [
        { id: "username", label: "User name", hint: "Lowercase letters, digits, . _ - (2 to 32 characters)" },
        { id: "password", label: "First password", value: randomPassword(), hint: "Give it to them yourself; it is not shown again." },
        { id: "role", label: "Role", type: "select", value: "user", options: [{ value: "user", label: "User: their own space" }, { value: "admin", label: "Administrator: sees and manages everything" }] },
        { id: "must_change", label: "They must change it at their first sign-in", type: "checkbox", value: true },
      ],
      ok: "Add",
      validate: (o) => (o.password.length < PASSWORD_MIN ? `The password needs ${PASSWORD_MIN} characters or more.` : ""),
    });
    if (!v) return;
    await act(() => api("/users", { body: v }), `${v.username} added. First password: ${v.password}`);
  };

  const reset = async (u) => {
    const v = await formDialog({
      title: `Reset the password of ${u.username}`,
      text: "Their sessions end now. They choose a new password at their next sign-in.",
      fields: [{ id: "password", label: "Temporary password", value: randomPassword(), hint: "Give it to them yourself." }],
      ok: "Reset",
      validate: (o) => (o.password.length < PASSWORD_MIN ? `The password needs ${PASSWORD_MIN} characters or more.` : ""),
    });
    if (v) await act(() => api("/users/" + encodeURIComponent(u.username), { method: "PATCH", body: { password: v.password, must_change: true } }), `Temporary password for ${u.username}: ${v.password}`);
  };

  const remove = async (u) => {
    const others = users.filter((x) => x.username !== u.username && !x.disabled);
    const v = await formDialog({
      title: `Remove ${u.username}`,
      text: `${u.username} can no longer sign in. Their ${u.resources} resource${u.resources === 1 ? "" : "s"} (models, results, palettes) go to the user you choose; nothing is deleted.`,
      fields: [{ id: "to", label: "Give their resources to", type: "select", value: ME.username, options: others.map((x) => ({ value: x.username, label: x.username + (x.username === ME.username ? " (you)" : "") })) }],
      ok: "Remove the user",
      danger: true,
    });
    if (!v) return;
    await act(async () => {
      const r = await api(`/users/${encodeURIComponent(u.username)}?transfer_to=${encodeURIComponent(v.to)}`, { method: "DELETE" });
      return r.users;
    }, `${u.username} removed`);
  };

  const patch = (u, body, msg) => act(() => api("/users/" + encodeURIComponent(u.username), { method: "PATCH", body }), msg);

  function draw() {
    box.replaceChildren(
      h("div", { class: "panel-head" }, h("h2", {}, `${users.length} user${users.length === 1 ? "" : "s"}`), h("button", { class: "btn primary", type: "button", onclick: add }, "Add a user")),
      h("table", {}, h("thead", {}, h("tr", {}, ["User", "Role", "State", "Last sign-in", "Resources", ""].map((c) => h("th", {}, c)))),
        h("tbody", {}, users.map((u) => h("tr", {},
          h("td", {}, h("strong", {}, u.username), u.username === ME.username ? h("span", { class: "muted small" }, "  (you)") : null),
          h("td", {}, u.role === "admin" ? h("span", { class: "tag amber" }, "admin") : "user"),
          h("td", {}, u.disabled ? h("span", { class: "tag bad" }, "disabled") : u.must_change ? h("span", { class: "tag amber" }, "must change password") : h("span", { class: "tag ok" }, "active")),
          h("td", { class: "small muted nowrap" }, fmtWhen(u.last_login)),
          h("td", {}, u.resources),
          h("td", {}, h("div", { class: "row", style: { gap: "4px", justifyContent: "flex-end", flexWrap: "wrap" } },
            h("button", { class: "btn sm", type: "button", onclick: () => reset(u) }, "Reset password"),
            u.must_change ? h("button", { class: "btn sm ghost", type: "button", onclick: () => patch(u, { must_change: false }, "No longer required") }, "Don't require change")
              : h("button", { class: "btn sm ghost", type: "button", onclick: () => patch(u, { must_change: true }, `${u.username} must change their password at the next sign-in`) }, "Require change"),
            h("button", { class: "btn sm ghost", type: "button", onclick: () => patch(u, { role: u.role === "admin" ? "user" : "admin" }, "Role changed") }, u.role === "admin" ? "Make user" : "Make admin"),
            u.username === ME.username ? null : h("button", { class: "btn sm ghost", type: "button", onclick: () => patch(u, { disabled: !u.disabled }, u.disabled ? "Enabled" : "Disabled") }, u.disabled ? "Enable" : "Disable"),
            u.username === ME.username ? null : h("button", { class: "btn sm danger", type: "button", onclick: () => remove(u) }, "Remove"))))))),
      h("p", { class: "small muted", style: { marginTop: "12px" } },
        "Each user has their own models, results, palettes, picks and jobs. Administrators see everything, with who owns it. Lost the only administrator password: ",
        h("code", {}, "python3 webapp/users_cli.py reset <name>"), " on the machine."));
  }

  main.append(head("Users", "Who can sign in, and what each one owns."), box);
  draw();
}

// ---------------------------------------------------------------- sharing

/** Who owns a resource and who can use it: a badge for lists. `item` has owner, shared_with, shared_all, access. */
function ownerBadge(item) {
  if (!item) return null;
  const bits = [];
  if (item.owner && (item.owner !== ME.username || item.shared_all || (item.shared_with || []).length)) {
    bits.push(item.owner === ME.username ? "yours" : item.owner);
  }
  if (item.shared_all) bits.push("shared with everyone");
  else if ((item.shared_with || []).length) bits.push("shared with " + item.shared_with.join(", "));
  if (item.access === "read" && item.owner !== ME.username) bits.push("read only");
  if (!bits.length) return null;
  const mine = item.owner === ME.username;
  return h("span", { class: "tag " + (mine ? "" : "amber"), title: "Owner: " + (item.owner || "nobody") }, bits.join(" · "));
}

/** The Share dialog of a model, a result folder or a palette. Only its owner (or an administrator) opens it. */
async function shareDialog(kind, key, label, onSaved) {
  let acl, names;
  try {
    [acl, names] = await Promise.all([api(`/acl/${kind}/${encodeURIComponent(key)}`), api("/users/names")]);
  } catch (e) { toast(String(e.message || e), true); return; }
  const chosen = new Set(acl.shared_with || []);
  let everyone = !!acl.shared_all;
  const layer = h("div", { class: "modal dialog-layer" });
  const list = h("div", { class: "stack", style: { gap: "4px", maxHeight: "260px", overflow: "auto" } });
  const drawList = () => {
    list.replaceChildren(...(names.length ? names.map((n) => h("label", { class: "row", style: { gap: "8px" } },
      h("input", { type: "checkbox", checked: chosen.has(n.username), disabled: everyone,
        onchange: (e) => { if (e.target.checked) chosen.add(n.username); else chosen.delete(n.username); } }), n.username))
      : [h("p", { class: "muted small" }, "There is nobody else yet. Add users on the Users page (administrators).")]));
  };
  const done = (saved) => { layer.remove(); document.removeEventListener("keydown", onKey, true); if (saved && onSaved) onSaved(); };
  const onKey = (e) => { if (e.key === "Escape") { e.stopPropagation(); done(false); } };
  const save = async () => {
    try {
      await api(`/acl/${kind}/${encodeURIComponent(key)}`, { method: "PUT", body: { users: [...chosen], all: everyone } });
      toast("Sharing saved");
      done(true);
    } catch (e) { toast(String(e.message || e), true); }
  };
  const everyoneBox = h("input", { type: "checkbox", checked: everyone, onchange: (e) => { everyone = e.target.checked; drawList(); } });
  layer.append(h("div", { class: "modal-box dialog-box", role: "dialog", "aria-modal": "true" },
    h("h2", {}, `Share ${label || key}`),
    h("p", { class: "dialog-text" }, `Owner: ${acl.owner || "nobody"}. People you share with can use it (generate with it, look at it, copy from it) but not change or delete it.`),
    h("label", { class: "row", style: { gap: "8px", marginBottom: "10px" } }, everyoneBox, "Everyone who has an account"),
    list,
    h("div", { class: "row", style: { marginTop: "18px", justifyContent: "flex-end" } },
      h("button", { class: "btn ghost", type: "button", onclick: () => done(false) }, "Cancel"),
      h("button", { class: "btn primary", type: "button", onclick: save }, "Save"))));
  layer.addEventListener("click", (e) => { if (e.target === layer) done(false); });
  document.addEventListener("keydown", onKey, true);
  drawList();
  document.body.append(layer);
}

/** A Share button for a list row or a header; null when this user cannot share it. */
function shareButton(kind, key, item, label, onSaved) {
  if (!item || item.access !== "write") return null;
  return h("button", { class: "btn sm ghost", type: "button", title: "Choose who can use this", onclick: () => shareDialog(kind, key, label, onSaved) }, "Share");
}
