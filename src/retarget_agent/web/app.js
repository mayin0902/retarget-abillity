"use strict";

const GRADE_HELP = {
  A: "可直接交付",
  B: "小修即可用",
  C: "需要明显返工",
  D: "不可使用",
  Skip: "无法判断",
};

const REASON_LABELS = {
  content_cutoff: "关键内容被裁切",
  text_or_logo_damage: "文字或 Logo 损坏/难读",
  person_or_product_distortion: "人物或商品明显变形",
  structure_bending: "建筑/结构线弯曲",
  layout_imbalance: "版式或视觉重心失衡",
  important_content_too_small: "重要内容缩得过小",
  visible_seam_or_artifact: "可见接缝、重影或伪影",
  wrong_target_composition: "目标比例下构图不成立",
  technical_failure: "技术失败或图片缺失",
  other: "其他问题（请写备注）",
};

const state = {
  workspace: null,
  currentIndex: 0,
  reviewerId: localStorage.getItem("retarget-reviewer-id") || "local-reviewer",
  draft: {},
  dirty: false,
  saving: false,
};

const el = (id) => document.getElementById(id);
const escapeHtml = (value) => String(value ?? "")
  .replaceAll("&", "&amp;")
  .replaceAll("<", "&lt;")
  .replaceAll(">", "&gt;")
  .replaceAll('"', "&quot;")
  .replaceAll("'", "&#039;");

function setError(message = "") {
  const banner = el("error-banner");
  banner.textContent = message;
  banner.classList.toggle("hidden", !message);
}

let toastTimer;
function toast(message, isError = false) {
  const node = el("toast");
  node.textContent = message;
  node.classList.toggle("error", isError);
  node.classList.add("visible");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => node.classList.remove("visible"), 3200);
}

function setSaveState(kind, text) {
  const node = el("save-state");
  node.className = `status-badge status-${kind}`;
  node.textContent = text;
}

function markDirty() {
  state.dirty = true;
  const key = draftStorageKey();
  if (key) localStorage.setItem(key, JSON.stringify(state.draft));
  setSaveState("dirty", "本机草稿已自动保存");
  updateValidationSummary();
}

function currentTask() {
  return state.workspace?.tasks[state.currentIndex] ?? null;
}

function draftStorageKey(task = currentTask()) {
  if (!state.workspace || !task) return "";
  return `retarget-review-draft-v2:${state.workspace.run_id}:${state.reviewerId}:${task.task_id}`;
}

function discardCurrentDraft() {
  const key = draftStorageKey();
  if (key) localStorage.removeItem(key);
  state.dirty = false;
}

function taskIsComplete(task) {
  return task.candidates.length > 0 && task.candidates.every((candidate) => candidate.review !== null);
}

function firstIncompleteIndex() {
  const index = state.workspace.tasks.findIndex((task) => !taskIsComplete(task));
  return index >= 0 ? index : Math.max(0, state.workspace.tasks.length - 1);
}

function buildDraft(task) {
  const draft = {};
  for (const candidate of task.candidates) {
    const review = candidate.review;
    draft[candidate.candidate_id] = {
      candidate_id: candidate.candidate_id,
      grade: review?.grade ?? "",
      is_best: review?.is_best ?? false,
      failure_reasons: [...(review?.failure_reasons ?? [])],
      dimension_grades: review?.dimension_grades?.map((item) => ({ ...item }))
        ?? state.workspace.review_dimensions.map((item) => ({
          dimension: item.dimension,
          grade: "",
          reason_codes: [],
        })),
      note: review?.note ?? "",
      display_order: 0,
    };
  }
  task.candidates.forEach((candidate, index) => { draft[candidate.candidate_id].display_order = index; });
  const key = draftStorageKey(task);
  if (key) {
    try {
      const persisted = JSON.parse(localStorage.getItem(key));
      const validGrades = new Set(["", "A", "B", "C", "D", "Skip"]);
      if (!persisted || !task.candidates.every((candidate) => persisted[candidate.candidate_id])) {
        localStorage.removeItem(key);
        return draft;
      }
      for (const candidate of task.candidates) {
        const saved = persisted[candidate.candidate_id];
        if (!validGrades.has(saved.grade) || !Array.isArray(saved.failure_reasons)) {
          throw new Error("invalid local draft");
        }
        draft[candidate.candidate_id] = {
          ...draft[candidate.candidate_id],
          grade: saved.grade,
          is_best: Boolean(saved.is_best),
          failure_reasons: saved.failure_reasons.filter((reason) => state.workspace.failure_reasons.includes(reason)),
          dimension_grades: Array.isArray(saved.dimension_grades)
            ? saved.dimension_grades.filter((entry) => state.workspace.review_dimensions.some((item) => item.dimension === entry.dimension))
            : draft[candidate.candidate_id].dimension_grades,
          note: typeof saved.note === "string" ? saved.note.slice(0, 2000) : "",
        };
      }
    } catch (_) {
      localStorage.removeItem(key);
    }
  }
  return draft;
}

