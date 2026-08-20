"use strict";

const ISSUE_LABELS = {
  directly_usable: "可直接上传",
  core_content_preserved: "主体和信息完整",
  natural_layout: "排版自然美观",
  minor_nonblocking_issue: "有轻微问题但不影响上传",
  critical_content_missing: "关键内容缺失",
  text_damage: "文字缺失或变形",
  face_body_distortion: "人物脸或身体变形",
  global_stretch: "全局拉伸明显",
  local_deformation: "局部扭曲或接缝",
  structure_damage: "结构线或商品变形",
  composition_problem: "构图或留白影响使用",
  aigc_semantic_change: "AIGC改写主体或文字",
  other: "其他",
};

const state = {
  mode: "all60",
  workspace: null,
  index: 0,
  reviewerId: localStorage.getItem("movie60-reviewer-id") || "local-reviewer",
  draft: {},
  dirty: false,
};

const el = (id) => document.getElementById(id);
const escapeHtml = (value) => String(value ?? "")
  .replaceAll("&", "&amp;")
  .replaceAll("<", "&lt;")
  .replaceAll(">", "&gt;")
  .replaceAll('"', "&quot;")
  .replaceAll("'", "&#039;");

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
  });
  if (!response.ok) {
    let message = `请求失败（HTTP ${response.status}）`;
    try {
      const body = await response.json();
      message = typeof body.detail === "string"
        ? body.detail
        : body.detail.map((item) => item.msg).join("；");
    } catch (_) { /* keep HTTP message */ }
    throw new Error(message);
  }
  return response.json();
}

function currentTask() {
  return state.workspace?.tasks[state.index] || null;
}

function taskComplete(task) {
  const available = task.candidates.filter((candidate) => candidate.available);
  return available.length > 0 && available.every((candidate) => candidate.review !== null);
}

function draftKey(task = currentTask()) {
  if (!task) return "";
  return `movie60-review-v1:${state.mode}:${state.reviewerId}:${task.task_id}`;
}

function buildDraft(task) {
  const draft = {};
  for (const candidate of task.candidates.filter((item) => item.available)) {
    draft[candidate.route] = {
      route: candidate.route,
      grade: candidate.review?.grade || "",
      reason: candidate.review?.reason || "",
      issue_codes: [...(candidate.review?.issue_codes || [])],
    };
  }
  try {
    const stored = JSON.parse(localStorage.getItem(draftKey(task)) || "null");
    if (stored && Object.keys(draft).every((route) => stored[route])) return stored;
  } catch (_) { /* use server data */ }
  return draft;
}

function setDirty() {
  state.dirty = true;
  localStorage.setItem(draftKey(), JSON.stringify(state.draft));
  el("save-state").textContent = "本机草稿已保存";
  el("save-state").className = "save-state dirty";
  updateValidation();
}

function toast(message) {
  const node = el("toast");
  node.textContent = message;
  node.classList.add("visible");
  setTimeout(() => node.classList.remove("visible"), 2600);
}

function confirmDiscard() {
  return !state.dirty || window.confirm("当前任务有未提交草稿，确定离开吗？");
}

async function loadMode(mode, preserveIndex = false) {
  if (!confirmDiscard()) return;
  state.mode = mode;
  state.dirty = false;
  el("loading").classList.remove("hidden");
  el("workspace").classList.add("hidden");
  el("save-bar").classList.add("hidden");
  el("error").classList.add("hidden");
  document.querySelectorAll(".mode-button").forEach((button) => {
    button.classList.toggle("active", button.dataset.mode === mode);
  });
  try {
    const previous = state.index;
    state.workspace = await api(`/v1/workspace?mode=${mode}`);
    const incomplete = state.workspace.tasks.findIndex((task) => !taskComplete(task));
    state.index = preserveIndex
      ? Math.min(previous, state.workspace.task_count - 1)
      : (incomplete >= 0 ? incomplete : 0);
    render();
  } catch (error) {
    el("error").textContent = `无法载入：${error.message}`;
    el("error").classList.remove("hidden");
  } finally {
    el("loading").classList.add("hidden");
  }
}

