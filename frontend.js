import { createClient } from "https://esm.sh/@supabase/supabase-js@2";

const config = window.SUPABASE_CONFIG;
const statusNode = document.querySelector(".upload-side p");
const startButton = document.getElementById("start-screening");
const jdInput = document.getElementById("jd-input");
const resumeInput = document.getElementById("resume-input");
const selected = { jd: null, resumes: [] };
let liveSupabase = null;
let activeJobId = null;
let jobPollTimer = null;

const DEFAULT_SCREENING_CONFIG = {
  hard_gates: {
    min_years: { enabled: true },
    education: { enabled: true },
    must_have_skills: { enabled: true, min_coverage: 0.5 },
  },
  score_thresholds: {
    recommend_min: 75,
    review_min: 60,
  },
};

const SCREENING_CONFIG_STORAGE_KEY = "resume_agent_screening_config";
const RESUME_SAMPLE_COUNT = 4;

let sampleManifest = null;
const selectedSampleIds = { jd: null, resumes: new Set() };

wireScreeningConfigButtons();
initSamplePickers();

document.getElementById("result-list").addEventListener("click", (event) => {
  const button = event.target.closest(".detail");
  const card = button?.closest(".person-card");
  if (card) window.openDrawer?.(card.dataset.name);
});

if (!config?.url || !config?.anonKey || !config?.workspaceId) {
  if (statusNode) {
    statusNode.textContent = "当前为演示数据。复制 supabase-config.example.js 为 supabase-config.js 并填写项目配置后，即可启用真实上传与任务状态。";
  }
  wireDemoViews();
} else {
  liveSupabase = createClient(config.url, config.anonKey);
  const supabase = liveSupabase;
  resetUploadLists();
  const sessionReady = initializeAnonymousSession(supabase);
  wireAuth(sessionReady);
  wireUpload(supabase);
  wireLiveViews(supabase, sessionReady);
  sessionReady
    .then(() => {
      watchLatestJob(supabase);
      probeWorkerService();
    })
    .catch((error) => showUploadError(`匿名会话初始化失败：${error.message}`));
}

async function initializeAnonymousSession(supabase) {
  const { data: { user: existingUser } } = await supabase.auth.getUser();
  let user = existingUser;
  if (!user) {
    const { data, error } = await supabase.auth.signInAnonymously();
    if (error) throw error;
    user = data.user;
  }

  if (!user) throw new Error("未能创建匿名会话。");

  const { error: membershipError } = await bootstrapWorkspaceMembership(supabase);
  if (membershipError) throw membershipError;

  return user;
}

async function bootstrapWorkspaceMembership(supabase) {
  const fixed = await supabase.rpc("bootstrap_anonymous_workspace");
  if (!fixed.error) return fixed;

  const missingRpc = fixed.error.code === "PGRST202"
    || fixed.error.message?.includes("schema cache");
  if (!missingRpc) return fixed;

  return supabase.rpc("bootstrap_anonymous_workspace", {
    target_workspace_id: config.workspaceId,
  });
}

function wireAuth(sessionReady) {
  const action = document.getElementById("login-action");
  const name = document.getElementById("account-name");
  const role = document.getElementById("account-role");

  sessionReady.then(() => {
    name.textContent = "匿名会话";
    role.textContent = "本地招聘工作区";
    action.disabled = true;
    action.setAttribute("aria-label", "匿名会话已就绪");
  });
}

function wireUpload(supabase) {
  window.demoAdd = (type) => (type === "jd" ? jdInput : resumeInput).click();

  jdInput.addEventListener("change", () => {
    const [file] = jdInput.files;
    if (!file) return;
    selected.jd = file;
    selectedSampleIds.jd = null;
    renderFiles();
    renderSampleChips("jd");
  });

  resumeInput.addEventListener("change", () => {
    selected.resumes.push(...Array.from(resumeInput.files || []));
    resumeInput.value = "";
    selectedSampleIds.resumes.clear();
    renderFiles();
    renderSampleChips("resume");
  });

  startButton.addEventListener("click", async () => {
    try {
      startButton.disabled = true;
      startButton.textContent = "正在上传…";
      resetParseMeterState();
      updateParseMeter({ upload: "processing" }, { status: "uploading" });
      const jobId = await startScreening(supabase);
      startButton.textContent = "正在解析…";
      window.showView?.("upload");
      await runOneClickParse(jobId);
      startButton.textContent = "解析完成 ✓";
      window.showView?.("results");
    } catch (error) {
      stopSyntheticParseProgress();
      startButton.disabled = false;
      startButton.textContent = "一键解析 →";
      showUploadError(error.message);
    }
  });

  document.getElementById("new-screening-btn")?.addEventListener("click", () => {
    resetUploadWorkspace();
  });
}

async function startScreening(supabase) {
  if (!selected.jd) throw new Error("请先选择 1 份 JD。");
  if (selected.resumes.length < 1) throw new Error("请至少选择 1 份候选人简历。");
  if (selected.resumes.length > 20) throw new Error("单次最多上传 20 份简历。");

  const { data: { user } } = await supabase.auth.getUser();
  if (!user) throw new Error("请先通过 Supabase Auth 登录后再上传文件。");

  const { data: job, error: jobError } = await supabase
    .from("screening_jobs")
    .insert({
      workspace_id: config.workspaceId,
      title: selected.jd.name.replace(/\.[^.]+$/, ""),
      status: "uploading",
      created_by: user.id,
    })
    .select()
    .single();
  if (jobError) throw jobError;

  const docs = [{ file: selected.jd, type: "jd" }, ...selected.resumes.map((file) => ({ file, type: "resume" }))];
  for (const document of docs) {
    validateFile(document.file);
    const path = `${config.workspaceId}/${job.id}/${document.type}/${crypto.randomUUID()}-${safeFilename(document.file.name)}`;
    const { error: storageError } = await supabase.storage
      .from("screening-documents")
      .upload(path, document.file, { contentType: document.file.type, upsert: false });
    if (storageError) throw storageError;

    const { error: documentError } = await supabase.from("documents").insert({
      workspace_id: config.workspaceId,
      screening_job_id: job.id,
      document_type: document.type,
      original_filename: document.file.name,
      storage_path: path,
      mime_type: document.file.type,
      size_bytes: document.file.size,
      status: "validated",
    });
    if (documentError) throw documentError;
  }

  const { error: startError } = await supabase.rpc("start_screening", { target_job_id: job.id });
  if (startError) throw startError;
  if (statusNode) statusNode.textContent = "文件上传完成，已进入解析队列。结果页会自动刷新任务进度。";

  const freshJob = await fetchJobById(supabase, job.id);
  activeJobId = freshJob?.id || job.id;
  showSubmittedPanel(freshJob || { ...job, status: "queued" }, docs.map((document) => ({
    original_filename: document.file.name,
    document_type: document.type,
    size_bytes: document.file.size,
  })));
  startJobPolling(supabase, activeJobId);
  selected.jd = null;
  selected.resumes = [];
  selectedSampleIds.jd = null;
  selectedSampleIds.resumes.clear();
  resetUploadLists();
  renderSampleChips("jd");
  renderSampleChips("resume");
  return activeJobId;
}