function confirmDiscard() {
  return !state.dirty || window.confirm("当前任务有未提交的本机草稿。确定放弃这份草稿吗？");
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: { "Content-Type": "application/json", ...(options.headers ?? {}) },
  });
  if (!response.ok) {
    let detail = `请求失败（HTTP ${response.status}）`;
    try {
      const body = await response.json();
      if (typeof body.detail === "string") detail = body.detail;
      else if (Array.isArray(body.detail)) detail = body.detail.map((item) => item.msg).join("；");
    } catch (_) { /* keep status text */ }
    throw new Error(detail);
  }
  return response.json();
}

async function loadWorkspace({ preserveIndex = false } = {}) {
  setError();
  el("loading-panel").classList.remove("hidden");
  el("workspace").classList.add("hidden");
  el("save-bar").classList.add("hidden");
  try {
    const reviewer = state.reviewerId.trim();
    if (!/^[a-z0-9][a-z0-9_-]*$/.test(reviewer)) {
      throw new Error("Reviewer ID 只能使用小写字母、数字、连字符或下划线。");
    }
    const previousIndex = state.currentIndex;
    state.workspace = await api(`/v1/review-workspace?reviewer_id=${encodeURIComponent(reviewer)}`);
    if (!state.workspace.task_count) throw new Error("该 Run 没有可评审任务。");
    state.currentIndex = preserveIndex ? Math.min(previousIndex, state.workspace.task_count - 1) : firstIncompleteIndex();
    localStorage.setItem("retarget-reviewer-id", reviewer);
    renderWorkspace();
  } catch (error) {
    setError(`无法载入评审工作区：${error.message}`);
    setSaveState("neutral", "载入失败");
  } finally {
    el("loading-panel").classList.add("hidden");
  }
}

function renderProgress() {
  const { completed_task_count: complete, task_count: count } = state.workspace;
  const percent = count ? Math.round((complete / count) * 100) : 0;
  el("progress-count").textContent = `${complete} / ${count}`;
  el("progress-percent").textContent = `${percent}% 已完成`;
  el("progress-fill").style.width = `${percent}%`;
  const track = document.querySelector(".progress-track");
  track.setAttribute("aria-valuenow", String(percent));
}

function renderTaskNavigation() {
  const select = el("task-select");
  select.innerHTML = state.workspace.tasks.map((task, index) => {
    const done = taskIsComplete(task) ? "✓" : "○";
    return `<option value="${index}">${done} ${index + 1}. ${escapeHtml(task.task_id)}</option>`;
  }).join("");
  select.value = String(state.currentIndex);
  select.disabled = false;
  el("previous-task").disabled = state.currentIndex === 0;
  el("next-task").disabled = state.currentIndex >= state.workspace.task_count - 1;
  el("next-incomplete").disabled = state.workspace.tasks.every(taskIsComplete);
}

function imageCard({ title, caption, imageUrl, downloadUrl, dialogTitle, unavailable }) {
  const image = imageUrl
    ? `<img src="${escapeHtml(imageUrl)}" alt="${escapeHtml(dialogTitle)}" loading="eager">
       <button class="image-open" type="button" data-preview-url="${escapeHtml(imageUrl)}"
         data-download-url="${escapeHtml(downloadUrl)}" data-preview-title="${escapeHtml(dialogTitle)}">全屏查看原图 ⛶</button>`
    : `<div class="missing-image"><strong>图片不可用</strong><p>${escapeHtml(unavailable ?? "没有图片输出")}</p></div>`;
  return `<article class="image-card">
    <header class="image-card-header"><div><h3>${escapeHtml(title)}</h3><p>${escapeHtml(caption)}</p></div></header>
    <div class="image-stage">${image}</div>
  </article>`;
}

