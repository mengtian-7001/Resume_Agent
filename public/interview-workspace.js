const INTERVIEW_STORAGE_NAMESPACE = "resume_agent.interview.v1";
const MAX_SCORE_PER_QUESTION = 10;

const candidateSelect = document.getElementById("interview-candidate-select");
const candidateMeta = document.getElementById("interview-candidate-meta");
const questionList = document.getElementById("interview-question-list");
const totalNode = document.getElementById("interview-total");
const totalMetaNode = document.getElementById("interview-total-meta");
const saveStateNode = document.getElementById("interview-save-state");

let activeCandidateName = "";

function profileEntries() {
  return Object.entries(window.profiles || {})
    .filter(([, profile]) => interviewQuestions(profile).length > 0)
    .sort(([left], [right]) => left.localeCompare(right, "zh-CN"));
}

function interviewQuestions(profile) {
  const questions = Array.isArray(profile?.questions) ? profile.questions : [];
  if (questions.length) return questions;
  return profile?.question ? [{ id: "fallback", question: profile.question }] : [];
}

function questionText(question) {
  return typeof question === "string" ? question : String(question?.question || "");
}

function questionTopic(question) {
  return typeof question === "object" ? String(question?.knowledge_point || "") : "";
}

function questionKey(question, index) {
  if (typeof question === "object" && (question.id || question.question_id)) {
    return String(question.id || question.question_id);
  }
  return `question-${index + 1}`;
}

function candidateScope(name, profile) {
  const configuredWorkspace = window.SUPABASE_CONFIG?.workspaceId || "demo";
  const jobId = profile?.screeningJobId || profile?.screening_job_id || "demo";
  const candidateId = profile?.candidateProfileId || profile?.candidate_profile_id || name;
  return { workspaceId: configuredWorkspace, jobId, candidateId };
}

// Reusable keying: namespace + workspace + screening job + candidate profile.
function interviewStorageKey(name, profile) {
  const scope = candidateScope(name, profile);
  return [
    INTERVIEW_STORAGE_NAMESPACE,
    encodeURIComponent(scope.workspaceId),
    encodeURIComponent(scope.jobId),
    encodeURIComponent(scope.candidateId),
  ].join(":");
}

function defaultInterviewState(name, profile) {
  return {
    version: 1,
    candidate: { name, ...candidateScope(name, profile) },
    answers: {},
    updatedAt: null,
  };
}

function loadInterviewState(name, profile) {
  const fallback = defaultInterviewState(name, profile);
  try {
    const raw = window.localStorage.getItem(interviewStorageKey(name, profile));
    if (!raw) return fallback;
    const saved = JSON.parse(raw);
    if (saved?.version !== 1 || !saved.answers || typeof saved.answers !== "object") return fallback;
    return { ...fallback, ...saved, candidate: fallback.candidate };
  } catch {
    setSaveState("浏览器存储不可用，当前记录不会保留", "error");
    return fallback;
  }
}

function persistInterviewState(name, profile, state) {
  state.updatedAt = new Date().toISOString();
  try {
    window.localStorage.setItem(interviewStorageKey(name, profile), JSON.stringify(state));
    setSaveState(`已自动保存 · ${new Date(state.updatedAt).toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" })}`, "saved");
  } catch {
    setSaveState("保存失败：浏览器存储不可用", "error");
  }
}

function setSaveState(message, state = "") {
  if (!saveStateNode) return;
  saveStateNode.textContent = message;
  saveStateNode.dataset.state = state;
}

function updateTotals(questions, state) {
  const entries = questions.map((question, index) => state.answers?.[questionKey(question, index)] || {});
  const scored = entries.filter((entry) => Number.isFinite(Number(entry.score)) && entry.score !== "").length;
  const total = entries.reduce((sum, entry) => sum + (Number.isFinite(Number(entry.score)) ? Number(entry.score) : 0), 0);
  if (totalNode) totalNode.textContent = `${total} / ${questions.length * MAX_SCORE_PER_QUESTION}`;
  if (totalMetaNode) totalMetaNode.textContent = scored
    ? `已评分 ${scored} / ${questions.length} 题`
    : `尚未评分 · 每题满分 ${MAX_SCORE_PER_QUESTION} 分`;
}

function renderCandidateOptions(entries) {
  if (!candidateSelect) return;
  candidateSelect.replaceChildren();
  entries.forEach(([name, profile]) => {
    const option = document.createElement("option");
    option.value = name;
    option.textContent = `${name} · ${profile.role || "候选人"}`;
    candidateSelect.append(option);
  });
}

