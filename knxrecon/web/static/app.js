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