function renderReferences(task) {
  const top = task.candidates.find((candidate) => candidate.candidate_id === task.technical_top_candidate_id);
  const sourceCard = imageCard({
    title: "源图 · 内容基准",
    caption: `${task.source.width}×${task.source.height} · 场景：${task.source.scene_category}`,
    imageUrl: task.source_url,
    downloadUrl: task.source_download_url,
    dialogTitle: `源图 · ${task.source.source_id}`,
  });
  const topCard = top ? imageCard({
    title: `技术 Top-1 · ${top.method_id}`,
    caption: "未经过人工评分校准，请独立判断，不要把它当标准答案",
    imageUrl: top.image_url,
    downloadUrl: top.download_url,
    dialogTitle: `技术 Top-1 · ${top.method_id}`,
    unavailable: top.error_summary,
  }) : imageCard({
    title: "技术 Top-1 · 无可用结果",
    caption: "当前 Selector 没有给出成功候选",
    unavailable: "没有成功生成技术 Top-1",
  });
  const preview = task.pre_review;
  const previewTop = preview
    ? task.candidates.find((candidate) => candidate.candidate_id === preview.selected_candidate_id)
    : null;
  const previewCard = previewTop ? imageCard({
    title: `大模型预审 · ${previewTop.method_id}`,
    caption: preview.agent_called
      ? "已结合源图语义、候选像素与传统指标；仅作评审辅助，可人工覆盖"
      : "本任务未触发大模型调用，显示安全回退结果；仅作评审辅助",
    imageUrl: previewTop.image_url,
    downloadUrl: previewTop.download_url,
    dialogTitle: `大模型预审 · ${previewTop.method_id}`,
    unavailable: previewTop.error_summary,
  }) : "";
  el("reference-grid").innerHTML = sourceCard + topCard + previewCard;
}

function reasonOptions(candidateId, selected, disabled) {
  return state.workspace.failure_reasons.map((reason) => {
    const id = `reason-${candidateId}-${reason}`;
    return `<span class="reason-option">
      <input id="${escapeHtml(id)}" type="checkbox" name="reason-${escapeHtml(candidateId)}"
        value="${escapeHtml(reason)}" data-candidate-id="${escapeHtml(candidateId)}"
        ${selected.includes(reason) ? "checked" : ""} ${disabled ? "disabled" : ""}>
      <label for="${escapeHtml(id)}">${escapeHtml(REASON_LABELS[reason] ?? reason)}</label>
    </span>`;
  }).join("");
}

function dimensionControls(candidateId, values, disabled) {
  const byDimension = Object.fromEntries(values.map((item) => [item.dimension, item.grade]));
  return state.workspace.review_dimensions.map((item) => {
    const options = ["", "A", "B", "C", "D"].map((grade) =>
      `<option value="${grade}" ${byDimension[item.dimension] === grade ? "selected" : ""}>${grade || "请选择"}</option>`
    ).join("");
    return `<label class="dimension-control">
      <span>${escapeHtml(item.label)}</span>
      <select data-dimension-candidate-id="${escapeHtml(candidateId)}"
        data-dimension-id="${escapeHtml(item.dimension)}" ${disabled ? "disabled" : ""}>${options}</select>
    </label>`;
  }).join("");
}

