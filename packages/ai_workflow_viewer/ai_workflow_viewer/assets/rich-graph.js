(function () {
  const dataEl = document.getElementById("observation-data");
  if (!dataEl) return;
  const data = JSON.parse(dataEl.textContent);
  const graph = document.getElementById("rich-graph");
  const edgeLayer = document.getElementById("rich-edges");
  const inspector = document.getElementById("node-inspector");
  const search = document.getElementById("node-search");
  const nodes = new Map(data.nodes.map((node) => [node.id, node]));
  const nodeEls = new Map();
  const positions = new Map();
  const initialPositions = new Map(
    data.nodes.map((node) => [node.id, { x: node.x, y: node.y }])
  );

  function escapeHtml(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;");
  }

  function setNodePosition(id, x, y) {
    const node = nodes.get(id);
    const el = nodeEls.get(id);
    if (!node || !el) return;
    node.x = Math.max(0, x);
    node.y = Math.max(0, y);
    positions.set(id, { x: node.x, y: node.y, width: node.width, height: node.height });
    el.style.left = `${node.x}px`;
    el.style.top = `${node.y}px`;
  }

  function isHidden(id) {
    const el = nodeEls.get(id);
    return Boolean(el && el.classList.contains("is-hidden"));
  }

  function renderEdges() {
    edgeLayer.setAttribute("viewBox", `0 0 ${data.canvas.width} ${data.canvas.height}`);
    edgeLayer.setAttribute("width", data.canvas.width);
    edgeLayer.setAttribute("height", data.canvas.height);
    edgeLayer.innerHTML = '<defs><marker id="arrow" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto"><path d="M 0 0 L 8 4 L 0 8 z" fill="#7b8794"></path></marker></defs>';
    for (const edge of data.transitions) {
      if (isHidden(edge.source) || isHidden(edge.target)) continue;
      const source = positions.get(edge.source);
      const target = positions.get(edge.target);
      if (!source || !target) continue;
      const sx = source.x + source.width;
      const sy = source.y + source.height / 2;
      const tx = target.x;
      const ty = target.y + target.height / 2;
      const bend = Math.max(70, Math.abs(tx - sx) / 2);
      const d = `M ${sx} ${sy} C ${sx + bend} ${sy}, ${tx - bend} ${ty}, ${tx} ${ty}`;
      const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
      path.setAttribute("d", d);
      path.setAttribute("class", `rich-edge ${edge.policy === "decision" ? "decision" : ""}`);
      path.setAttribute("marker-end", "url(#arrow)");
      edgeLayer.appendChild(path);
      if (edge.label || edge.description) {
        const label = document.createElementNS("http://www.w3.org/2000/svg", "text");
        label.setAttribute("x", (sx + tx) / 2);
        label.setAttribute("y", (sy + ty) / 2 - 6);
        label.setAttribute("class", "rich-edge-label");
        label.textContent = edge.label || edge.description;
        edgeLayer.appendChild(label);
      }
    }
  }

  function jsonNode(value) {
    if (value === null) return '<span class="j-null">null</span>';
    const t = typeof value;
    if (t === "number") return `<span class="j-num">${value}</span>`;
    if (t === "boolean") return `<span class="j-bool">${value}</span>`;
    if (t === "string") return `<span class="j-str">${escapeHtml(JSON.stringify(value))}</span>`;
    if (Array.isArray(value)) {
      if (!value.length) return '<span class="j-bracket">[]</span>';
      const items = value.map((v, i) => `<div class="j-item">${jsonNode(v)}${i < value.length - 1 ? '<span class="j-comma">,</span>' : ''}</div>`).join("");
      return `<details open><summary><span class="j-bracket">[</span> <span class="j-count">${value.length}</span></summary><div class="j-children">${items}</div><span class="j-bracket">]</span></details>`;
    }
    if (t === "object") {
      const keys = Object.keys(value);
      if (!keys.length) return '<span class="j-bracket">{}</span>';
      const items = keys.map((k, i) => `<div class="j-item"><span class="j-key">${escapeHtml(JSON.stringify(k))}</span><span class="j-colon">: </span>${jsonNode(value[k])}${i < keys.length - 1 ? '<span class="j-comma">,</span>' : ''}</div>`).join("");
      return `<details open><summary><span class="j-bracket">{</span> <span class="j-count">${keys.length}</span></summary><div class="j-children">${items}</div><span class="j-bracket">}</span></details>`;
    }
    return escapeHtml(String(value));
  }

  function jsonTreeHtml(text) {
    if (!text) return "";
    try { return `<div class="json-tree">${jsonNode(JSON.parse(text))}</div>`; }
    catch (e) { return `<pre>${escapeHtml(text)}</pre>`; }
  }

  function detailHtml(detail) {
    const rawLink = detail.anchor
      ? `<a href="#${escapeHtml(detail.anchor)}" data-open-detail="${escapeHtml(detail.anchor)}">open in raw list</a>`
      : "";
    const previewLink = detail.links?.page
      ? `<a href="${escapeHtml(detail.links.page)}">bounded preview</a>`
      : "";
    const downloadLink = detail.links?.download
      ? `<a href="${escapeHtml(detail.links.download)}" download>exact download</a>`
      : "";
    return `
      <div class="detail-card">
        <div class="detail-card-header">
          <span class="detail-kind">${escapeHtml(detail.kind)}</span>
          <span class="muted">${escapeHtml(detail.storage || "detail")}</span>
          ${rawLink}
          ${previewLink}
          ${downloadLink}
        </div>
        <div class="detail-summary">${escapeHtml(detail.summary || "(no summary)")}</div>
        ${detail.digest ? `<div class="detail-digest">digest ${escapeHtml(detail.digest)}</div>` : ""}
        <details class="raw-toggle">
          <summary>Raw ${escapeHtml(detail.content_type || "detail")}</summary>
          ${jsonTreeHtml(detail.body || "")}
        </details>
      </div>`;
  }

  function eventHtml(event) {
    const bits = [
      event.timestamp_label || event.timestamp || "",
      event.phase || "event",
      event.decision ? `→ ${event.decision}` : "",
      event.elapsed_ms == null ? "" : `${event.elapsed_ms} ms`,
      event.detail_refs && event.detail_refs.length ? `${event.detail_refs.length} detail refs` : "",
    ].filter(Boolean).join(" · ");
    return `<div class="event-chip ${event.error ? "error" : ""}">
      <strong>${escapeHtml(bits || event.event_id.slice(0, 8))}</strong>
      ${event.error ? `<div>error: ${escapeHtml(event.error)}</div>` : ""}
    </div>`;
  }

  function selectNode(id) {
    const node = nodes.get(id);
    if (!node) return;
    for (const el of nodeEls.values()) el.classList.remove("is-selected");
    const selectedEl = nodeEls.get(id);
    if (selectedEl) selectedEl.classList.add("is-selected");
    const decisions = node.decisions.length ? node.decisions.join(", ") : "-";
    const errors = node.errors.length ? node.errors.map(escapeHtml).join("<br>") : "-";
    const details = node.details.length
      ? node.details.map(detailHtml).join("")
      : '<p class="muted">No detail records captured for this node.</p>';
    const events = node.events.length
      ? node.events.map(eventHtml).join("")
      : '<p class="muted">No timeline events captured for this node.</p>';
    inspector.innerHTML = `
      <h2>${escapeHtml(node.label)}</h2>
      <p class="muted"><code>${escapeHtml(node.id)}</code></p>
      <p>${escapeHtml(node.description)}</p>
      <dl class="inspector-grid">
        <dt>Status</dt><dd>${escapeHtml(node.raw_status)}</dd>
        <dt>Kind</dt><dd>${escapeHtml(node.kind_label)}</dd>
        <dt>Attempts</dt><dd>${escapeHtml(node.attempts)}</dd>
        <dt>Elapsed</dt><dd>${node.elapsed_ms ? `${escapeHtml(node.elapsed_ms)} ms` : "-"}</dd>
        <dt>Usage</dt><dd>${escapeHtml(node.usage)}</dd>
        <dt>Decisions</dt><dd>${escapeHtml(decisions)}</dd>
        <dt>Errors</dt><dd>${errors}</dd>
      </dl>
      <div class="inspector-section">
        <h3>Significant Details</h3>
        ${details}
      </div>
      <div class="inspector-section">
        <h3>Timeline For Node</h3>
        <div class="event-list">${events}</div>
      </div>`;
    inspector.querySelectorAll("[data-open-detail]").forEach((link) => {
      link.addEventListener("click", (event) => {
        const target = document.getElementById(link.dataset.openDetail);
        if (target && target.tagName.toLowerCase() === "details") target.open = true;
      });
    });
  }

  function applySearch() {
    const query = (search.value || "").trim().toLowerCase();
    for (const node of data.nodes) {
      const el = nodeEls.get(node.id);
      if (!el) continue;
      el.classList.toggle("is-hidden", Boolean(query && !node.search.includes(query)));
    }
    renderEdges();
  }

  for (const node of data.nodes) {
    const el = document.querySelector(`[data-node-id="${CSS.escape(node.id)}"]`);
    if (!el) continue;
    nodeEls.set(node.id, el);
    positions.set(node.id, { x: node.x, y: node.y, width: node.width, height: node.height });
    el.addEventListener("click", () => selectNode(node.id));
    el.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        selectNode(node.id);
      }
    });

    let drag = null;
    el.addEventListener("pointerdown", (event) => {
      if (event.button !== 0) return;
      drag = { startX: event.clientX, startY: event.clientY, x: node.x, y: node.y };
      el.setPointerCapture(event.pointerId);
    });
    el.addEventListener("pointermove", (event) => {
      if (!drag) return;
      setNodePosition(node.id, drag.x + event.clientX - drag.startX, drag.y + event.clientY - drag.startY);
      renderEdges();
    });
    el.addEventListener("pointerup", () => { drag = null; });
    el.addEventListener("pointercancel", () => { drag = null; });
  }

  document.querySelectorAll("[data-mode]").forEach((button) => {
    button.addEventListener("click", () => {
      graph.dataset.mode = button.dataset.mode;
      document.querySelectorAll("[data-mode]").forEach((item) => item.classList.remove("is-active"));
      button.classList.add("is-active");
    });
  });

  document.getElementById("reset-layout")?.addEventListener("click", () => {
    for (const node of data.nodes) {
      const initial = initialPositions.get(node.id);
      if (initial) setNodePosition(node.id, initial.x, initial.y);
    }
    applySearch();
  });

  search?.addEventListener("input", applySearch);

  document.querySelectorAll("[data-detail-action]").forEach((button) => {
    button.addEventListener("click", () => {
      const shouldOpen = button.dataset.detailAction === "open";
      document.querySelectorAll("details.observation-detail").forEach((item) => {
        item.open = shouldOpen;
      });
    });
  });

  document.querySelectorAll("[data-open-dialog]").forEach((button) => {
    button.addEventListener("click", () => {
      const dialog = document.getElementById(button.dataset.openDialog);
      if (dialog && typeof dialog.showModal === "function") dialog.showModal();
    });
  });

  document.querySelectorAll("[data-load-detail]").forEach((button) => {
    button.addEventListener("click", async () => {
      const target = document.getElementById(button.dataset.previewTarget);
      const dialog = document.getElementById(button.dataset.dialogTarget);
      const status = button.parentElement?.querySelector(".copy-status");
      if (!target || !dialog) return;
      if (button.dataset.loaded === "true") {
        if (typeof dialog.showModal === "function") dialog.showModal();
        return;
      }
      button.disabled = true;
      if (status) status.textContent = "loading validated preview";
      try {
        const response = await fetch(button.dataset.loadDetail, { credentials: "same-origin" });
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        target.textContent = await response.text();
        button.dataset.loaded = "true";
        button.textContent = "Open preview";
        if (status) status.textContent = "";
        if (typeof dialog.showModal === "function") dialog.showModal();
      } catch (error) {
        if (status) status.textContent = `preview unavailable: ${error.message}`;
      } finally {
        button.disabled = false;
      }
    });
  });

  document.querySelectorAll("[data-close-dialog]").forEach((button) => {
    button.addEventListener("click", () => {
      const dialog = button.closest("dialog");
      if (dialog) dialog.close();
    });
  });

  document.querySelectorAll("[data-copy-target]").forEach((button) => {
    button.addEventListener("click", async () => {
      const target = document.getElementById(button.dataset.copyTarget);
      if (!target) return;
      try {
        await navigator.clipboard.writeText(target.textContent || "");
        const status = button.parentElement?.querySelector(".copy-status");
        if (status) {
          status.textContent = "copied";
          window.setTimeout(() => { status.textContent = ""; }, 1200);
        }
      } catch (_error) {
        window.prompt("Copy detail payload", target.textContent || "");
      }
    });
  });

  renderEdges();
  const firstObserved = data.nodes.find((node) => node.raw_status !== "not_started") || data.nodes[0];
  if (firstObserved) selectNode(firstObserved.id);
})();