function validateFile(file) {
  const allowed = [
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  ];
  if (!allowed.includes(file.type)) throw new Error(`${file.name} 不是 PDF 或 DOCX 文件。`);
  if (file.size > 10 * 1024 * 1024) throw new Error(`${file.name} 超过 10MB 限制。`);
}

function resetUploadLists() {
  const jdBox = document.getElementById("jd-file");
  const resumeBox = document.getElementById("resume-files");
  if (jdBox) jdBox.innerHTML = '<p class="file-empty">尚未选择 JD 文件</p>';
  if (resumeBox) resumeBox.innerHTML = '<p class="file-empty">尚未选择简历</p>';
}

function renderFiles() {
  const jdBox = document.getElementById("jd-file");
  const resumeBox = document.getElementById("resume-files");
  if (selected.jd) jdBox.innerHTML = fileMarkup(selected.jd);
  else jdBox.innerHTML = '<p class="file-empty">尚未选择 JD 文件</p>';
  if (selected.resumes.length) resumeBox.innerHTML = selected.resumes.map(fileMarkup).join("");
  else resumeBox.innerHTML = '<p class="file-empty">尚未选择简历</p>';
}

function fileMarkup(file) {
  const ext = file.name.endsWith(".docx") ? "DOCX" : "PDF";
  return `<div class="file file-pending"><div class="filetype">${ext}</div><div class="file-name">${escapeHtml(file.name)}<span>${Math.ceil(file.size / 1024)} KB · 待提交</span></div></div>`;
}

const jobStatusLabels = {
  uploading: { label: "上传中", desc: "文件正在上传…" },
  queued: { label: "排队中", desc: "已进入解析队列，正在等待 Worker 处理。" },
  processing: { label: "解析中", desc: "正在解析 JD 与简历并匹配。" },
  completed: { label: "已完成", desc: "筛选已完成，可查看候选人结果。" },
  failed: { label: "失败", desc: "任务处理失败，请检查文件并重试。" },
  draft: { label: "草稿", desc: "任务尚未开始。" },
  cancelled: { label: "已取消", desc: "任务已取消。" },
};

async function fetchJobById(supabase, jobId) {
  const { data, error } = await supabase
    .from("screening_jobs")
    .select("id,title,status,candidate_count,processed_count,error_message")
    .eq("id", jobId)
    .single();
  if (error) throw error;
  return data;
}

function getJobStatusDesc(job) {
  const info = jobStatusLabels[job.status] || { label: job.status, desc: "" };
  if (job.status === "failed" && job.error_message) return job.error_message;
  let desc = info.desc;
  if (["queued", "processing"].includes(job.status) && job.candidate_count) {
    desc = `${desc} 已处理 ${job.processed_count || 0} / ${job.candidate_count} 份。`;
  }
  if (["queued", "processing"].includes(job.status) && !config.workerUrl && !config.workerToken) {
    desc = `${desc} 若长时间无进展，请先运行 ./dev.sh 启动解析服务。`;
  }
  return desc;
}

function workerBaseUrl() {
  if (config?.workerUrl) return config.workerUrl;
  if (["localhost", "127.0.0.1"].includes(window.location.hostname)) return "http://127.0.0.1:8000";
  return "/api";
}

async function probeWorkerService() {
  if (!statusNode) return;
  try {
    const response = await fetch(`${workerBaseUrl()}/health`);
    if (response.ok) {
      statusNode.textContent = "解析服务已就绪。选择样例 JD 和简历后，点击「一键解析」即可。";
    } else {
      statusNode.textContent = "解析服务未响应。请先运行 ./dev.sh。";
    }
  } catch {
    statusNode.textContent = "解析服务未连接。请先运行 ./dev.sh，再点击「一键解析」。";
  }
}

async function runOneClickParse(jobId) {
  if (!liveSupabase) throw new Error("会话未就绪，请刷新页面。");
  const base = workerBaseUrl();
  const headers = { "Content-Type": "application/json" };
  let url = `${base}/dev/jobs/process`;
  if (config.workerToken) {
    url = `${base}/internal/jobs/process`;
    headers["X-Internal-Token"] = config.workerToken;
  } else if (base === "/api") {
    const { data, error } = await liveSupabase.auth.getSession();
    if (error || !data.session?.access_token) throw new Error("登录会话已失效，请刷新页面。");
    url = `${base}/jobs/process`;
    headers.Authorization = `Bearer ${data.session.access_token}`;
  }
  beginSyntheticParseProgress();
  const response = await fetch(url, {
    method: "POST",
    headers,
    body: JSON.stringify({ job_id: jobId }),
  });
  if (!response.ok) {
    stopSyntheticParseProgress();
    let detail = "解析失败，请查看终端日志后重试。";
    try {
      const body = await response.json();
      if (typeof body?.detail === "string") detail = body.detail;
      else if (body?.job?.error_message) detail = body.job.error_message;
    } catch {
      if (response.status === 0 || response.status >= 500) {
        detail = "解析服务未启动。请在项目根目录运行 ./dev.sh 后重试。";
      }
    }
    throw new Error(detail);
  }
  const result = await response.json();
  let job = await refreshJobState(liveSupabase, jobId);
  for (let i = 0; i < 20 && !["completed", "failed", "cancelled"].includes(job?.status || result.status); i += 1) {
    await new Promise((resolve) => window.setTimeout(resolve, 400));
    job = await refreshJobState(liveSupabase, jobId);
  }
  const status = job?.status || result.status;
  if (status === "failed") {
    throw new Error(job?.error_message || result.job?.error_message || "解析失败，请检查文件内容。");
  }
  if (status !== "completed") {
    throw new Error("解析尚未完成。请打开「候选人」查看是否已出结果，或再点一次「一键解析」。");
  }
  // Backend may finish in <1s; keep UX on the meter until timeline reaches 100%.
  await waitForParseMeterComplete(18000);
  await loadResults(liveSupabase, jobId);
  return { ...result, status, job };
}

function resetUploadWorkspace() {
  const panel = document.getElementById("submitted-panel");
  const uploadView = document.getElementById("upload");
  if (panel) panel.hidden = true;
  uploadView?.classList.remove("upload-busy");
  selected.jd = null;
  selected.resumes = [];
  selectedSampleIds.jd = null;
  selectedSampleIds.resumes.clear();
  resetUploadLists();
  renderSampleChips("jd");
  renderSampleChips("resume");
  if (startButton) {
    startButton.disabled = false;
    startButton.textContent = "一键解析 →";
  }
  if (statusNode) statusNode.textContent = "选择样例或上传文件后，点击「一键解析」即可。";
  resetParseMeterState();
  const fill = document.getElementById("parse-meter-fill");
  const percentNode = document.getElementById("parse-meter-percent");
  const title = document.getElementById("parse-meter-title");
  const label = document.getElementById("parse-meter-label");
  if (fill) fill.style.width = "0%";
  if (percentNode) percentNode.textContent = "0%";
  if (title) title.textContent = "等待开始";
  if (label) label.textContent = "准备开始";
  const chainList = document.getElementById("agent-chain-list");
  const chainMode = document.getElementById("agent-chain-mode");
  if (chainList) {
    chainList.innerHTML = '<p class="agent-chain-empty">一键解析后，这里会实时显示 Construction / Checker 等步骤。</p>';
  }
  if (chainMode) chainMode.textContent = "等待步骤事件…";
}