function candidateCard(candidate, index) {
  const draft = state.draft[candidate.candidate_id];
  const statusClass = candidate.generation_status === "SUCCESS" ? "" : " failed";
  const image = candidate.image_url
    ? `<img src="${escapeHtml(candidate.image_url)}" alt="${escapeHtml(candidate.method_id)} 候选图" loading="lazy">
       <button class="image-open" type="button" data-preview-url="${escapeHtml(candidate.image_url)}"
         data-download-url="${escapeHtml(candidate.download_url)}" data-preview-title="候选 ${index + 1} · ${escapeHtml(candidate.method_id)}">全屏查看原图 ⛶</button>`
    : `<div class="missing-image"><strong>候选生成失败</strong><p>${escapeHtml(candidate.error_summary ?? "没有图片输出")}</p></div>`;
  const grades = Object.keys(GRADE_HELP).map((grade) => {
    const id = `grade-${candidate.candidate_id}-${grade}`;
    return `<span class="grade-option"><input id="${escapeHtml(id)}" type="radio"
      name="grade-${escapeHtml(candidate.candidate_id)}" value="${grade}"
      data-candidate-id="${escapeHtml(candidate.candidate_id)}" ${draft.grade === grade ? "checked" : ""}>
      <label for="${escapeHtml(id)}" data-grade="${grade}" title="${escapeHtml(GRADE_HELP[grade])}">${grade}</label></span>`;
  }).join("");
  const eligibleBest = ["A", "B"].includes(draft.grade);
  return `<article class="candidate-card" data-candidate-card="${escapeHtml(candidate.candidate_id)}">
    <header class="candidate-header">
      <div><span class="candidate-number">候选 ${index + 1}</span><h3>${escapeHtml(candidate.method_id)}</h3></div>
      <span class="candidate-status${statusClass}">${escapeHtml(candidate.generation_status)}</span>
    </header>
    <div class="image-stage">${image}</div>
    <div class="candidate-tools"><span>原尺寸 ${candidate.target_width}×${candidate.target_height} PNG</span>
      ${candidate.download_url ? `<a href="${escapeHtml(candidate.download_url)}" download>下载高清原图 ↓</a>` : ""}</div>
    <div class="review-fields">
      <div><div class="field-label"><span>质量等级</span><span class="required">必选</span></div><div class="grade-options">${grades}</div></div>
      <fieldset class="dimension-fieldset" ${draft.grade && draft.grade !== "Skip" ? "" : "disabled"}>
        <legend class="field-label"><span>六项细分等级</span><span class="required">非 Skip 必填</span></legend>
        <div class="dimension-grid">${dimensionControls(candidate.candidate_id, draft.dimension_grades, !draft.grade || draft.grade === "Skip")}</div>
      </fieldset>
      <fieldset class="reason-fieldset" ${["B", "C", "D"].includes(draft.grade) ? "" : "disabled"}>
        <legend class="field-label"><span>问题原因</span><span class="required">C / D 必选，B 建议填写</span></legend>
        <div class="reason-options">${reasonOptions(candidate.candidate_id, draft.failure_reasons, !["B", "C", "D"].includes(draft.grade))}</div>
      </fieldset>
      <div class="note-field"><label for="note-${escapeHtml(candidate.candidate_id)}">评审备注（可选）</label>
        <textarea id="note-${escapeHtml(candidate.candidate_id)}" maxlength="2000"
          data-note-candidate-id="${escapeHtml(candidate.candidate_id)}"
          placeholder="例如：标题可读，但右侧商品被压窄；人物脸部正常。">${escapeHtml(draft.note)}</textarea></div>
      <label class="best-toggle${eligibleBest ? "" : " disabled"}">
        <input type="radio" name="card-best" value="${escapeHtml(candidate.candidate_id)}"
          data-best-candidate-id="${escapeHtml(candidate.candidate_id)}" ${draft.is_best ? "checked" : ""}
          ${eligibleBest ? "" : "disabled"}>
        设为本任务最佳候选（仅 A / B）
      </label>
    </div>
  </article>`;
}

function renderCandidates(task) {
  el("candidate-grid").innerHTML = task.candidates.map(candidateCard).join("");
  renderBestSelect(task);
}

function renderBestSelect(task) {
  const select = el("best-select");
  const eligible = task.candidates.filter((candidate) => ["A", "B"].includes(state.draft[candidate.candidate_id].grade));
  const selected = Object.values(state.draft).find((item) => item.is_best)?.candidate_id ?? "";
  select.innerHTML = `<option value="">不选择最佳候选</option>` + eligible.map((candidate, index) =>
    `<option value="${escapeHtml(candidate.candidate_id)}">候选 ${task.candidates.indexOf(candidate) + 1} · ${escapeHtml(candidate.method_id)} · ${state.draft[candidate.candidate_id].grade}</option>`
  ).join("");
  select.value = eligible.some((item) => item.candidate_id === selected) ? selected : "";
}

