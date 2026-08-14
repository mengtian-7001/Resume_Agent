/** Group Agent 链 steps by candidate so parallel resumes stay independent. */

const SHARED_KEY = "shared";
const SHARED_NAME = "共用";

const JOB_STEP_IDS = new Set([
  "parse_jd.extract",
  "jd_research",
  "fact_graph.job",
  "match.start",
  "pipeline.completed",
]);

const TOOLISH_NAME = /^(score_deterministic|llm_judge|generate_questions|web_research|retrieve_memory|tool|core)$/i;
const CANDIDATE_LABEL_RE = /^(Construction|Checker|ReAct Plan|Act\+Observe|Reflect|Decision \+ Generate|按 Checker 修正|落库|写入候选人事实图)/;

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, (character) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#039;",
  }[character]));
}

function baseStepId(stepId) {
  const id = String(stepId || "");
  if (JOB_STEP_IDS.has(id)) return id;
  const parts = id.split(".");
  if (parts.length < 2) return id;
  const withoutTail = parts.slice(0, -1).join(".");
  return JOB_STEP_IDS.has(withoutTail) ? withoutTail : id;
}

function nameFromLabel(label) {
  const cleaned = String(label || "").replace(/（按 Checker 反馈修正）$/, "").trim();
  if (!CANDIDATE_LABEL_RE.test(cleaned)) return "";
  const idx = cleaned.lastIndexOf(" · ");
  if (idx < 0) return "";
  const name = cleaned.slice(idx + 3).trim();
  if (!name || TOOLISH_NAME.test(name)) return "";
  return name;
}

function nameFromDetail(detail) {
  const match = String(detail || "").match(/(?:^|\s)name=([^\s]+)/);
  return match?.[1] ? String(match[1]).trim() : "";
}

export function agentChainCandidateOf(step) {
  if (!step || typeof step !== "object") {
    return { key: SHARED_KEY, name: SHARED_NAME };
  }
  if (JOB_STEP_IDS.has(String(step.id || "")) || JOB_STEP_IDS.has(baseStepId(step.id))) {
    return { key: SHARED_KEY, name: SHARED_NAME };
  }
  const candidateId = String(step.candidate_id || "").trim();
  const candidateName = String(step.candidate_name || "").trim();
  if (candidateName) {
    return { key: `name:${candidateName}`, name: candidateName };
  }
  if (candidateId) {
    return { key: `id:${candidateId}`, name: candidateId };
  }
  const fromLabel = nameFromLabel(step.label);
  if (fromLabel) {
    return { key: `name:${fromLabel}`, name: fromLabel };
  }
  const fromDetail = nameFromDetail(step.detail);
  if (fromDetail) {
    return { key: `name:${fromDetail}`, name: fromDetail };
  }
  return { key: SHARED_KEY, name: SHARED_NAME };
}

export function groupAgentChainSteps(steps) {
  const list = Array.isArray(steps) ? steps : [];
  const groups = [];
  const indexByKey = new Map();
  for (const step of list) {
    const { key, name } = agentChainCandidateOf(step);
    if (!indexByKey.has(key)) {
      indexByKey.set(key, groups.length);
      groups.push({ key, name, steps: [] });
    }
    groups[indexByKey.get(key)].steps.push(step);
  }
  const sharedIdx = groups.findIndex((group) => group.key === SHARED_KEY);
  if (sharedIdx > 0) {
    const [shared] = groups.splice(sharedIdx, 1);
    groups.unshift(shared);
  }
  return groups;
}

function displayStepLabel(step, group) {
  let label = String(step?.label || step?.id || "step");
  const person = group?.key !== SHARED_KEY ? group?.name : "";
  if (person) {
    const suffix = ` · ${person}`;
    if (label.endsWith(suffix)) label = label.slice(0, -suffix.length);
    else if (label.includes(suffix)) label = label.replace(suffix, "");
  }
  return label;
}

export function agentChainStepMarkup(step, group) {
  const status = escapeHtml(step.status || "queued");
  const label = escapeHtml(displayStepLabel(step, group));
  const detail = escapeHtml(step.detail || "");
  const model = step.model ? escapeHtml(String(step.model)) : "";
  const ms = Number.isFinite(Number(step.duration_ms)) ? `${Number(step.duration_ms)} ms` : "";
  const meta = [ms, model].filter(Boolean).join(" · ");
  return `<div class="agent-chain-item ${status}"><i class="agent-chain-dot"></i><div><strong>${label}</strong>${detail ? `<p>${detail}</p>` : ""}</div><div class="agent-chain-meta">${meta || status}</div></div>`;
}

function groupMeta(group) {
  if (group.steps.some((step) => step.status === "running")) return "进行中";
  if (group.steps.some((step) => step.status === "failed")) return "失败";
  return `${group.steps.length} 步`;
}

function agentChainGroupMarkup(group, showHead) {
  const head = showHead
    ? `<div class="agent-chain-group-head"><b>${escapeHtml(group.name)}</b><span>${escapeHtml(groupMeta(group))}</span></div>`
    : "";
  const items = group.steps.map((step) => agentChainStepMarkup(step, group)).join("");
  return `<section class="agent-chain-group" data-candidate="${escapeHtml(group.name)}" data-group="${escapeHtml(group.key)}">${head}${items}</section>`;
}

export function agentChainGroupedMarkup(steps) {
  const groups = groupAgentChainSteps(steps);
  if (!groups.length) return "";
  const showHeads = groups.length > 1 || groups[0].key !== SHARED_KEY;
  return groups.map((group) => agentChainGroupMarkup(group, showHeads)).join("");
}