function renderProgress() {
  const completed = state.workspace.tasks.filter(taskComplete).length;
  const total = state.workspace.task_count;
  const percent = total ? Math.round(completed / total * 100) : 0;
  el("progress-count").textContent = `${completed} / ${total}`;
  el("progress-bar").style.width = `${percent}%`;
  el("progress-text").textContent = `${percent}% 已完成`;
}

function renderNavigation() {
  el("task-select").innerHTML = state.workspace.tasks.map((task, index) =>
    `<option value="${index}">${taskComplete(task) ? "✓" : "○"} ${index + 1}. ${escapeHtml(task.task_id)}</option>`
  ).join("");
  el("task-select").value = String(state.index);
  el("previous").disabled = state.index === 0;
  el("next").disabled = state.index === state.workspace.task_count - 1;
  el("next-incomplete").disabled = state.workspace.tasks.every(taskComplete);
}

function issueOptions(route, selected) {
  return Object.entries(ISSUE_LABELS).map(([code, label]) => {
    const id = `issue-${route}-${code}`;
    return `<span class="issue-option"><input id="${id}" type="checkbox"
      data-route="${route}" data-issue="${code}" ${selected.includes(code) ? "checked" : ""}>
      <label for="${id}">${escapeHtml(label)}</label></span>`;
  }).join("");
}

function gradeOptions(route, current) {
  return ["A", "B", "C", "D"].map((grade) => {
    const id = `grade-${route}-${grade}`;
    return `<span class="grade-option grade-${grade.toLowerCase()}">
      <input id="${id}" type="radio" name="grade-${route}" value="${grade}"
        data-route="${route}" ${current === grade ? "checked" : ""}>
      <label for="${id}">${grade}</label></span>`;
  }).join("");
}

function machineEvidence(candidate) {
  if (candidate.rule_reason) {
    const ruleScore = Number(candidate.machine_score).toFixed(2);
    const agentGrade = candidate.agent_grade || "未独立判级";
    const agentRank = candidate.agent_rank ? `${candidate.agent_rank}/7` : "无";
    const selected = candidate.final_selected
      ? '<span class="selection-badge">当前最终选择</span>'
      : "";
    const agentCodes = (candidate.agent_reason_codes || []).length
      ? `<details class="reason-codes"><summary>查看 Agent 决策代码</summary><code>${escapeHtml(candidate.agent_reason_codes.join(" · "))}</code></details>`
      : "";
    const modelAdvice = candidate.model_advice_grade
      ? `<p class="evidence-score"><b>${escapeHtml(candidate.model_advice_grade)}</b> · ${escapeHtml(candidate.model_advice_scope)}</p>
        <p>${escapeHtml(candidate.model_advice_reason)}</p>`
      : `<p class="evidence-score"><b>待复核</b></p>
        <p>该候选尚未经过大模型高清人工式复核，不用 Rule 或 Agent 结果冒充建议。</p>`;
    return `<section class="machine-evidence">
      <div class="evidence-column rule-evidence">
        <div class="evidence-title"><h4>Rule 判分</h4><span>第 ${candidate.rule_rank}/7 名</span></div>
        <p class="evidence-score"><b>${ruleScore}</b> / 100 · 等级 ${escapeHtml(candidate.machine_grade)}</p>
        <p>${escapeHtml(candidate.rule_reason)}</p>
      </div>
      <div class="evidence-column agent-evidence">
        <div class="evidence-title"><h4>Agent 判分</h4>${selected}</div>
        <p class="evidence-score"><b>${escapeHtml(agentGrade)}</b> · 排名 ${escapeHtml(agentRank)} · ${escapeHtml(candidate.agent_review_scope)}</p>
        <p>${escapeHtml(candidate.agent_reason)}</p>${agentCodes}
        <p class="evidence-note">Agent 没有虚构连续分数：只有进入高清复核的候选才有 A/B/C/D；其余仅显示七候选总览排名。</p>
      </div>
      <div class="evidence-column model-evidence">
        <div class="evidence-title"><h4>大模型复核建议</h4></div>
        ${modelAdvice}
        <p class="evidence-note">这是辅助建议，不是人工金标；最终以你的高清人工评分和理由为准。</p>
      </div>
    </section>`;
  }
  if (candidate.review_rationale) {
    return `<section class="machine-evidence compact-evidence"><div class="evidence-column">
      <div class="evidence-title"><h4>现有机器与大模型复核依据</h4></div>
      <p>${escapeHtml(candidate.review_rationale)}</p>
    </div></section>`;
  }
  return "";
}