function renderTask() {
  const task = currentTask();
  state.draft = buildDraft(task);
  state.dirty = Boolean(localStorage.getItem(draftStorageKey(task)));
  el("task-position").textContent = `任务 ${state.currentIndex + 1} / ${state.workspace.task_count}`;
  el("task-title").textContent = task.task_id;
  el("task-meta").textContent = `源图 ${task.source.source_id} · 场景 ${task.source.scene_category} · Selector ${task.selector_id} · 方法名可见（非盲评）`;
  el("target-size").textContent = `${task.target.width} × ${task.target.height}`;
  el("target-ratio").textContent = `宽高比 ${(task.target.width / task.target.height).toFixed(2)} : 1`;
  renderTaskNavigation();
  renderReferences(task);
  renderCandidates(task);
  if (state.dirty) setSaveState("dirty", "已恢复本机草稿");
  else setSaveState(taskIsComplete(task) ? "saved" : "neutral", taskIsComplete(task) ? "本任务已提交" : "本任务未完成");
  updateValidationSummary();
  window.scrollTo({ top: 0, behavior: "smooth" });
}

function renderWorkspace() {
  const workspace = state.workspace;
  el("run-meta").textContent = `Run ${workspace.run_id} · ${workspace.task_count} 个任务 · ${workspace.run_status} · 五条候选路线名称可见`;
  el("reviewer-id").value = state.reviewerId;
  renderProgress();
  renderTask();
  el("workspace").classList.remove("hidden");
  el("save-bar").classList.remove("hidden");
}

function validationErrors() {
  const task = currentTask();
  const errors = [];
  for (const candidate of task.candidates) {
    const item = state.draft[candidate.candidate_id];
    if (!item.grade) errors.push(`${candidate.method_id} 尚未评分`);
    if (["C", "D"].includes(item.grade) && item.failure_reasons.length === 0) errors.push(`${candidate.method_id} 的 C/D 级缺少问题原因`);
    if (["A", "Skip"].includes(item.grade) && item.failure_reasons.length) errors.push(`${candidate.method_id} 的问题原因只适用于 B/C/D`);
    if (item.grade !== "Skip" && item.grade && item.dimension_grades.some((entry) => !entry.grade)) errors.push(`${candidate.method_id} 的六项细分等级未完成`);
    if (item.grade === "Skip" && item.dimension_grades.some((entry) => entry.grade)) errors.push(`${candidate.method_id} 已 Skip，不能保留细分等级`);
    if (item.is_best && !["A", "B"].includes(item.grade)) errors.push(`${candidate.method_id} 不是 A/B，不能设为最佳`);
  }
  if (Object.values(state.draft).filter((item) => item.is_best).length > 1) errors.push("最多只能选择一个最佳候选");
  return errors;
}

function updateValidationSummary() {
  if (!state.workspace) return;
  const scored = Object.values(state.draft).filter((item) => item.grade).length;
  const total = Object.keys(state.draft).length;
  const errors = validationErrors();
  el("completion-summary").textContent = `${scored} / ${total} 张已评分`;
  el("validation-summary").textContent = errors.length ? errors[0] : "评分完整，可以安全保存";
  el("save-only").disabled = state.saving || errors.length > 0;
  el("save-next").disabled = state.saving || errors.length > 0;
}

function setBest(candidateId) {
  for (const item of Object.values(state.draft)) item.is_best = item.candidate_id === candidateId && Boolean(candidateId);
  document.querySelectorAll("[data-best-candidate-id]").forEach((node) => { node.checked = node.value === candidateId; });
  el("best-select").value = candidateId;
  markDirty();
}

function handleGradeChange(input) {
  const candidateId = input.dataset.candidateId;
  const item = state.draft[candidateId];
  item.grade = input.value;
  if (!["B", "C", "D"].includes(item.grade)) item.failure_reasons = [];
  if (item.grade === "Skip") {
    item.dimension_grades = item.dimension_grades.map((entry) => ({ ...entry, grade: "" }));
  } else {
    item.dimension_grades = item.dimension_grades.map((entry) => ({
      ...entry,
      grade: entry.grade || item.grade,
    }));
  }
  if (!["A", "B"].includes(item.grade)) item.is_best = false;
  markDirty();
  renderCandidates(currentTask());
}

function changeTask(index) {
  if (index === state.currentIndex) return;
  if (!confirmDiscard()) {
    el("task-select").value = String(state.currentIndex);
    return;
  }
  discardCurrentDraft();
  state.currentIndex = Math.max(0, Math.min(index, state.workspace.task_count - 1));
  renderTask();
}