function showSubmittedPanel(job, documents) {
  const panel = document.getElementById("submitted-panel");
  const list = document.getElementById("submitted-list");
  const status = document.getElementById("submitted-status");
  const desc = document.getElementById("submitted-desc");
  const title = document.getElementById("submitted-title");
  const viewResults = document.getElementById("view-results-btn");
  const uploadView = document.getElementById("upload");
  if (!panel || !list || !documents?.length) return;

  const info = jobStatusLabels[job.status] || { label: job.status, desc: "" };
  const wasHidden = panel.hidden;
  panel.hidden = false;
  uploadView?.classList.add("upload-busy");
  title.textContent = job.title || "本次筛选";
  status.textContent = info.label;
  status.dataset.state = job.status;
  desc.textContent = getJobStatusDesc(job);
  list.innerHTML = documents.map(submittedDocMarkup).join("");
  if (viewResults) {
    viewResults.hidden = job.status !== "completed";
    viewResults.className = job.status === "completed" ? "primary" : "outline";
  }
  if (wasHidden) {
    window.requestAnimationFrame(() => {
      panel.scrollIntoView({ behavior: "smooth", block: "start" });
    });
  }
}

function startJobPolling(supabase, jobId) {
  stopJobPolling();
  if (!jobId) return;
  const meter = getParseMeterState();
  if (!meter.startedAt) meter.startedAt = Date.now();
  jobPollTimer = window.setInterval(() => {
    refreshJobState(supabase, jobId).catch(() => {});
  }, 700);
}

function stopJobPolling() {
  if (jobPollTimer) {
    window.clearInterval(jobPollTimer);
    jobPollTimer = null;
  }
}

async function refreshJobState(supabase, jobId) {
  const job = await fetchJobById(supabase, jobId);
  const { data: documents } = await supabase
    .from("documents")
    .select("original_filename,document_type,size_bytes")
    .eq("screening_job_id", jobId)
    .order("document_type");
  if (documents?.length) showSubmittedPanel(job, documents);
  updateJobProgress(job);
  await updateParseProgress(supabase, job);
  await refreshAgentChain(supabase, jobId);
  const meter = getParseMeterState();
  if (job.status === "completed") {
    meter.pendingResultsJobId = jobId;
    meter.backendDone = true;
    meter.jobCompleted = true;
    ensureParseMeterAnimation();
  }
  if (job.status === "failed" || job.status === "cancelled") {
    stopJobPolling();
    stopSyntheticParseProgress();
  }
  return job;
}

async function refreshAgentChain(supabase, jobId) {
  const list = document.getElementById("agent-chain-list");
  const modeNode = document.getElementById("agent-chain-mode");
  if (!list || !jobId) return;
  try {
    const { data, error } = await supabase
      .from("agent_runs")
      .select("status,mode,state")
      .eq("screening_job_id", jobId)
      .maybeSingle();
    if (error) throw error;
    const steps = Array.isArray(data?.state?.steps) ? data.state.steps : [];
    const mode = data?.mode || "—";
    const stage = data?.state?.stage || data?.status || "waiting";
    if (modeNode) modeNode.textContent = `mode=${mode} · ${stage} · ${steps.length} steps`;
    if (!steps.length) {
      list.innerHTML = '<p class="agent-chain-empty">等待 Worker 写入步骤事件…</p>';
      return;
    }
    const nearBottom = list.scrollHeight - list.scrollTop - list.clientHeight < 48;
    list.innerHTML = steps.map(agentChainStepMarkup).join("");
    if (nearBottom || steps.length <= 6) {
      list.scrollTop = list.scrollHeight;
    }
  } catch {
    if (modeNode) modeNode.textContent = "agent_runs 暂不可读";
  }
}

function agentChainStepMarkup(step) {
  const status = escapeHtml(step.status || "queued");
  const label = escapeHtml(step.label || step.id || "step");
  const detail = escapeHtml(step.detail || "");
  const model = step.model ? escapeHtml(String(step.model)) : "";
  const ms = Number.isFinite(Number(step.duration_ms)) ? `${Number(step.duration_ms)} ms` : "";
  const meta = [ms, model].filter(Boolean).join(" · ");
  return `<div class="agent-chain-item ${status}"><i class="agent-chain-dot"></i><div><strong>${label}</strong>${detail ? `<p>${detail}</p>` : ""}</div><div class="agent-chain-meta">${meta || status}</div></div>`;
}

async function processJobQueue(jobId) {
  await runOneClickParse(jobId);
}

function submittedDocMarkup(doc) {
  const typeLabel = doc.document_type === "jd" ? "JD" : "简历";
  const size = doc.size_bytes ? `${Math.ceil(doc.size_bytes / 1024)} KB` : "";
  return `<div class="submitted-item"><span class="submitted-type">${typeLabel}</span><div class="submitted-name">${escapeHtml(doc.original_filename)}${size ? `<span>${size}</span>` : ""}</div><span class="submitted-done">已提交</span></div>`;
}

function watchLatestJob(supabase) {
  const refresh = async () => {
    const { data: jobs } = await supabase
      .from("screening_jobs")
      .select("id,title,status,candidate_count,processed_count,error_message")
      .eq("workspace_id", config.workspaceId)
      .order("created_at", { ascending: false })
      .limit(1);
    const job = jobs?.[0];
    if (!job) return;
    activeJobId = job.id;
    updateJobProgress(job);
    const { data: documents } = await supabase
      .from("documents")
      .select("original_filename,document_type,size_bytes")
      .eq("screening_job_id", job.id)
      .order("document_type");
    if (documents?.length) showSubmittedPanel(job, documents);
    await updateParseProgress(supabase, job);
    await refreshAgentChain(supabase, job.id);
    if (["queued", "processing", "uploading"].includes(job.status)) {
      startJobPolling(supabase, job.id);
    } else {
      stopJobPolling();
    }
    if (job.status === "completed") await loadResults(supabase, job.id);
    if (document.getElementById("tasks")?.classList.contains("active")) loadTaskHistory(supabase);
  };
  refresh();
  supabase
    .channel("screening-jobs")
    .on("postgres_changes", { event: "*", schema: "public", table: "screening_jobs", filter: `workspace_id=eq.${config.workspaceId}` }, refresh)
    .subscribe();
}

