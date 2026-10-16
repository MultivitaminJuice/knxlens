// Notizfelder speichern beim Verlassen bzw. Enter; Suche filtert Tabellenzeilen.
document.addEventListener("change", async (ev) => {
  const el = ev.target;
  if (!el.matches("input.note")) return;
  el.classList.remove("saved", "error");
  try {
    const res = await fetch("/api/note", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ kind: el.dataset.kind, key: el.dataset.key, field: el.dataset.field, value: el.value }),
    });
    el.classList.add(res.ok ? "saved" : "error");
    setTimeout(() => el.classList.remove("saved"), 1200);
  } catch {
    el.classList.add("error");
  }
});

document.addEventListener("keydown", (ev) => {
  if (ev.key === "Enter" && ev.target.matches("input.note")) ev.target.blur();
});

const search = document.querySelector("input[data-filter]");
if (search) {
  const rows = [...document.querySelectorAll(search.dataset.filter)];
  const apply = () => {
    const q = search.value.trim().toLowerCase();
    let shown = 0;
    for (const row of rows) {
      const text = (row.textContent + " " + [...row.querySelectorAll("input")].map((i) => i.value).join(" ")).toLowerCase();
      const hit = !q || text.includes(q);
      row.hidden = !hit;
      shown += hit;
    }
    const counter = document.querySelector("[data-count]");
    if (counter) counter.textContent = shown;
  };
  search.addEventListener("input", apply);
  apply();
}

// Live-Liste: neue Telegramme alle 2 s nachladen (nur Lesen aus der Datenbank).
const live = document.querySelector("tbody[data-live]");
if (live) {
  let after = 0;
  let paused = false;
  const toggle = document.querySelector("[data-live-toggle]");
  toggle?.addEventListener("click", () => {
    paused = !paused;
    toggle.textContent = paused ? "Weiter" : "Pause";
  });
  const cell = (text, cls) => {
    const td = document.createElement("td");
    td.textContent = text;
    if (cls) td.className = cls;
    return td;
  };
  const poll = async () => {
    if (!paused) {
      try {
        const res = await fetch(`/api/telegrams?after=${after}&limit=${after ? 200 : 30}`);
        const { telegrams } = await res.json();
        for (const t of telegrams.reverse()) {
          after = Math.max(after, t.id);
          const tr = document.createElement("tr");
          tr.append(
            cell(t.time, "mono muted"),
            cell(t.src, t.src_known ? "mono" : "mono warn"),
            cell(t.dst, "mono"),
            cell(t.name),
            cell(t.service, "muted"),
            cell(t.value + (t.dpt_source === "Vorschlag" ? " ?" : ""), "nowrap"),
          );
          live.prepend(tr);
        }
        while (live.rows.length > 200) live.deleteRow(-1);
      } catch {
        /* Server kurz weg – nächster Versuch in 2 s */
      }
    }
    setTimeout(poll, 2000);
  };
  poll();
}
