/* Exports page: sticky bottom bar tracking the bulk-delete checkbox selection. */
function updateBulkBar() {
  var boxes = document.querySelectorAll('#bulk-form input[name="ids"]:checked');
  var bar = document.getElementById("bulk-bar");
  var count = document.getElementById("bulk-count");
  if (!bar || !count) return;
  bar.hidden = boxes.length === 0;
  count.textContent = boxes.length + " selected";
}
function toggleAllExports(master) {
  document.querySelectorAll('#bulk-form input[name="ids"]').forEach(function (cb) {
    cb.checked = master.checked;
  });
  updateBulkBar();
}

/* Exports page: type a show/movie name to highlight matching rows (dims the rest). */
function highlightExports(query) {
  var q = (query || "").trim().toLowerCase();
  document.querySelectorAll("#bulk-form tbody tr").forEach(function (tr) {
    var match = q.length > 0 && tr.textContent.toLowerCase().indexOf(q) !== -1;
    tr.classList.toggle("row-highlight", match);
    tr.classList.toggle("row-dim", q.length > 0 && !match);
  });
}

/* Exports page: the "manage series" drawer opened from an episode row's title. */
function manager() {
  return {
    open: false,
    html: "",
    async show(url) {
      this.open = true;
      this.html = '<p class="muted">Loading…</p>';
      try {
        const r = await fetch(url);
        this.html = await r.text();
      } catch (e) {
        this.html = '<p class="banner err">Failed to load.</p>';
      }
    },
    close() {
      this.open = false;
    },
  };
}

async function manageDeleteSeries(seriesId) {
  if (!confirm("Delete every exported file for this series?")) return;
  try {
    const r = await fetch("/tv/" + seriesId + "/delete-exports", { method: "POST" });
    if (!r.ok) throw new Error("delete failed");
    location.reload();
  } catch (e) {
    window.toast("Delete failed.", "err");
  }
}

async function manageDeleteEpisode(exportId) {
  if (!confirm("Delete this episode's file?")) return;
  try {
    const r = await fetch("/exports/" + exportId + "/delete", { method: "POST" });
    if (!r.ok) throw new Error("delete failed");
    location.reload();
  } catch (e) {
    window.toast("Delete failed.", "err");
  }
}

async function manageRetargetEpisode(exportId) {
  var sel = document.getElementById("ep-source-" + exportId);
  if (!sel) return;
  try {
    const r = await fetch("/exports/" + exportId + "/retarget", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ episode_id: parseInt(sel.value, 10) }),
    });
    const data = await r.json().catch(function () { return {}; });
    if (!r.ok || !data.ok) {
      window.toast(data.detail || "Change failed.", "err");
      return;
    }
    location.reload();
  } catch (e) {
    window.toast("Change failed.", "err");
  }
}

async function manageRetargetSeries(seriesId) {
  var sel = document.getElementById("series-source-select");
  if (!sel) return;
  if (!confirm("Re-point every episode of this series to the selected source?")) return;
  try {
    const r = await fetch("/exports/series/" + seriesId + "/retarget", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ target_series_id: parseInt(sel.value, 10) }),
    });
    const data = await r.json().catch(function () { return {}; });
    if (!r.ok || !data.ok) {
      window.toast((data.errors && data.errors[0]) || "Some episodes failed to change.", "err");
      return;
    }
    location.reload();
  } catch (e) {
    window.toast("Change failed.", "err");
  }
}

/* Restore the per-tab Posters/List preference saved in localStorage. */
function restoreView() {
  try {
    var el = document.getElementById("results");
    if (!el || !window.htmx) return;
    var saved = localStorage.getItem("strmvert.view." + el.dataset.tab);
    if (saved && saved !== el.dataset.view && (saved === "grid" || saved === "list")) {
      var qs = el.dataset.qsNoview ? el.dataset.qsNoview + "&" : "";
      htmx.ajax("GET", el.dataset.pageUrl + "?" + qs + "view=" + saved, {
        target: "#results",
        pushUrl: true,
      });
    }
  } catch (e) {}
}

/* Category filter popup shared by the Movies and TV filter bars — a
 * searchable multi-select "groups" picker (Dispatcharr-style): search
 * narrows which categories are shown, Select/De-select visible operate on
 * exactly what the search currently shows, and the available list itself
 * switches instantly (client-side, no round-trip) with the Server field
 * since every server's categories are embedded on the page up front. */