async function loadResults(supabase, jobId) {
  const list = document.getElementById("result-list");
  try {
    const [{ data: matches, error: matchError }, { data: profiles }, packsResult] = await Promise.all([
      supabase.from("match_results").select("*").eq("screening_job_id", jobId).order("score", { ascending: false }),
      supabase.from("candidate_profiles").select("id,display_name,profile").eq("screening_job_id", jobId),
      supabase.from("question_packs").select("candidate_profile_id,questions,followups").eq("screening_job_id", jobId),
    ]);
    if (matchError) throw matchError;
    const packs = packsResult?.data || [];
    const byProfile = new Map((profiles || []).map((profile) => [profile.id, profile]));
    const byPack = new Map((packs || []).map((pack) => [pack.candidate_profile_id, pack]));
    if (!list) return;
    if (!matches?.length) {
      updateResultsSummary([]);
      list.innerHTML = '<p class="task-empty">这次筛选还没有候选人结果。请回到「发起筛选」，点「再发起一次」或重新选样例后点「一键解析」。解析服务需保持 <code>./dev.sh</code> 运行中。</p>';
      return;
    }
    updateResultsSummary(matches);
    window.profiles ||= {};
    matches.forEach((match) => {
      const profile = byProfile.get(match.candidate_profile_id);
      const name = profile?.display_name || "未命名候选人";
      const evidence = Array.isArray(match.evidence) ? match.evidence : [];
      const risks = Array.isArray(match.risks) ? match.risks : [];
      const breakdown = match.score_breakdown && typeof match.score_breakdown === "object" ? match.score_breakdown : {};
      const pack = byPack.get(match.candidate_profile_id);
      const embeddedQuestions = Array.isArray(breakdown.questions) ? breakdown.questions : [];
      const embeddedFollowups = Array.isArray(breakdown.followups) ? breakdown.followups : [];
      const questions = Array.isArray(pack?.questions) && pack.questions.length ? pack.questions : embeddedQuestions;
      const followups = Array.isArray(pack?.followups) && pack.followups.length ? pack.followups : embeddedFollowups;
      const scoreEntries = Object.entries(breakdown)
        .filter(([label, score]) => !["questions", "followups"].includes(label) && typeof score === "number")
        .map(([label, score]) => [label, Number(score) || 0]);
      window.profiles[name] = {
        role: `${profile?.profile?.years_experience || 0} 年经验`,
        score: Math.round(Number(match.score) || 0),
        decision: { recommend: "建议优先面试", review: "建议人工复核", reject: "当前不建议推进" }[match.decision] || "已完成分析",
        summary: (typeof risks[0] === "string" ? risks[0] : evidence[0]?.text) || "已完成自动分析。",
        gates: [["硬门槛", match.hard_gate_pass ? "已通过" : "未通过", match.hard_gate_pass ? "通过" : "未通过"]],
        scores: scoreEntries,
        quotes: evidence.map((item) => `“${item?.text || item || ""}”`).filter((quote) => quote !== "“”"),
        questions,
        followups,
        question: match.interview_question || followups[0]?.question || questions[0]?.question || "",
      };
    });
    list.innerHTML = matches.map((match) => {
      const profile = byProfile.get(match.candidate_profile_id);
      const name = profile?.display_name || "未命名候选人";
      const skills = (profile?.profile?.skills || []).slice(0, 3);
      const evidence = Array.isArray(match.evidence) ? match.evidence : [];
      const pack = byPack.get(match.candidate_profile_id);
      const embeddedQuestions = Array.isArray(match.score_breakdown?.questions) ? match.score_breakdown.questions : [];
      const questionCount = (Array.isArray(pack?.questions) && pack.questions.length)
        ? pack.questions.length
        : embeddedQuestions.length;
      const kind = match.decision === "recommend" ? "good" : (match.decision || "review");
      const label = { recommend: "推荐面试", review: "建议复核", reject: "不匹配" }[match.decision] || "已分析";
      return `<article class="person-card" data-kind="${escapeHtml(kind)}" data-name="${escapeHtml(name)}">
      <div class="person-head"><div class="initial">${escapeHtml(name.slice(0, 1))}</div><div><h3>${escapeHtml(name)}</h3><p>${profile?.profile?.years_experience || 0} 年经验</p></div><div class="ring" style="--score:${Number(match.score) || 0}"><span>${Math.round(Number(match.score) || 0)}</span></div></div>
      <div class="tags">${skills.map((skill) => `<span class="tag">${escapeHtml(skill)}</span>`).join("")}</div>
      <div class="evidence"><strong>匹配结果：</strong>${escapeHtml(evidence[0]?.text || "等待分析证据")}</div>
      <div class="person-footer"><span class="badge ${escapeHtml(kind)}">${escapeHtml(label)}</span><span class="qcount">${questionCount ? `${questionCount} 道面试题` : "暂无面试题"}</span><button class="detail">查看分析 →</button></div>
    </article>`;
    }).join("");
  } catch (error) {
    if (list) list.innerHTML = `<p class="task-empty">结果加载失败：${escapeHtml(error.message || "请刷新后重试")}</p>`;
  }
}

function updateResultsSummary(matches) {
  const list = matches || [];
  const recommend = list.filter((item) => item.decision === "recommend").length;
  const review = list.filter((item) => item.decision === "review").length;
  const reject = list.filter((item) => item.decision === "reject").length;
  const intro = document.querySelector("#results .intro p");
  const headline = document.querySelector("#results .result-top h2");
  const stamp = document.querySelector("#results .result-top small");
  const counts = document.querySelectorAll("#results .result-summary b");
  const filters = document.querySelectorAll("#results .filters .filter");
  if (intro) intro.textContent = list.length ? `本次共分析 ${list.length} 份简历` : "暂无分析结果";
  if (headline) {
    headline.textContent = list.length
      ? (recommend ? `建议优先面试 ${recommend} 位候选人` : "本次筛选结果")
      : "还没有候选人结果";
  }
  if (stamp) stamp.textContent = list.length ? `本次筛选 · ${list.length} 份简历` : "等待完成解析";
  if (counts[0]) counts[0].textContent = String(list.length);
  if (counts[1]) counts[1].textContent = String(recommend);
  if (counts[2]) counts[2].textContent = String(review);
  if (filters[0]) filters[0].textContent = `全部 ${list.length}`;
  if (filters[1]) filters[1].textContent = `推荐 ${recommend}`;
  if (filters[2]) filters[2].textContent = `待复核 ${review}`;
  if (filters[3]) filters[3].textContent = `不匹配 ${reject}`;
}

function updateJobProgress(job) {
  const progress = document.querySelector("#live-pipeline-meta span");
  const title = document.querySelector("#live-pipeline-meta");
  if (progress) progress.textContent = job.candidate_count
    ? `已处理 ${job.processed_count || 0} / ${job.candidate_count} 份`
    : (jobStatusLabels[job.status]?.label || job.status);
  if (title) {
    const label = title.childNodes[0];
    if (label && label.nodeType === Node.TEXT_NODE) {
      label.textContent = `${job.title || "当前筛选"} `;
    }
  }
  if (job.status === "failed" && statusNode) statusNode.textContent = job.error_message || "任务处理失败，请检查文件并重试。";
}

