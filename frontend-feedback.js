/** Recruiter feedback flywheel: decision / questions / status / evidence. */

const STORAGE_PREFIX = "resume_agent.feedback.v1";
const NEGATIVE_VALUES = new Set(["too_high", "too_low", "insufficient", "ineffective", "not_entered"]);

function storageKey(profile) {
  const workspace = window.SUPABASE_CONFIG?.workspaceId || "demo";
  const job = profile?.screeningJobId || "demo-job";
  const candidate = profile?.candidateProfileId || profile?.name || "unknown";
  return `${STORAGE_PREFIX}:${workspace}:${job}:${candidate}`;
}

function loadLocal(profile) {
  try {
    return JSON.parse(window.localStorage.getItem(storageKey(profile)) || "{}") || {};
  } catch {
    return {};
  }
}

function saveLocal(profile, state) {
  window.localStorage.setItem(storageKey(profile), JSON.stringify(state));
}

function options() {
  return [
    ["decision", "推荐结果", [["accurate", "准确"], ["too_high", "偏高"], ["too_low", "偏低"]]],
    ["question", "面试题", [["effective", "有效"], ["ineffective", "无效"]]],
    ["candidate_status", "候选人", [["entered_interview", "进入面试"], ["not_entered", "未进入面试"]]],
    ["evidence", "证据", [["confirmed", "确认有效"], ["insufficient", "证据不足"]]],
  ];
}

async function persistFeedback(profile, type, value) {
  const supabase = window.liveSupabase;
  const workspaceId = window.SUPABASE_CONFIG?.workspaceId;
  if (!supabase || !workspaceId || !profile?.candidateProfileId || !profile?.screeningJobId) {
    return "local";
  }
  const { data: { user } = {}, error: authError } = await supabase.auth.getUser();
  if (authError || !user) return "local";
  const { error } = await supabase.from("recruiter_feedback").insert({
    workspace_id: workspaceId,
    screening_job_id: profile.screeningJobId,
    candidate_profile_id: profile.candidateProfileId,
    feedback_type: type,
    value,
    job_title: profile.jobTitle || profile.role || "",
    skills: profile.skills || [],
    evidence_id: profile.evidenceIds?.[0] || null,
    polarity: NEGATIVE_VALUES.has(value) ? "negative_calibration" : "positive",
    created_by: user.id,
  });
  if (error) throw error;
  return "remote";
}

export function renderRecruiterFeedback(name, profile = {}) {
  const root = document.getElementById("candidate-feedback");
  if (!root) return;
  const state = loadLocal({ ...profile, name });
  root.replaceChildren();
  options().forEach(([type, label, choices]) => {
    const row = document.createElement("div");
    row.className = "feedback-row";
    const title = document.createElement("span");
    title.textContent = label;
    row.append(title);
    choices.forEach(([value, text]) => {
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = text;
      if (state[type] === value) button.classList.add("active");
      button.addEventListener("click", async () => {
        state[type] = value;
        saveLocal({ ...profile, name }, state);
        renderRecruiterFeedback(name, profile);
        try {
          const persisted = await persistFeedback(profile, type, value);
          window.notify?.(persisted === "remote" ? "已保存到数据库" : "仅保存到本地");
        } catch (error) {
          console.info("recruiter feedback persist failed", error);
          window.notify?.("保存失败，已保留在本地");
        }
      });
      row.append(button);
    });
    root.append(row);
  });
}

window.renderRecruiterFeedback = renderRecruiterFeedback;
