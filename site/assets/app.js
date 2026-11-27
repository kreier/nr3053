(() => {
  const lang = document.documentElement.lang === "vi" ? "vi" : "en";
  const I18N = {
    en: {
      of256: "of 256 MiB",
      blk: "blk",
      copy: "Copy",
      copied: "Copied!",
      failed: "Failed",
      light: "☀ Light",
      dark: "☾ Dark",
      payloadTip: (kib, pct) => `ImmortalWrt v2.5.0 FIP payload: ${kib} KiB (${pct}% of 2 MiB slot)`,
    },
    vi: {
      of256: "của 256 MiB",
      blk: "block",
      copy: "Sao chép",
      copied: "Đã chép!",
      failed: "Lỗi",
      light: "☀ Sáng",
      dark: "☾ Tối",
      payloadTip: (kib, pct) => `File FIP ImmortalWrt v2.5.0: ${kib} KiB (${pct}% của vùng 2 MiB)`,
    },
  }[lang];

  /* ---------- Theme Toggle ---------- */
  const root = document.documentElement;
  const themeBtn = document.getElementById("theme-toggle");
  const savedTheme = localStorage.getItem("nr3053-theme");
  if (savedTheme === "dark" || savedTheme === "light") {
    root.setAttribute("data-theme", savedTheme);
  }
  function syncThemeBtn() {
    if (!themeBtn) return;
    const explicit = root.getAttribute("data-theme");
    const isDark =
      explicit === "dark" ||
      (!explicit && window.matchMedia("(prefers-color-scheme: dark)").matches);
    themeBtn.textContent = isDark ? I18N.light : I18N.dark;
  }
  if (themeBtn) {
    themeBtn.addEventListener("click", () => {
      const explicit = root.getAttribute("data-theme");
      const currentlyDark =
        explicit === "dark" ||
        (!explicit && window.matchMedia("(prefers-color-scheme: dark)").matches);
      const next = currentlyDark ? "light" : "dark";
      root.setAttribute("data-theme", next);
      localStorage.setItem("nr3053-theme", next);
      syncThemeBtn();
    });
    syncThemeBtn();
  }

  /* ---------- Close Mobile Menu on Link Click ---------- */
  const navToggle = document.getElementById("nav-open");
  if (navToggle) {
    document.querySelectorAll("nav.side a").forEach((a) => {
      a.addEventListener("click", () => {
        navToggle.checked = false;
      });
    });
  }

  /* ---------- Right-Sidebar "On this page" Scrollspy ---------- */
  const tocLinks = Array.from(document.querySelectorAll("aside.toc-right a"));
  const headings = tocLinks
    .map((a) => {
      const id = (a.getAttribute("href") || "").replace(/^#/, "");
      return id ? document.getElementById(id) : null;
    })
    .filter(Boolean);

  if (tocLinks.length && headings.length) {
    const setActiveToc = (id) => {
      tocLinks.forEach((a) =>
        a.classList.toggle("active", a.getAttribute("href") === "#" + id)
      );
    };
    setActiveToc(headings[0].id);

    if ("IntersectionObserver" in window) {
      const visible = new Set();
      const observer = new IntersectionObserver(
        (entries) => {
          entries.forEach((entry) => {
            if (entry.isIntersecting) visible.add(entry.target.id);
            else visible.delete(entry.target.id);
          });
          for (const h of headings) {
            if (visible.has(h.id)) {
              setActiveToc(h.id);
              return;
            }
          }
          const offset = 110;
          let current = headings[0].id;
          for (const h of headings) {
            if (h.getBoundingClientRect().top <= offset) current = h.id;
          }
          setActiveToc(current);
        },
        { rootMargin: "-64px 0px -65% 0px", threshold: 0 }
      );
      headings.forEach((h) => observer.observe(h));
    }
  }

  /* ---------- Copy Buttons on Code Blocks ---------- */
  document.querySelectorAll(".code-block").forEach((block) => {
    const pre = block.querySelector("pre");
    if (!pre) return;
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "copy-btn";
    btn.textContent = I18N.copy;
    btn.addEventListener("click", async () => {
      try {
        await navigator.clipboard.writeText(pre.innerText.trimEnd());
        btn.textContent = I18N.copied;
        btn.classList.add("copied");
        setTimeout(() => {
          btn.textContent = I18N.copy;
          btn.classList.remove("copied");
        }, 1600);
      } catch {
        btn.textContent = I18N.failed;
        setTimeout(() => (btn.textContent = I18N.copy), 1400);
      }
    });
    block.appendChild(btn);
  });

  /* ---------- Interactive 256 MiB Flash Map (Partitions Page) ---------- */
  const dataEl = document.getElementById("partition-data");
  const fullBar = document.getElementById("full-bar");
  const bootBar = document.getElementById("boot-bar");
  if (!dataEl || !fullBar || !bootBar) return;

  const DATA = JSON.parse(dataEl.textContent);
  const TOTAL_KIB = DATA.chip.total_kib;
  const BOOT_KIB = 6 * 1024;
  const legendEl = document.getElementById("legend");
  const layoutSummary = document.getElementById("layout-summary");
  const partRows = Array.from(document.querySelectorAll("tr.part-row"));

  const hex = (n) => "0x" + n.toString(16).padStart(8, "0");
  const fmtSize = (kib) =>
    kib >= 1024 ? `${(kib / 1024).toFixed(kib % 1024 === 0 ? 0 : 2)} MiB` : `${kib} KiB`;
  const fmtPct = (kib) => `${((kib / TOTAL_KIB) * 100).toFixed(2)}%`;

  const getDesc = (obj) => (lang === "vi" && obj.desc_vi ? obj.desc_vi : obj.desc);
  const getName = (obj) => (lang === "vi" && obj.name_vi ? obj.name_vi : obj.name);
  const getSummary = (obj) => (lang === "vi" && obj.summary_vi ? obj.summary_vi : obj.summary);

  let currentMode = "immortalwrt";

  function clearActive() {
    document.querySelectorAll(".seg.active").forEach((el) => el.classList.remove("active"));
    partRows.forEach((r) => r.classList.remove("active"));
  }

  function highlightMembers(members) {
    clearActive();
    if (!members || !members.length) return;
    const set = new Set(members);
    partRows.forEach((row) => {
      if (set.has(row.dataset.mtd)) row.classList.add("active");
    });
    document.querySelectorAll(".seg").forEach((seg) => {
      const segMembers = (seg.dataset.members || "").split(",");
      if (segMembers.some((m) => set.has(m))) seg.classList.add("active");
    });
  }

  function showInspector(item) {
    const end = item.start + item.kib * 1024;
    const blocks = item.kib / DATA.chip.erase_block_kib;
    document.getElementById("insp-swatch").style.background = item.hatch
      ? "repeating-linear-gradient(-45deg, transparent, transparent 3px, var(--muted) 3px, var(--muted) 6px)"
      : item.color;
    document.getElementById("insp-title").textContent = getName(item);
    document.getElementById("insp-mtd").textContent = item.mtd;
    document.getElementById("insp-metrics").textContent =
      `${fmtSize(item.kib)} (${item.kib.toLocaleString()} KiB) · ${fmtPct(item.kib)} ${I18N.of256}`;
    document.getElementById("insp-desc").textContent = getDesc(item);
    document.getElementById("insp-range").textContent =
      `${hex(item.start)}–${hex(end)} · ${blocks} ${I18N.blk}`;
    highlightMembers(item.members || [item.mtd]);
  }

  function renderFullBar(mode) {
    const layout = DATA.layouts[mode];
    if (!layout) return;
    fullBar.innerHTML = "";
    legendEl.innerHTML = "";
    if (layoutSummary) layoutSummary.textContent = getSummary(layout);

    layout.segments.forEach((seg) => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "seg" + (seg.highlight ? " highlight" : "") + (seg.hatch ? " hatch" : "");
      btn.style.width = `${(seg.kib / TOTAL_KIB) * 100}%`;
      if (!seg.hatch) btn.style.background = seg.color;
      btn.dataset.members = (seg.members || [seg.mtd]).join(",");
      btn.setAttribute("aria-label", `${getName(seg)}: ${fmtSize(seg.kib)} (${fmtPct(seg.kib)})`);

      const lbl = lang === "vi" && seg.label_vi ? seg.label_vi : seg.label;
      if (lbl) {
        const span = document.createElement("span");
        span.className = "seg-label";
        span.textContent = lbl;
        btn.appendChild(span);
      }

      btn.addEventListener("mouseenter", () => showInspector(seg));
      btn.addEventListener("focus", () => showInspector(seg));
      btn.addEventListener("click", () => showInspector(seg));
      fullBar.appendChild(btn);

      const lbtn = document.createElement("button");
      lbtn.type = "button";
      lbtn.className = "legend-item";
      const swStyle = seg.hatch
        ? "background: repeating-linear-gradient(-45deg, transparent, transparent 3px, var(--muted) 3px, var(--muted) 6px); border: 1px solid var(--border);"
        : `background: ${seg.color};`;
      lbtn.innerHTML = `
        <span class="legend-name">
          <span class="swatch" style="${swStyle}"></span>
          <span>${getName(seg)}</span>
        </span>
        <span class="mono">${fmtSize(seg.kib)} (${fmtPct(seg.kib)})</span>
      `;
      lbtn.addEventListener("mouseenter", () => showInspector(seg));
      lbtn.addEventListener("click", () => showInspector(seg));
      legendEl.appendChild(lbtn);
    });
  }

  function renderBootBar() {
    bootBar.innerHTML = "";
    const bootParts = DATA.partitions.slice(0, 4);
    bootParts.forEach((p) => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "seg" + (p.highlight ? " highlight" : "");
      btn.style.width = `${(p.kib / BOOT_KIB) * 100}%`;
      btn.style.background = p.color;
      btn.dataset.members = p.mtd;
      btn.setAttribute("aria-label", `${p.name} (${p.mtd}): ${fmtSize(p.kib)} (${fmtPct(p.kib)})`);

      const span = document.createElement("span");
      span.className = "seg-label";
      span.textContent = p.name;
      btn.appendChild(span);

      if (p.fip_kib) {
        const fill = document.createElement("span");
        fill.className = "seg-fill";
        const pct = ((p.fip_kib / p.kib) * 100).toFixed(1);
        fill.style.width = `${pct}%`;
        fill.title = I18N.payloadTip(p.fip_kib, pct);
        btn.appendChild(fill);
      }

      btn.addEventListener("mouseenter", () => showInspector(p));
      btn.addEventListener("focus", () => showInspector(p));
      btn.addEventListener("click", () => showInspector(p));
      bootBar.appendChild(btn);
    });
  }

  partRows.forEach((row) => {
    const p = DATA.partitions.find((item) => item.mtd === row.dataset.mtd);
    if (!p) return;
    row.addEventListener("mouseenter", () => showInspector(p));
    row.addEventListener("click", () => showInspector(p));
  });

  document.querySelectorAll(".mode-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      currentMode = btn.dataset.mode;
      document.querySelectorAll(".mode-btn").forEach((b) =>
        b.classList.toggle("active", b.dataset.mode === currentMode)
      );
      renderFullBar(currentMode);
      const fipPart = DATA.partitions.find((p) => p.mtd === "mtd3");
      if (fipPart) showInspector(fipPart);
    });
  });

  renderBootBar();
  renderFullBar(currentMode);
  const initial = DATA.partitions.find((p) => p.mtd === "mtd3");
  if (initial) showInspector(initial);
})();