async function updateParseProgress(supabase, job) {
  const { data: tasks } = await supabase
    .from("processing_tasks")
    .select("task_type,status")
    .eq("screening_job_id", job.id);
  const list = tasks || [];
  const statusOf = (type) => {
    const typed = list.filter((task) => task.task_type === type);
    if (!typed.length) return job.status === "completed" ? "completed" : "queued";
    if (typed.every((task) => task.status === "completed")) return "completed";
    if (typed.some((task) => task.status === "failed")) return "failed";
    if (typed.some((task) => task.status === "processing")) return "processing";
    return "queued";
  };

  const upload = job.status === "uploading" ? "processing" : "completed";
  const parseJd = statusOf("parse_jd");
  const parseResume = parseJd === "completed" ? statusOf("parse_resume") : "queued";
  const match = parseResume === "completed" ? statusOf("match") : "queued";
  let questions = "queued";
  if (job.status === "completed") questions = "completed";
  else if (match === "completed") questions = "processing";
  else if (match === "failed") questions = "failed";

  const stepStates = {
    upload,
    parse_jd: parseJd,
    parse_resume: parseResume,
    match,
    questions,
  };

  const meter = getParseMeterState();
  // Only stage the 5 steps while the synthetic meter is actively running.
  // Completed jobs opened later must show all steps done (not stuck on 上传).
  const elapsed = Date.now() - (meter.startedAt || Date.now());
  const holdVisual = Boolean(meter.syntheticTimer)
    && (job.status === "completed" || meter.backendDone)
    && meter.displayed < 100
    && elapsed < PARSE_METER_MIN_MS;
  if (meter.displayed >= 100 && (job.status === "completed" || meter.backendDone)) {
    PARSE_STEP_ORDER.forEach((step) => { stepStates[step] = "completed"; });
  } else if (holdVisual) {
    const stage = Math.min(PARSE_STEP_ORDER.length - 1, Math.floor(elapsed / 2000));
    PARSE_STEP_ORDER.forEach((step, index) => {
      if (index < stage) stepStates[step] = "completed";
      else if (index === stage) stepStates[step] = "processing";
      else stepStates[step] = "queued";
    });
  } else if (job.status === "completed") {
    Object.keys(stepStates).forEach((key) => { stepStates[key] = "completed"; });
  }

  Object.entries(stepStates).forEach(([step, status]) => setParseStep(step, status));
  updateParseTrackSummary(stepStates, job);
  updateParseMeter(stepStates, job);

  const pipe = {
    parse: stepStates.parse_jd,
    extract: stepStates.parse_resume,
    match: stepStates.match,
    questions: stepStates.questions,
  };
  document.querySelectorAll("#live-pipeline .pipe-node").forEach((node) => {
    const state = pipe[node.dataset.pipe];
    node.classList.toggle("done", state === "completed");
    node.classList.toggle("now", state === "processing");
    const icon = node.querySelector(".pipe-icon");
    if (icon) {
      icon.textContent = state === "completed" ? "✓" : state === "processing" ? "◌" : (node.dataset.pipe === "questions" ? "4" : "◷");
    }
  });
}

const PARSE_STEP_WEIGHTS = {
  upload: 12,
  parse_jd: 22,
  parse_resume: 28,
  match: 22,
  questions: 16,
};

const PARSE_STEP_LABELS = {
  upload: "正在上传与校验文件…",
  parse_jd: "正在解析岗位 JD…",
  parse_resume: "正在解析候选人简历…",
  match: "正在智能匹配打分…",
  questions: "正在生成面试题…",
};

const PARSE_STEP_ORDER = Object.keys(PARSE_STEP_WEIGHTS);

/** Minimum visual duration before 100% is allowed (backend often finishes much sooner). */
const PARSE_METER_MIN_MS = 11000;

/** Timeline caps: [elapsedMs, maxPercent]. 100% only after min duration + backend done. */
const PARSE_METER_TIMELINE = [
  [0, 4],
  [1200, 14],
  [2800, 30],
  [4500, 48],
  [6500, 66],
  [8500, 82],
  [PARSE_METER_MIN_MS, 96],
];

function getParseMeterState() {
  if (!window.__parseMeter) {
    window.__parseMeter = {
      displayed: 0,
      target: 0,
      floor: 0,
      label: "准备开始",
      title: "等待开始",
      animTimer: null,
      syntheticTimer: null,
      syntheticStage: 0,
      startedAt: 0,
      backendDone: false,
      jobCompleted: false,
      pendingResultsJobId: null,
      stepStartedAt: 0,
      activeStepKey: null,
      lastStepStates: null,
    };
  }
  return window.__parseMeter;
}

function resetParseMeterState() {
  const meter = getParseMeterState();
  if (meter.animTimer) {
    window.clearInterval(meter.animTimer);
    meter.animTimer = null;
  }
  stopSyntheticParseProgress();
  meter.displayed = 0;
  meter.target = 0;
  meter.floor = 0;
  meter.label = "准备开始";
  meter.title = "等待开始";
  meter.startedAt = Date.now();
  meter.backendDone = false;
  meter.jobCompleted = false;
  meter.pendingResultsJobId = null;
  meter.stepStartedAt = 0;
  meter.activeStepKey = null;
  meter.syntheticStage = 0;
  meter.lastStepStates = null;
}

function timelinePercentCap(elapsedMs, backendDone) {
  const points = PARSE_METER_TIMELINE;
  let cap = points[0][1];
  for (let i = 0; i < points.length - 1; i += 1) {
    const [t0, p0] = points[i];
    const [t1, p1] = points[i + 1];
    if (elapsedMs <= t0) {
      cap = p0;
      break;
    }
    if (elapsedMs >= t1) {
      cap = p1;
      continue;
    }
    const ratio = (elapsedMs - t0) / Math.max(1, t1 - t0);
    cap = p0 + (p1 - p0) * ratio;
    break;
  }
  if (elapsedMs >= points[points.length - 1][0]) {
    cap = points[points.length - 1][1];
  }
  cap = Math.min(96, Math.max(0, cap));
  if (backendDone && elapsedMs >= PARSE_METER_MIN_MS) return 100;
  return Math.floor(cap);
}

function beginSyntheticParseProgress() {
  const meter = getParseMeterState();
  stopSyntheticParseProgress();
  if (!meter.startedAt) meter.startedAt = Date.now();
  meter.syntheticStage = 0;
  meter.backendDone = false;
  meter.jobCompleted = false;
  // Drive staged labels + capped targets while API / polling runs.
  meter.syntheticTimer = window.setInterval(() => {
    const elapsed = Date.now() - (meter.startedAt || Date.now());
    const stage = Math.min(
      PARSE_STEP_ORDER.length - 1,
      Math.floor(elapsed / 2000)
    );
    meter.syntheticStage = stage;
    const softStates = {};
    PARSE_STEP_ORDER.forEach((step, index) => {
      if (meter.backendDone && elapsed >= PARSE_METER_MIN_MS - 500) {
        softStates[step] = "completed";
      } else if (index < stage) softStates[step] = "completed";
      else if (index === stage) softStates[step] = "processing";
      else softStates[step] = "queued";
    });
    updateParseMeter(softStates, {
      status: meter.backendDone ? "completed" : "processing",
    });
  }, 250);
}

function stopSyntheticParseProgress() {
  const meter = getParseMeterState();
  if (meter.syntheticTimer) {
    window.clearInterval(meter.syntheticTimer);
    meter.syntheticTimer = null;
  }
}

function markAllParseStepsComplete() {
  PARSE_STEP_ORDER.forEach((step) => setParseStep(step, "completed"));
  updateParseTrackSummary(
    Object.fromEntries(PARSE_STEP_ORDER.map((step) => [step, "completed"])),
    { status: "completed" }
  );
  document.querySelectorAll("#live-pipeline .pipe-node").forEach((node) => {
    node.classList.add("done");
    node.classList.remove("now");
    const icon = node.querySelector(".pipe-icon");
    if (icon) icon.textContent = "✓";
  });
}