function categoryPicker(byServer, initialSelected) {
  return {
    open: false,
    search: "",
    serverValue: "",
    byServer: byServer || {},
    checked: new Set(initialSelected || []),

    get available() {
      return this.byServer[this.serverValue] || this.byServer[""] || [];
    },
    get visible() {
      const q = this.search.trim().toLowerCase();
      return q ? this.available.filter((c) => c.toLowerCase().includes(q)) : this.available;
    },
    toggleCat(cat) {
      this.checked.has(cat) ? this.checked.delete(cat) : this.checked.add(cat);
      this.checked = new Set(this.checked);
      this._apply();
    },
    selectVisible() {
      this.visible.forEach((c) => this.checked.add(c));
      this.checked = new Set(this.checked);
      this._apply();
    },
    deselectVisible() {
      this.visible.forEach((c) => this.checked.delete(c));
      this.checked = new Set(this.checked);
      this._apply();
    },
    _apply() {
      this.$nextTick(() => {
        const form = this.$el.closest("form");
        if (form) form.dispatchEvent(new Event("change"));
      });
    },
  };
}

/* Selection + export behaviour shared by the Movies and TV tabs.
 * Selection is persisted to localStorage (not just page-local state) so a
 * user can tick movies, switch to TV Shows, tick episodes/series there too,
 * and submit everything together — the /export endpoint already accepts
 * mixed m:/e:/s: tokens in one request. */
const SELECTION_KEY = "strmvert.selection";

function library() {
  return {
    selected: new Set(),
    busy: false,
    detailOpen: false,
    detailHtml: "",

    init() {
      try {
        const raw = localStorage.getItem(SELECTION_KEY);
        if (raw) this.selected = new Set(JSON.parse(raw));
      } catch (e) {}
    },
    _persist() {
      try {
        localStorage.setItem(SELECTION_KEY, JSON.stringify(Array.from(this.selected)));
      } catch (e) {}
    },

    has(tok) { return this.selected.has(tok); },
    toggle(tok) {
      this.selected.has(tok) ? this.selected.delete(tok) : this.selected.add(tok);
      this.selected = new Set(this.selected);
      this._persist();
    },
    toggleMany(tokens, on) {
      tokens.forEach((t) => (on ? this.selected.add(t) : this.selected.delete(t)));
      this.selected = new Set(this.selected);
      this._persist();
    },
    allOn(tokens) {
      return tokens.length > 0 && tokens.every((t) => this.selected.has(t));
    },
    clear() { this.selected = new Set(); this._persist(); },
    get count() { return this.selected.size; },

    /* Selects every id matching the CURRENT filters (not just the visible
     * page) — url is one of /movies/ids or /tv/ids with the active filter
     * querystring already attached server-side. */
    async selectAllInView(kind, url) {
      try {
        const r = await fetch(url);
        const data = await r.json();
        const tokens = (data.ids || []).map((id) => kind + ":" + id);
        this.toggleMany(tokens, true);
        window.toast(
          "Selected " + tokens.length + " item" + (tokens.length === 1 ? "" : "s") + ".",
          "note"
        );
      } catch (e) {
        window.toast("Select all failed.", "err");
      }
    },
    get counts() {
      let movies = 0, tv = 0;
      this.selected.forEach((t) => (t.startsWith("m:") ? movies++ : tv++));
      return { movies, tv };
    },

    async openDetail(url) {
      this.detailOpen = true;
      this.detailHtml = '<p class="muted">Loading…</p>';
      try {
        const r = await fetch(url);
        this.detailHtml = await r.text();
      } catch (e) {
        this.detailHtml = '<p class="banner err">Failed to load details.</p>';
      }
    },

    async submit() {
      if (this.count === 0 || this.busy) return;
      this.busy = true;
      try {
        const r = await fetch("/export", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ selections: Array.from(this.selected) }),
        });
        const data = await r.json().catch(() => ({}));
        if (!r.ok || !data.ok) {
          window.toast((data.errors && data.errors[0]) || "Export failed.", "err");
        }
        if (data.written) {
          window.toast(
            "Wrote " + data.written + " file" + (data.written === 1 ? "" : "s") +
              (data.failed ? ", " + data.failed + " failed" : ""),
            data.failed ? "err" : "note"
          );
        }
        this.clear();
        const form = document.getElementById("filter-form");
        if (form) form.requestSubmit();
      } catch (e) {
        window.toast("Export request failed.", "err");
      } finally {
        this.busy = false;
      }
    },
  };
}
