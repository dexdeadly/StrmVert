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

/* Selection + export behaviour shared by the Movies and TV tabs. */
function library() {
  return {
    selected: new Set(),
    busy: false,
    detailOpen: false,
    detailHtml: "",

    has(tok) { return this.selected.has(tok); },
    toggle(tok) {
      this.selected.has(tok) ? this.selected.delete(tok) : this.selected.add(tok);
      this.selected = new Set(this.selected);
    },
    toggleMany(tokens, on) {
      tokens.forEach((t) => (on ? this.selected.add(t) : this.selected.delete(t)));
      this.selected = new Set(this.selected);
    },
    allOn(tokens) {
      return tokens.length > 0 && tokens.every((t) => this.selected.has(t));
    },
    clear() { this.selected = new Set(); },
    get count() { return this.selected.size; },

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