function waitForParseMeterComplete(timeoutMs = 18000) {
  const meter = getParseMeterState();
  meter.backendDone = true;
  meter.jobCompleted = true;
  ensureParseMeterAnimation();
  return new Promise((resolve) => {
    const started = Date.now();
    const timer = window.setInterval(() => {
      const elapsed = Date.now() - (meter.startedAt || started);
      const cap = timelinePercentCap(elapsed, true);
      meter.target = Math.max(meter.target || 0, Math.min(100, cap));
      if (meter.displayed >= 100 || Date.now() - started >= timeoutMs) {
        meter.displayed = 100;
        meter.target = 100;
        meter.title = "全部完成";
        meter.label = "解析完成，可查看候选人与面试题";
        renderParseMeter(meter);
        markAllParseStepsComplete();
        window.clearInterval(timer);
        stopSyntheticParseProgress();
        stopJobPolling();
        resolve();
      }
    }, 200);
  });
}

function ensureParseMeterAnimation() {
  const meter = getParseMeterState();
  if (meter.animTimer) return;
  meter.animTimer = window.setInterval(async () => {
    const elapsed = Date.now() - (meter.startedAt || Date.now());
    const cap = timelinePercentCap(elapsed, meter.backendDone || meter.jobCompleted);
    // Target follows the wall-clock timeline only (never leaps past the cap).
    meter.target = Math.max(meter.displayed || 0, cap);
    meter.floor = Math.max(meter.floor || 0, Math.min(96, meter.target));

    const gap = meter.target - meter.displayed;
    if (gap > 0) {
      const step = gap > 25 ? 2 : 1;
      meter.displayed = Math.min(meter.target, meter.displayed + step);
      if (meter.displayed < 100) {
        meter.title = jobStatusLabels.processing?.label || "解析中";
      } else {
        meter.title = "全部完成";
        meter.label = "解析完成，可查看候选人与面试题";
      }
      renderParseMeter(meter);
    }
    if (meter.jobCompleted && meter.displayed >= 100 && meter.pendingResultsJobId && liveSupabase) {
      const jobId = meter.pendingResultsJobId;
      meter.pendingResultsJobId = null;
      markAllParseStepsComplete();
      stopJobPolling();
      stopSyntheticParseProgress();
      if (meter.animTimer) {
        window.clearInterval(meter.animTimer);
        meter.animTimer = null;
      }
      try {
        await loadResults(liveSupabase, jobId);
      } catch {
        // ignore
      }
    } else if (meter.jobCompleted && meter.displayed >= 100) {
      markAllParseStepsComplete();
    }
  }, 280);
}

function renderParseMeter(meter) {
  const percent = Math.max(0, Math.min(100, Math.round(meter.displayed)));
  const fill = document.getElementById("parse-meter-fill");
  const percentNode = document.getElementById("parse-meter-percent");
  const title = document.getElementById("parse-meter-title");
  const label = document.getElementById("parse-meter-label");
  if (fill) fill.style.width = `${percent}%`;
  if (percentNode) percentNode.textContent = `${percent}%`;
  if (title) title.textContent = meter.title;
  if (label) label.textContent = meter.label;
}

function updateParseMeter(stepStates, job) {
  const meter = getParseMeterState();
  if (!meter.startedAt) meter.startedAt = Date.now();
  meter.lastStepStates = stepStates || meter.lastStepStates;

  let percent = 0;
  let currentLabel = "准备开始";
  let activeStep = null;
  let waitingStep = null;

  // When backend already finished, keep showing staged labels from synthetic timeline
  // instead of snapping every step to "completed".
  const useStates = (meter.backendDone || job?.status === "completed") && meter.syntheticTimer
    ? stepStates
    : stepStates;

  for (const [step, weight] of Object.entries(PARSE_STEP_WEIGHTS)) {
    const state = useStates?.[step];
    const safeWeight = Number(weight);
    if (!Number.isFinite(safeWeight)) continue;
    if (state === "completed") {
      percent += safeWeight;
      continue;
    }
    if (state === "processing" || state === "uploading") {
      activeStep = step;
      percent += Math.round(safeWeight * 0.35);
      currentLabel = PARSE_STEP_LABELS[step] || "处理中…";
      break;
    }
    waitingStep = step;
    currentLabel = {
      upload: "等待上传与校验…",
      parse_jd: "等待解析岗位 JD…",
      parse_resume: "等待解析候选人简历…",
      match: "等待智能匹配打分…",
      questions: "等待生成面试题…",
    }[step] || "等待下一步…";
    break;
  }

  const stepKey = activeStep || waitingStep || null;
  if (stepKey !== meter.activeStepKey) {
    meter.activeStepKey = stepKey;
    meter.stepStartedAt = Date.now();
  }

  if (activeStep && !(meter.backendDone || job?.status === "completed")) {
    const stepWeight = Number(PARSE_STEP_WEIGHTS[activeStep]) || 0;
    const base = Math.round(stepWeight * 0.35);
    const room = Math.max(0, stepWeight - base - 1);
    const elapsedStep = Math.max(0, Date.now() - (meter.stepStartedAt || Date.now()));
    const creep = Math.min(room, Math.floor(elapsedStep / 900));
    percent += creep;
  }

  if (job?.status === "completed" || meter.backendDone) {
    meter.backendDone = true;
    meter.jobCompleted = true;
    // Do NOT force percent=100 here — timeline cap decides when 100 is allowed.
    if (!activeStep && !waitingStep) {
      currentLabel = "正在汇总匹配结果与面试题…";
    }
  } else if (job?.status === "failed") {
    currentLabel = job.error_message || "解析失败";
  }

  percent = Number(percent);
  if (!Number.isFinite(percent)) percent = 0;
  percent = Math.max(0, Math.min(96, Math.round(percent)));

  const elapsed = Date.now() - meter.startedAt;
  const cap = timelinePercentCap(elapsed, meter.backendDone);
  const capped = Math.min(percent, cap);
  // Also allow timeline itself to advance the bar during long waits.
  const merged = Math.max(capped, Math.min(cap, meter.floor || 0));

  meter.floor = Math.max(meter.floor || 0, merged);
  meter.target = Math.max(meter.target || 0, Math.min(cap, Math.max(meter.floor, merged)));
  // Hard clamp: never let target exceed timeline until unlock.
  meter.target = Math.min(meter.target, cap);

  meter.label = currentLabel;
  meter.title = meter.displayed >= 100
    ? "全部完成"
    : (jobStatusLabels[job?.status === "completed" ? "processing" : job?.status]?.label || "解析中");

  if (meter.displayed <= 0 && meter.target > 0) {
    meter.displayed = Math.min(meter.target, 2);
  }
  ensureParseMeterAnimation();
  renderParseMeter(meter);
}

function updateParseTrackSummary(stepStates, job) {
  const node = document.getElementById("parse-track-summary-text");
  const wrap = document.getElementById("parse-track-wrap");
  if (!node) return;
  const order = PARSE_STEP_ORDER;
  const done = order.filter((step) => stepStates?.[step] === "completed").length;
  const active = order.find((step) => ["processing", "uploading"].includes(stepStates?.[step]));
  if (job?.status === "completed" || done === order.length) {
    node.textContent = "全部完成";
  } else if (active) {
    node.textContent = `${PARSE_STEP_LABELS[active] || "进行中"}`.replace(/…$/, "").replace(/^正在/, "");
  } else {
    node.textContent = `${done}/${order.length} 完成`;
  }
  // Phone: keep 五步进度 collapsed so Agent 链 stays above the fold.
  if (wrap && window.matchMedia("(max-width: 720px)").matches) {
    if (job?.status === "completed") wrap.open = false;
    else if (active || done > 0) wrap.open = false;
  }
}