function candidateCard(candidate) {
  if (!candidate.available) {
    return `<article class="candidate-card unavailable">
      <div class="candidate-header"><div><h3>${escapeHtml(candidate.title)}</h3>
      <p class="candidate-meta">视觉等级 N/A</p></div></div>
      <div class="unavailable-panel"><div><b>没有AIGC图片</b><p>${escapeHtml(candidate.status_text)}</p>
      <p>这是API技术失败，不按图像质量判C。</p></div></div></article>`;
  }
  const draft = state.draft[candidate.route];
  const badges = [
    `方法 ${candidate.method}`,
    candidate.machine_score !== undefined
      ? `Rule ${Number(candidate.machine_score).toFixed(2)} / ${candidate.machine_grade}`
      : `旧机器 ${candidate.machine_grade}`,
    candidate.codex_grade
      ? `${candidate.rule_reason ? "Agent高清" : "大模型建议"} ${candidate.codex_grade}`
      : null,
    candidate.final_selected ? "当前最终选择" : null,
  ].filter(Boolean).map((text) => `<span class="machine-badge">${escapeHtml(text)}</span>`).join("");
  const nativeLink = candidate.native_url
    ? `<a class="native-link" href="${candidate.native_url}" target="_blank" rel="noreferrer">打开API原生2K图 →</a>`
    : "";
  return `<article class="candidate-card" data-route="${candidate.route}">
    <div class="candidate-header"><div><h3>${escapeHtml(candidate.title)}</h3>
    <p class="candidate-meta">${escapeHtml(candidate.status_text)}</p></div><div class="score-badges">${badges}</div></div>
    <a href="${candidate.image_url}" target="_blank" rel="noreferrer"><img class="candidate-image"
      src="${candidate.image_url}" alt="${escapeHtml(candidate.title)}"></a>${nativeLink}
    ${machineEvidence(candidate)}
    <div class="review-form">
      <fieldset><legend>你的人工等级</legend><div class="grade-buttons">${gradeOptions(candidate.route, draft.grade)}</div></fieldset>
      <fieldset><legend>评价要点（可多选）</legend><div class="issue-options">${issueOptions(candidate.route, draft.issue_codes)}</div></fieldset>
      <label class="reason-label" for="reason-${candidate.route}">你的人工理由（必填）</label>
      <textarea id="reason-${candidate.route}" data-reason-route="${candidate.route}"
        placeholder="例如：建议给A，原因：主体自然且均保留，文字无问题；即便背景略有裁切，也不影响整体上传，不应仅因画布比例变化给C。">${escapeHtml(draft.reason)}</textarea>
      <p class="reason-help">请写清楚：保留了什么、存在什么问题、为什么影响或不影响上传。</p>
    </div></article>`;
}

function render() {
  const task = currentTask();
  state.draft = buildDraft(task);
  state.dirty = false;
  renderProgress();
  renderNavigation();
  el("mode-description").textContent = state.mode === "all60"
    ? "完整60张：逐张校对七种候选，共420张；同时核对Rule与Agent判分。"
    : "重点20张：分别评价Rule、Agent和成功回图的AIGC。";
  el("candidate-heading").textContent = state.mode === "all60" ? "全部七种候选" : "Rule / Agent / AIGC";
  el("task-position").textContent = `任务 ${state.index + 1} / ${state.workspace.task_count}`;
  el("task-title").textContent = task.task_id;
  el("task-meta").textContent = `${task.scene_category} · ${task.split}`;
  el("source-image").src = task.source_url;
  el("source-link").href = task.source_url;
  el("comparison-link").href = task.comparison_url;
  el("candidate-grid").innerHTML = task.candidates.map(candidateCard).join("");
  el("workspace").classList.remove("hidden");
  el("save-bar").classList.remove("hidden");
  el("save-state").textContent = taskComplete(task) ? "已提交" : "尚未提交";
  el("save-state").className = taskComplete(task) ? "save-state saved" : "save-state";
  bindReviewInputs();
  updateValidation();
}