function renderWorkspace(preferredName = activeCandidateName) {
  const entries = profileEntries();
  renderCandidateOptions(entries);
  if (!entries.length) {
    activeCandidateName = "";
    if (candidateSelect) candidateSelect.disabled = true;
    if (candidateMeta) candidateMeta.textContent = "暂无带专属面试题的候选人。请先完成候选人筛选。";
    if (questionList) questionList.replaceChildren(Object.assign(document.createElement("p"), { className: "interview-empty", textContent: "暂无可进行面试记录的候选人。" }));
    if (totalNode) totalNode.textContent = "0 / 0";
    if (totalMetaNode) totalMetaNode.textContent = "尚未评分";
    return;
  }

  const found = entries.find(([name]) => name === preferredName);
  activeCandidateName = (found || entries[0])[0];
  const profile = window.profiles[activeCandidateName];
  const questions = interviewQuestions(profile);
  const state = loadInterviewState(activeCandidateName, profile);
  if (candidateSelect) {
    candidateSelect.disabled = false;
    candidateSelect.value = activeCandidateName;
  }
  if (candidateMeta) {
    candidateMeta.textContent = `${activeCandidateName} · ${profile.role || "候选人"} · ${questions.length} 道专属面试题 · 每题满分 ${MAX_SCORE_PER_QUESTION} 分`;
  }
  renderQuestionCards(activeCandidateName, profile, questions, state);
  updateTotals(questions, state);
  if (!state.updatedAt) setSaveState("自动保存已就绪");
  else setSaveState(`上次保存 · ${new Date(state.updatedAt).toLocaleString("zh-CN")}`, "saved");
}

function renderQuestionCards(name, profile, questions, state) {
  if (!questionList) return;
  questionList.replaceChildren();
  questions.forEach((question, index) => {
    const key = questionKey(question, index);
    const answer = state.answers[key] || {};
    const card = document.createElement("article");
    card.className = "interview-question";

    const top = document.createElement("div");
    top.className = "interview-question-top";
    const heading = document.createElement("div");
    const number = document.createElement("span");
    number.className = "interview-question-number";
    number.textContent = `Q${String(index + 1).padStart(2, "0")}`;
    heading.append(number);
    const topic = questionTopic(question);
    if (topic) {
      const topicNode = document.createElement("span");
      topicNode.className = "interview-question-topic";
      topicNode.textContent = topic;
      heading.append(topicNode);
    }
    const text = document.createElement("p");
    text.className = "interview-question-text";
    text.textContent = questionText(question);
    heading.append(text);

    const scoreWrap = document.createElement("label");
    scoreWrap.className = "interview-score";
    scoreWrap.htmlFor = `interview-score-${index}`;
    scoreWrap.textContent = "评分 / 10";
    const scoreInput = document.createElement("input");
    scoreInput.className = "interview-score-input";
    scoreInput.id = `interview-score-${index}`;
    scoreInput.type = "number";
    scoreInput.min = "0";
    scoreInput.max = String(MAX_SCORE_PER_QUESTION);
    scoreInput.step = "1";
    scoreInput.inputMode = "numeric";
    scoreInput.setAttribute("aria-label", `第 ${index + 1} 题评分，满分 10 分`);
    scoreInput.value = answer.score ?? "";
    scoreWrap.append(scoreInput);
    top.append(heading, scoreWrap);

    const answerLabel = document.createElement("label");
    answerLabel.className = "interview-field-label";
    answerLabel.htmlFor = `interview-answer-${index}`;
    answerLabel.textContent = "候选人回答 / 面试笔记";
    const answerInput = document.createElement("textarea");
    answerInput.className = "interview-answer";
    answerInput.id = `interview-answer-${index}`;
    answerInput.placeholder = "记录候选人的关键回答、证据和待追问点…";
    answerInput.value = answer.answer || "";
    answerLabel.append(answerInput);
    card.append(top, answerLabel);
    questionList.append(card);

    const save = () => {
      const scoreValue = scoreInput.value.trim();
      const normalizedScore = scoreValue === "" ? "" : Math.max(0, Math.min(MAX_SCORE_PER_QUESTION, Math.round(Number(scoreValue) || 0)));
      if (scoreValue !== "" && scoreInput.value !== String(normalizedScore)) scoreInput.value = String(normalizedScore);
      state.answers[key] = { answer: answerInput.value, score: normalizedScore };
      persistInterviewState(name, profile, state);
      updateTotals(questions, state);
    };
    answerInput.addEventListener("input", save);
    scoreInput.addEventListener("input", save);
  });
}

candidateSelect?.addEventListener("change", () => renderWorkspace(candidateSelect.value));
document.addEventListener("viewchange", (event) => {
  if (event.detail.view === "interview") renderWorkspace(activeCandidateName);
});
window.addEventListener("profilesupdated", () => {
  if (document.body.dataset.view === "interview") renderWorkspace(activeCandidateName);
});

window.openInterviewWorkspace = (candidateName) => {
  renderWorkspace(candidateName);
  window.showView?.("interview");
};