function setParseStep(step, status) {
  const node = document.querySelector(`.parse-step[data-step="${step}"]`);
  if (!node) return;
  node.classList.remove("done", "now", "wait");
  const state = node.querySelector(".parse-state");
  if (status === "completed") {
    node.classList.add("done");
    if (state) state.textContent = "完成";
  } else if (status === "processing" || status === "uploading") {
    node.classList.add("now");
    if (state) state.textContent = "进行中";
  } else if (status === "failed") {
    node.classList.add("wait");
    if (state) state.textContent = "失败";
  } else {
    node.classList.add("wait");
    if (state) state.textContent = "等待";
  }
}

function showUploadError(message) {
  if (statusNode) statusNode.textContent = `上传失败：${message}`;
}

function wireDemoViews() {
  renderDemoTasks();
  renderDemoSettings();
  loadScreeningConfig(null).then((screeningConfig) => applyScreeningConfigToForm(screeningConfig));
  document.addEventListener("viewchange", (event) => {
    if (event.detail.view === "tasks") renderDemoTasks();
    if (event.detail.view === "settings") {
      renderDemoSettings();
      loadScreeningConfig(null).then((screeningConfig) => applyScreeningConfigToForm(screeningConfig));
    }
  });
}

function wireLiveViews(supabase, sessionReady) {
  sessionReady.then((user) => renderSettings(config, user));
  loadScreeningConfig(supabase).then((screeningConfig) => applyScreeningConfigToForm(screeningConfig));
  document.addEventListener("viewchange", (event) => {
    if (event.detail.view === "tasks") loadTaskHistory(supabase);
    if (event.detail.view === "results" && activeJobId) loadResults(supabase, activeJobId);
    if (event.detail.view === "settings") {
      sessionReady.then((user) => renderSettings(config, user));
      loadScreeningConfig(supabase).then((screeningConfig) => applyScreeningConfigToForm(screeningConfig));
    }
  });
}

function renderDemoTasks() {
  const list = document.getElementById("task-list");
  if (!list) return;
  const demoJobs = [
    { title: "AI Agent / LLM 应用工程师", status: "completed", candidate_count: 12, processed_count: 12, created_at: "2026-08-13T10:42:00Z" },
    { title: "后端工程师", status: "processing", candidate_count: 18, processed_count: 14, created_at: "2026-08-12T02:00:00Z" },
  ];
  list.innerHTML = demoJobs.map((job) => taskRowMarkup(job)).join("");
  list.querySelectorAll(".task-row").forEach((row, index) => {
    row.addEventListener("click", () => {
      if (demoJobs[index].status === "completed") window.showView?.("results");
      else window.showView?.("upload");
    });
  });
}

function renderDemoSettings() {
  setText("settings-workspace-id", "演示模式");
  setText("settings-connection", "未连接 Supabase");
  setText("settings-auth-mode", "演示账户");
  setText("settings-user-id", "Martin");
  setText("settings-session-state", "本地演示");
}

function renderSettings(configValue, user) {
  setText("settings-workspace-id", configValue.workspaceId);
  setText("settings-connection", configValue.url ? "已连接" : "未配置");
  setText("settings-auth-mode", user?.is_anonymous ? "匿名会话" : "已登录");
  setText("settings-user-id", user?.id || "—");
  setText("settings-session-state", user ? "已就绪" : "未初始化");
}

async function loadTaskHistory(supabase) {
  const list = document.getElementById("task-list");
  if (!list) return;
  list.innerHTML = '<p class="task-empty">加载中…</p>';
  const { data: jobs, error } = await supabase
    .from("screening_jobs")
    .select("id,title,status,candidate_count,processed_count,created_at,error_message")
    .eq("workspace_id", config.workspaceId)
    .order("created_at", { ascending: false })
    .limit(30);
  if (error) {
    list.innerHTML = `<p class="task-empty">加载失败：${escapeHtml(error.message)}</p>`;
    return;
  }
  if (!jobs?.length) {
    list.innerHTML = '<p class="task-empty">暂无任务记录，去发起一次筛选吧。</p>';
    return;
  }
  list.innerHTML = jobs.map((job) => taskRowMarkup(job)).join("");
  list.querySelectorAll(".task-row").forEach((row) => {
    row.addEventListener("click", () => openJob(supabase, row.dataset.jobId));
  });
}

async function openJob(supabase, jobId) {
  const { data: job } = await supabase
    .from("screening_jobs")
    .select("id,title,status,candidate_count,processed_count,error_message")
    .eq("id", jobId)
    .single();
  if (!job) return;
  if (job.status === "completed") {
    await loadResults(supabase, job.id);
    window.showView?.("results");
    return;
  }
  const { data: documents } = await supabase
    .from("documents")
    .select("original_filename,document_type,size_bytes")
    .eq("screening_job_id", job.id)
    .order("document_type");
  if (documents?.length) showSubmittedPanel(job, documents);
  window.showView?.("upload");
}

function taskRowMarkup(job) {
  const info = jobStatusLabels[job.status] || { label: job.status };
  const progress = job.candidate_count
    ? `已处理 ${job.processed_count || 0} / ${job.candidate_count} 份`
    : "等待开始";
  const time = formatTaskTime(job.created_at);
  const jobId = job.id ? ` data-job-id="${job.id}"` : "";
  return `<div class="task-row"${jobId}><div><b>${escapeHtml(job.title || "未命名任务")}</b><span>${progress}</span></div><time class="task-meta">${escapeHtml(time)}</time><span class="task-status" data-state="${escapeHtml(job.status)}">${escapeHtml(info.label)}</span></div>`;
}