async function saveCurrent(advance) {
  const errors = validationErrors();
  if (errors.length) {
    toast(errors[0], true);
    const card = currentTask().candidates.find((candidate) => errors[0].startsWith(candidate.method_id));
    if (card) document.querySelector(`[data-candidate-card="${CSS.escape(card.candidate_id)}"]`)?.scrollIntoView({ behavior: "smooth", block: "center" });
    return;
  }
  state.saving = true;
  updateValidationSummary();
  setSaveState("neutral", "正在保存…");
  try {
    const task = currentTask();
    await api("/v1/reviews", {
      method: "POST",
      body: JSON.stringify({ reviewer_id: state.reviewerId, task_id: task.task_id, reviews: Object.values(state.draft) }),
    });
    localStorage.removeItem(draftStorageKey(task));
    const oldIndex = state.currentIndex;
    state.dirty = false;
    state.workspace = await api(`/v1/review-workspace?reviewer_id=${encodeURIComponent(state.reviewerId)}`);
    state.currentIndex = advance ? Math.min(oldIndex + 1, state.workspace.task_count - 1) : oldIndex;
    renderProgress();
    renderTask();
    toast(advance && oldIndex < state.workspace.task_count - 1 ? "评分已追加保存，已进入下一任务。" : "评分已追加保存。历史记录未被覆盖。");
  } catch (error) {
    setSaveState("dirty", "保存失败 · 修改仍在本页");
    toast(`保存失败：${error.message}`, true);
  } finally {
    state.saving = false;
    updateValidationSummary();
  }
}

function openPreview(button) {
  el("dialog-title").textContent = button.dataset.previewTitle;
  el("dialog-image").src = button.dataset.previewUrl;
  el("dialog-image").alt = `${button.dataset.previewTitle} 原始分辨率预览`;
  el("dialog-download").href = button.dataset.downloadUrl;
  el("image-dialog").showModal();
}

document.addEventListener("change", (event) => {
  const target = event.target;
  if (target.matches("input[type=radio][data-candidate-id]")) handleGradeChange(target);
  if (target.matches("input[type=checkbox][data-candidate-id]")) {
    const item = state.draft[target.dataset.candidateId];
    item.failure_reasons = [...document.querySelectorAll(`input[type=checkbox][data-candidate-id="${CSS.escape(target.dataset.candidateId)}"]:checked`)].map((node) => node.value);
    markDirty();
  }
  if (target.matches("select[data-dimension-candidate-id]")) {
    const item = state.draft[target.dataset.dimensionCandidateId];
    const entry = item.dimension_grades.find((value) => value.dimension === target.dataset.dimensionId);
    if (entry) entry.grade = target.value;
    markDirty();
  }
  if (target.matches("[data-best-candidate-id]")) setBest(target.value);
  if (target === el("best-select")) setBest(target.value);
  if (target === el("task-select")) changeTask(Number(target.value));
});

document.addEventListener("input", (event) => {
  const target = event.target;
  if (target.matches("[data-note-candidate-id]")) {
    state.draft[target.dataset.noteCandidateId].note = target.value;
    markDirty();
  }
});

document.addEventListener("click", (event) => {
  const preview = event.target.closest("[data-preview-url]");
  if (preview) openPreview(preview);
});

el("reviewer-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!confirmDiscard()) return;
  discardCurrentDraft();
  state.reviewerId = el("reviewer-id").value.trim();
  await loadWorkspace();
});
el("previous-task").addEventListener("click", () => changeTask(state.currentIndex - 1));
el("next-task").addEventListener("click", () => changeTask(state.currentIndex + 1));
el("next-incomplete").addEventListener("click", () => {
  const next = state.workspace.tasks.findIndex((task, index) => index > state.currentIndex && !taskIsComplete(task));
  const fallback = state.workspace.tasks.findIndex((task) => !taskIsComplete(task));
  changeTask(next >= 0 ? next : fallback);
});
el("save-only").addEventListener("click", () => saveCurrent(false));
el("save-next").addEventListener("click", () => saveCurrent(true));
el("dialog-close").addEventListener("click", () => el("image-dialog").close());
el("image-dialog").addEventListener("click", (event) => { if (event.target === el("image-dialog")) el("image-dialog").close(); });
window.addEventListener("keydown", (event) => { if (event.key === "Escape" && el("image-dialog").open) el("image-dialog").close(); });
el("reviewer-id").value = state.reviewerId;
loadWorkspace();