function bindReviewInputs() {
  document.querySelectorAll("input[data-route]").forEach((input) => {
    input.addEventListener("change", () => {
      const draft = state.draft[input.dataset.route];
      if (input.type === "radio") draft.grade = input.value;
      else {
        draft.issue_codes = [...document.querySelectorAll(`input[data-route="${input.dataset.route}"][data-issue]:checked`)]
          .map((item) => item.dataset.issue);
      }
      setDirty();
    });
  });
  document.querySelectorAll("textarea[data-reason-route]").forEach((textarea) => {
    textarea.addEventListener("input", () => {
      state.draft[textarea.dataset.reasonRoute].reason = textarea.value.slice(0, 2000);
      setDirty();
    });
  });
}

function validation() {
  const reviews = Object.values(state.draft);
  if (reviews.some((item) => !["A", "B", "C", "D"].includes(item.grade))) {
    return { valid: false, message: "请为每条有图路线选择A/B/C/D" };
  }
  if (reviews.some((item) => item.reason.trim().length < 3)) {
    return { valid: false, message: "请为每条路线填写具体人工理由" };
  }
  return { valid: true, message: `${reviews.length}条路线已完整填写` };
}

function updateValidation() {
  const result = validation();
  el("completion").textContent = result.valid ? "当前任务可以提交" : "当前任务尚未完成";
  el("validation").textContent = result.message;
  el("save").disabled = !result.valid;
  el("save-next").disabled = !result.valid;
}

async function save(moveNext) {
  const result = validation();
  if (!result.valid) return;
  const task = currentTask();
  try {
    await api("/v1/reviews", {
      method: "POST",
      body: JSON.stringify({
        mode: state.mode,
        reviewer_id: state.reviewerId,
        task_id: task.task_id,
        reviews: Object.values(state.draft),
      }),
    });
    localStorage.removeItem(draftKey(task));
    state.dirty = false;
    const nextIndex = moveNext ? Math.min(state.index + 1, state.workspace.task_count - 1) : state.index;
    state.workspace = await api(`/v1/workspace?mode=${state.mode}`);
    state.index = nextIndex;
    render();
    toast("人工评分与理由已写入CSV");
  } catch (error) {
    toast(`保存失败：${error.message}`);
  }
}

function navigate(index) {
  if (!confirmDiscard()) return;
  state.index = Math.max(0, Math.min(index, state.workspace.task_count - 1));
  render();
  window.scrollTo({ top: 0, behavior: "smooth" });
}

document.querySelectorAll(".mode-button").forEach((button) => {
  button.addEventListener("click", () => loadMode(button.dataset.mode));
});
el("task-select").addEventListener("change", (event) => navigate(Number(event.target.value)));
el("previous").addEventListener("click", () => navigate(state.index - 1));
el("next").addEventListener("click", () => navigate(state.index + 1));
el("next-incomplete").addEventListener("click", () => {
  const found = state.workspace.tasks.findIndex((task, index) => index > state.index && !taskComplete(task));
  const fallback = state.workspace.tasks.findIndex((task) => !taskComplete(task));
  if (found >= 0 || fallback >= 0) navigate(found >= 0 ? found : fallback);
});
el("save").addEventListener("click", () => save(false));
el("save-next").addEventListener("click", () => save(true));
el("reload").addEventListener("click", () => {
  const reviewer = el("reviewer-id").value.trim();
  if (!/^[a-z0-9][a-z0-9_-]*$/.test(reviewer)) {
    toast("评审者ID只能使用小写字母、数字、-或_");
    return;
  }
  state.reviewerId = reviewer;
  localStorage.setItem("movie60-reviewer-id", reviewer);
  loadMode(state.mode, true);
});

el("reviewer-id").value = state.reviewerId;
window.addEventListener("beforeunload", (event) => {
  if (!state.dirty) return;
  event.preventDefault();
  event.returnValue = "";
});
loadMode("all60");