function formatTaskTime(value) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "—";
  return date.toLocaleString("zh-CN", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

function setText(id, value) {
  const node = document.getElementById(id);
  if (node) node.textContent = value || "—";
}

function wireScreeningConfigButtons() {
  document.getElementById("save-screening-config")?.addEventListener("click", async () => {
    const screeningConfig = readScreeningConfigFromForm();
    const validationError = validateScreeningConfig(screeningConfig);
    if (validationError) {
      setScreeningConfigStatus(validationError, true);
      return;
    }
    try {
      await persistScreeningConfig(liveSupabase, screeningConfig);
      setScreeningConfigStatus("规则已保存，将在下次筛选任务中生效。");
    } catch (error) {
      setScreeningConfigStatus(`保存失败：${error.message}`, true);
    }
  });

  document.getElementById("reset-screening-config")?.addEventListener("click", async () => {
    applyScreeningConfigToForm(DEFAULT_SCREENING_CONFIG);
    try {
      await persistScreeningConfig(liveSupabase, DEFAULT_SCREENING_CONFIG);
      setScreeningConfigStatus("已恢复默认规则。");
    } catch (error) {
      setScreeningConfigStatus(`恢复默认失败：${error.message}`, true);
    }
  });
}

function mergeScreeningConfig(config) {
  return {
    hard_gates: {
      ...DEFAULT_SCREENING_CONFIG.hard_gates,
      ...(config?.hard_gates || {}),
      min_years: { ...DEFAULT_SCREENING_CONFIG.hard_gates.min_years, ...(config?.hard_gates?.min_years || {}) },
      education: { ...DEFAULT_SCREENING_CONFIG.hard_gates.education, ...(config?.hard_gates?.education || {}) },
      must_have_skills: {
        ...DEFAULT_SCREENING_CONFIG.hard_gates.must_have_skills,
        ...(config?.hard_gates?.must_have_skills || {}),
      },
    },
    score_thresholds: {
      ...DEFAULT_SCREENING_CONFIG.score_thresholds,
      ...(config?.score_thresholds || {}),
    },
  };
}

async function loadScreeningConfig(supabase) {
  if (!supabase) {
    const stored = localStorage.getItem(SCREENING_CONFIG_STORAGE_KEY);
    return mergeScreeningConfig(stored ? JSON.parse(stored) : null);
  }
  const { data, error } = await supabase
    .from("workspaces")
    .select("screening_config")
    .eq("id", config.workspaceId)
    .single();
  if (error) {
    const stored = localStorage.getItem(SCREENING_CONFIG_STORAGE_KEY);
    if (stored) return mergeScreeningConfig(JSON.parse(stored));
    return mergeScreeningConfig(null);
  }
  return mergeScreeningConfig(data?.screening_config);
}

async function persistScreeningConfig(supabase, screeningConfig) {
  localStorage.setItem(SCREENING_CONFIG_STORAGE_KEY, JSON.stringify(screeningConfig));
  if (!supabase) return;
  const { error } = await supabase
    .from("workspaces")
    .update({ screening_config: screeningConfig })
    .eq("id", config.workspaceId);
  if (error) throw error;
}

function readScreeningConfigFromForm() {
  return mergeScreeningConfig({
    hard_gates: {
      min_years: { enabled: document.getElementById("gate-min-years")?.checked ?? true },
      education: { enabled: document.getElementById("gate-education")?.checked ?? true },
      must_have_skills: {
        enabled: document.getElementById("gate-skills")?.checked ?? true,
        min_coverage: Number(document.getElementById("gate-skill-coverage")?.value || 100) / 100,
      },
    },
    score_thresholds: {
      recommend_min: Number(document.getElementById("threshold-recommend")?.value || 75),
      review_min: Number(document.getElementById("threshold-review")?.value || 60),
    },
  });
}

function applyScreeningConfigToForm(screeningConfig) {
  const cfg = mergeScreeningConfig(screeningConfig);
  const minYears = document.getElementById("gate-min-years");
  const education = document.getElementById("gate-education");
  const skills = document.getElementById("gate-skills");
  const coverage = document.getElementById("gate-skill-coverage");
  const recommend = document.getElementById("threshold-recommend");
  const review = document.getElementById("threshold-review");
  if (minYears) minYears.checked = cfg.hard_gates.min_years.enabled;
  if (education) education.checked = cfg.hard_gates.education.enabled;
  if (skills) skills.checked = cfg.hard_gates.must_have_skills.enabled;
  if (coverage) coverage.value = String(Math.round(cfg.hard_gates.must_have_skills.min_coverage * 100));
  if (recommend) recommend.value = String(cfg.score_thresholds.recommend_min);
  if (review) review.value = String(cfg.score_thresholds.review_min);
}

function validateScreeningConfig(screeningConfig) {
  const { recommend_min, review_min } = screeningConfig.score_thresholds;
  const coverage = screeningConfig.hard_gates.must_have_skills.min_coverage;
  if (recommend_min <= review_min) return "推荐分数线必须高于人工复核线。";
  if (recommend_min < 0 || recommend_min > 100 || review_min < 0 || review_min > 100) return "分数线须在 0–100 之间。";
  if (coverage < 0 || coverage > 1) return "技能覆盖率须在 0–100% 之间。";
  return null;
}

function setScreeningConfigStatus(message, isError = false) {
  const node = document.getElementById("screening-config-status");
  if (!node) return;
  node.textContent = message;
  node.style.color = isError ? "#c62828" : "var(--muted)";
}

async function initSamplePickers() {
  try {
    const response = await fetch("samples/manifest.json");
    if (!response.ok) throw new Error("无法加载样例清单");
    sampleManifest = await response.json();
    document.querySelectorAll(".sample-shuffle").forEach((button) => {
      button.addEventListener("click", () => renderSampleChips(button.dataset.type, true));
    });
    renderSampleChips("jd");
    renderSampleChips("resume");
  } catch (error) {
    document.querySelectorAll(".sample-picker").forEach((picker) => {
      picker.hidden = true;
    });
  }
}

function shuffleSamples(items) {
  return [...items].sort(() => Math.random() - 0.5);
}

function isSampleSelected(type, sampleId) {
  return type === "jd" ? selectedSampleIds.jd === sampleId : selectedSampleIds.resumes.has(sampleId);
}

function renderSampleChips(type, reshuffle = false) {
  if (!sampleManifest) return;
  const pool = type === "jd" ? sampleManifest.jd : sampleManifest.resumes;
  const container = document.getElementById(type === "jd" ? "jd-sample-chips" : "resume-sample-chips");
  if (!container || !pool?.length) return;
  const count = type === "jd" ? pool.length : Math.min(RESUME_SAMPLE_COUNT, pool.length);
  const visible = reshuffle || !container.dataset.rendered
    ? shuffleSamples(pool).slice(0, count)
    : JSON.parse(container.dataset.visible || "[]");
  container.dataset.rendered = "1";
  container.dataset.visible = JSON.stringify(visible);
  container.innerHTML = visible.map((sample) => `
    <button type="button" class="sample-chip${isSampleSelected(type, sample.id) ? " active" : ""}" data-type="${type}" data-sample-id="${escapeHtml(sample.id)}">
      ${escapeHtml(sample.title)}<small>${escapeHtml(sample.tag || "样例")}</small>
    </button>
  `).join("");
  container.querySelectorAll(".sample-chip").forEach((chip) => {
    chip.addEventListener("click", () => selectSample(chip.dataset.type, chip.dataset.sampleId));
  });
}

async function selectSample(type, sampleId) {
  if (!sampleManifest) return;
  const pool = type === "jd" ? sampleManifest.jd : sampleManifest.resumes;
  const sample = pool.find((item) => item.id === sampleId);
  if (!sample) return;
  try {
    const response = await fetch(sample.path);
    if (!response.ok) throw new Error(`无法读取样例：${sample.title}`);
    const blob = await response.blob();
    const file = new File(
      [blob],
      sample.filename,
      { type: "application/vnd.openxmlformats-officedocument.wordprocessingml.document" },
    );
    validateFile(file);
    if (type === "jd") {
      selected.jd = file;
      selectedSampleIds.jd = sampleId;
    } else {
      const index = selected.resumes.findIndex((resume) => resume.name === file.name);
      if (index >= 0) {
        selected.resumes.splice(index, 1);
        selectedSampleIds.resumes.delete(sampleId);
      } else {
        if (selected.resumes.length >= 20) throw new Error("单次最多选择 20 份简历。");
        selected.resumes.push(file);
        selectedSampleIds.resumes.add(sampleId);
      }
    }
    renderFiles();
    renderSampleChips(type);
  } catch (error) {
    showUploadError(error.message);
  }
}

function safeFilename(name) {
  return name.replace(/[^a-zA-Z0-9.\-_()]/g, "_");
}

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, (character) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;",
  }[character]));
}
