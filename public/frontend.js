import { checkerPresentation } from "./frontend-checker.js";
import { agentChainGroupedMarkup } from "./frontend-agent-chain.js";
import { renderRecruiterFeedback } from "./frontend-feedback.js";

const config = window.SUPABASE_CONFIG;
const statusNode = document.querySelector(".upload-side p");
const startButton = document.getElementById("start-screening");
const jdInput = document.getElementById("jd-input");
const resumeInput = document.getElementById("resume-input");
const selected = { jd: null, resumes: [] };
let liveSupabase = null;
let activeJobId = null;
let jobPollTimer = null;
/** Only follow this job on the upload page (in-flight session). Completed history stays in 任务记录. */
let followLiveJobId = null;
/** Latest completed screening payload used by「导出报告」. */
let latestResultsSnapshot = null;

wireExportReport();

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

prepareCandidateCards();

function prepareCandidateCards() {
  document.querySelectorAll("#result-list .person-card").forEach((card) => {
    const name = card.dataset.name || "候选人";
    card.tabIndex = 0;
    card.setAttribute("role", "link");
    card.setAttribute("aria-label", `查看 ${name} 的完整分析`);
    const detailButton = card.querySelector("button.detail");
    if (detailButton) {
      const label = document.createElement("span");
      label.className = "detail";
      label.setAttribute("aria-hidden", "true");
      label.textContent = "打开详情 →";
      detailButton.replaceWith(label);
    }
  });
}

document.getElementById("result-list").addEventListener("click", (event) => {
  const card = event.target.closest(".person-card");
  if (card) window.openCandidateDetail?.(card.dataset.name);
});

document.getElementById("result-list").addEventListener("keydown", (event) => {
  const card = event.target.closest(".person-card");
  if (!card || !["Enter", " "].includes(event.key)) return;
  event.preventDefault();
  window.openCandidateDetail?.(card.dataset.name);
});

if (!config?.url || !config?.anonKey || !config?.workspaceId) {
  if (statusNode) {
    statusNode.textContent = "当前为演示数据。复制 supabase-config.example.js 为 supabase-config.js 并填写项目配置后，即可启用真实上传与任务状态。";
  }
  setRuntimeMode("demo", "当前模式：静态产品示例");
  wireDemoOneClick();
  // Demo rendering reads jobStatusLabels, declared later in this module.
  queueMicrotask(wireDemoViews);
} else {
  // Keep the static demo fully offline. The browser SDK is fetched only when a
  // real Supabase project has been configured.
  const { createClient } = await import("https://esm.sh/@supabase/supabase-js@2.49.1");
  liveSupabase = createClient(config.url, config.anonKey);
  window.liveSupabase = liveSupabase;
  const supabase = liveSupabase;
  setRuntimeMode(
    config.allowAnonymousBootstrap === true ? "live-dev" : "live",
    config.allowAnonymousBootstrap === true ? "当前模式：真实样例处理" : "当前模式：真实数据处理"
  );
  resetUploadLists();
  const sessionReady = initializeSession(supabase);
  wireAuth(sessionReady);
  wireUpload(supabase);
  wireLiveViews(supabase, sessionReady);
  sessionReady
    .then((user) => {
      watchLatestJob(supabase);
      probeWorkerService();
      if (!user && statusNode && config.allowAnonymousBootstrap !== true) {
        statusNode.textContent = "请先登录工作区成员账号，再上传 JD 与简历。";
      }
    })
    .catch((error) => showUploadError(`会话初始化失败：${error.message}`));
}

function setRuntimeMode(mode, label) {
  const node = document.getElementById("runtime-mode");
  if (!node) return;
  node.dataset.mode = mode;
  node.textContent = label;
}

async function initializeSession(supabase) {
  const { data: { user: existingUser } } = await supabase.auth.getUser();
  let user = existingUser;

  if (!user && config.allowAnonymousBootstrap === true) {
    const { data, error } = await supabase.auth.signInAnonymously();
    if (error) throw error;
    user = data.user;
  }

  if (user?.is_anonymous && config.allowAnonymousBootstrap === true) {
    config.workspaceId = await bootstrapAnonymousWorkspace(supabase);
  } else if (!user && statusNode) {
    statusNode.textContent = "生产模式：请点击右上角账户按钮，使用邮箱密码登录工作区成员账号。";
  }

  return user;
}

async function bootstrapAnonymousWorkspace(supabase) {
  const { data, error } = await supabase.auth.getSession();
  if (error || !data.session?.access_token) {
    throw new Error("匿名会话初始化失败，请刷新页面重试。");
  }
  const response = await fetch(`${workerBaseUrl().replace(/\/$/, "")}/session/bootstrap`, {
    method: "POST",
    headers: { Authorization: `Bearer ${data.session.access_token}` },
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail || "无法创建匿名体验工作区，请稍后重试。");
  }
  const body = await response.json();
  if (!body.workspace_id) throw new Error("匿名体验工作区响应无效。");
  return body.workspace_id;
}

function wireAuth(sessionReady) {
  const action = document.getElementById("login-action");
  const name = document.getElementById("account-name");
  const role = document.getElementById("account-role");

  const refreshAccount = async () => {
    const { data: { user } } = await liveSupabase.auth.getUser();
    if (!user) {
      name.textContent = "未登录";
      role.textContent = "点击登录";
      action.disabled = false;
      action.setAttribute("aria-label", "登录工作区账号");
      return;
    }
    const label = user.email || (user.is_anonymous ? "匿名会话" : (user.id || "").slice(0, 8));
    name.textContent = label;
    role.textContent = user.is_anonymous ? "匿名体验工作区" : "工作区成员";
    action.disabled = false;
    action.setAttribute("aria-label", user.is_anonymous ? "匿名会话已就绪" : "账户菜单");
  };

  sessionReady.then(refreshAccount).catch(() => refreshAccount());

  action?.addEventListener("click", async () => {
    const { data: { user } } = await liveSupabase.auth.getUser();
    if (user && !user.is_anonymous) {
      const ok = window.confirm(`当前账号：${user.email || user.id}\n是否退出登录？`);
      if (!ok) return;
      await liveSupabase.auth.signOut();
      await refreshAccount();
      if (statusNode) statusNode.textContent = "已退出。请重新登录后再上传。";
      return;
    }
    if (user?.is_anonymous && config.allowAnonymousBootstrap === true) {
      window.alert("当前为匿名体验会话，无需邮箱登录。");
      return;
    }
    const email = window.prompt("工作区成员邮箱：");
    if (!email) return;
    const password = window.prompt("密码：");
    if (!password) return;
    const { error } = await liveSupabase.auth.signInWithPassword({ email: email.trim(), password });
    if (error) {
      window.alert(`登录失败：${error.message}`);
      return;
    }
    await refreshAccount();
    if (statusNode) statusNode.textContent = "登录成功。可以开始上传 JD 与简历。";
    watchLatestJob(liveSupabase);
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
      if (!selected.jd) throw new Error("请先选择 1 份 JD。");
      if (selected.resumes.length < 1) throw new Error("请至少选择 1 份候选人简历。");
      const { data: { user } } = await supabase.auth.getUser();
      if (!user) {
        throw new Error("请先点击右上角“登录工作区账号”，使用工作区成员邮箱登录。");
      }
      startButton.disabled = true;
      startButton.textContent = "正在解析…";
      resetParseMeterState();
      const previewDocs = [
        { original_filename: selected.jd.name, document_type: "jd", size_bytes: selected.jd.size },
        ...selected.resumes.map((file) => ({
          original_filename: file.name,
          document_type: "resume",
          size_bytes: file.size,
        })),
      ];
      showSubmittedPanel(
        { title: selected.jd.name.replace(/\.[^.]+$/, ""), status: "uploading" },
        previewDocs,
      );
      updateParseMeter({ upload: "processing" }, { status: "uploading" });
      beginSyntheticParseProgress();
      const jobId = await startScreening(supabase, user);
      await runOneClickParse(jobId);
      startButton.textContent = "解析完成 ✓";
      followLiveJobId = null;
      document.getElementById("upload")?.classList.remove("upload-busy");
      const donePanel = document.getElementById("submitted-panel");
      if (donePanel) donePanel.hidden = true;
      window.showView?.("results");
    } catch (error) {
      stopSyntheticParseProgress();
      document.getElementById("upload")?.classList.remove("upload-busy");
      const panel = document.getElementById("submitted-panel");
      if (panel) panel.hidden = true;
      startButton.disabled = false;
      startButton.textContent = "一键解析 →";
      showUploadError(error.message);
    }
  });

  document.getElementById("new-screening-btn")?.addEventListener("click", () => {
    try {
      followLiveJobId = null;
      activeJobId = null;
      latestResultsSnapshot = null;
      stopJobPolling();
      resetUploadWorkspace();
    } catch (error) {
      console.error("failed to reset screening workspace", error);
      showUploadError("无法重置本次筛选，请刷新页面后重试。");
    }
  });
}

async function startScreening(supabase, authenticatedUser = null) {
  if (!selected.jd) throw new Error("请先选择 1 份 JD。");
  if (selected.resumes.length < 1) throw new Error("请至少选择 1 份候选人简历。");
  if (selected.resumes.length > 20) throw new Error("单次最多上传 20 份简历。");

  const user = authenticatedUser || (await supabase.auth.getUser()).data.user;
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
  activeJobId = job.id;
  followLiveJobId = job.id;

  const docs = [{ file: selected.jd, type: "jd" }, ...selected.resumes.map((file) => ({ file, type: "resume" }))];
  const uploadedPaths = [];
  try {
    for (const document of docs) {
      validateFile(document.file);
      const path = `${config.workspaceId}/${job.id}/${document.type}/${crypto.randomUUID()}-${safeFilename(document.file.name)}`;
      const mimeType = documentMime(document.file);
      const { error: storageError } = await supabase.storage
        .from("screening-documents")
        .upload(path, document.file, { contentType: mimeType, upsert: false });
      if (storageError) throw storageError;
      uploadedPaths.push(path);

      const { error: documentError } = await supabase.from("documents").insert({
        workspace_id: config.workspaceId,
        screening_job_id: job.id,
        document_type: document.type,
        original_filename: document.file.name,
        storage_path: path,
        mime_type: mimeType,
        size_bytes: document.file.size,
        status: "validated",
      });
      if (documentError) throw documentError;
    }

    const { error: startError } = await supabase.rpc("start_screening", { target_job_id: job.id });
    if (startError) throw startError;
  } catch (error) {
    // Best-effort cleanup so a mid-upload failure does not leave orphaned storage or uploading jobs.
    if (uploadedPaths.length) {
      try { await supabase.storage.from("screening-documents").remove(uploadedPaths); } catch (_) { /* ignore */ }
    }
    try {
      await supabase.from("documents").delete().eq("screening_job_id", job.id);
      await supabase.from("screening_jobs").update({ status: "failed", error_message: "上传中断，已清理半成品任务" }).eq("id", job.id);
    } catch (_) { /* ignore */ }
    throw error;
  }
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
    "application/msword",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  ];
  if (!allowed.includes(documentMime(file))) throw new Error(`${file.name} 不是 PDF、DOC 或 DOCX 文件。`);
  if (file.size > 10 * 1024 * 1024) throw new Error(`${file.name} 超过 10MB 限制。`);
}

function documentMime(file) {
  const name = String(file?.name || "").toLowerCase();
  if (name.endsWith(".pdf")) return "application/pdf";
  if (name.endsWith(".docx")) return "application/vnd.openxmlformats-officedocument.wordprocessingml.document";
  if (name.endsWith(".doc")) return "application/msword";
  return String(file?.type || "").toLowerCase();
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
  const lower = file.name.toLowerCase();
  const ext = lower.endsWith(".docx") ? "DOCX" : lower.endsWith(".doc") ? "DOC" : "PDF";
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
  if (["queued", "processing"].includes(job.status) && !config.workerUrl) {
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
      const health = await response.json().catch(() => ({}));
      const openai = String(health.construction || "").includes("OpenAI") || String(health.checker || "").includes("OpenAI");
      setRuntimeMode(
        openai ? "live-openai" : "live-mock",
        openai ? "当前模式：真实模型处理 · OpenAI Agent" : "当前模式：真实样例处理 · Mock Agent",
      );
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
  // Never accept INTERNAL_API_TOKEN in the browser. Localhost Demo → loopback /dev.
  const isLocalWorker = ["localhost", "127.0.0.1"].includes(new URL(base, window.location.origin).hostname)
    || base.includes("127.0.0.1")
    || base.includes("localhost");
  let url = `${base}/dev/jobs/process`;
  if (!isLocalWorker) {
    const { data, error } = await liveSupabase.auth.getSession();
    if (error || !data.session?.access_token) throw new Error("登录会话已失效，请刷新页面。");
    url = `${base.replace(/\/$/, "")}/jobs/process`;
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
  followLiveJobId = null;
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
  if (statusNode) statusNode.textContent = "选择样例或上传文件后，点击「一键解析」即可。历史任务请到左侧「任务」查看。";
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
  window.__agentChainSteps = [];
  if (chainMode) chainMode.textContent = "等待步骤事件…";
  // Reset five-step track UI.
  document.querySelectorAll("#parse-track .parse-step").forEach((node) => {
    node.classList.remove("done", "now");
    node.classList.add("wait");
    const state = node.querySelector(".parse-state");
    if (state) state.textContent = "等待";
  });
  const wrap = document.getElementById("parse-track-wrap");
  if (wrap) wrap.open = true;
}

function showSubmittedPanel(job, documents) {
  const panel = document.getElementById("submitted-panel");
  const list = document.getElementById("submitted-list");
  const status = document.getElementById("submitted-status");
  const desc = document.getElementById("submitted-desc");
  const title = document.getElementById("submitted-title");
  const viewResults = document.getElementById("view-results-btn");
  const uploadView = document.getElementById("upload");
  if (!panel || !list) return;

  const docs = Array.isArray(documents) ? documents : [];
  const info = jobStatusLabels[job.status] || { label: job.status, desc: "" };
  const wasHidden = panel.hidden;
  panel.hidden = false;
  uploadView?.classList.add("upload-busy");
  title.textContent = job.title || "本次筛选";
  status.textContent = info.label;
  status.dataset.state = job.status;
  desc.textContent = getJobStatusDesc(job);
  list.innerHTML = docs.length
    ? docs.map(submittedDocMarkup).join("")
    : '<p class="file-empty">本次任务没有可展示的上传文件，仍可查看下方智能体步骤与错误信息。</p>';
  if (viewResults) {
    viewResults.hidden = job.status !== "completed";
    viewResults.className = job.status === "completed" ? "primary" : "outline";
  }
  if (wasHidden) {
    panel.scrollIntoView({ behavior: "instant", block: "start" });
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
  activeJobId = job?.id || jobId;
  updateJobProgress(job);
  // Upload page only shows live progress for the job the user is actively following.
  const following = followLiveJobId && jobId === followLiveJobId;
  if (following && documents?.length) showSubmittedPanel(job, documents);
  if (following) {
    await updateParseProgress(supabase, job);
    await refreshAgentChain(supabase, jobId);
  }
  const meter = getParseMeterState();
  if (following && job.status === "completed") {
    meter.pendingResultsJobId = jobId;
    meter.backendDone = true;
    meter.jobCompleted = true;
    ensureParseMeterAnimation();
  }
  if (job.status === "failed" || job.status === "cancelled") {
    stopJobPolling();
    stopSyntheticParseProgress();
    if (following) followLiveJobId = null;
  }
  if (following && job.status === "completed") {
    // Keep panel until user clicks 查看结果 / 再发起一次; stop polling.
    stopJobPolling();
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
    window.__agentChainSteps = steps;
    const mode = data?.mode || "—";
    const stage = data?.state?.stage || data?.status || "waiting";
    if (modeNode) modeNode.textContent = `mode=${mode} · ${stage} · ${steps.length} steps`;
    if (!steps.length) {
      list.innerHTML = '<p class="agent-chain-empty">等待 Worker 写入步骤事件…</p>';
      return;
    }
    const nearBottom = list.scrollHeight - list.scrollTop - list.clientHeight < 48;
    list.innerHTML = agentChainGroupedMarkup(steps);
    if (nearBottom || steps.length <= 6) {
      list.scrollTop = list.scrollHeight;
    }
  } catch {
    if (modeNode) modeNode.textContent = "agent_runs 暂不可读";
  }
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

    const inFlight = ["queued", "processing", "uploading"].includes(job.status);
    // Never dump a finished historical job onto「发起筛选」.
    // Only attach live progress when the user started/opened that job.
    if (followLiveJobId === job.id) {
      const { data: documents } = await supabase
        .from("documents")
        .select("original_filename,document_type,size_bytes")
        .eq("screening_job_id", job.id)
        .order("document_type");
      if (documents?.length) showSubmittedPanel(job, documents);
      await updateParseProgress(supabase, job);
      await refreshAgentChain(supabase, job.id);
      if (inFlight) startJobPolling(supabase, job.id);
      else stopJobPolling();
    } else if (inFlight && !followLiveJobId) {
      // Background job exists, but user is preparing a new screening — leave upload clean.
      stopJobPolling();
    } else {
      stopJobPolling();
    }

    if (job.status === "completed" && !document.getElementById("upload")?.classList.contains("upload-busy")) {
      // Prefetch results quietly for 候选人页; do not hijack upload view.
      await loadResults(supabase, job.id);
    }
    if (document.getElementById("tasks")?.classList.contains("active")) loadTaskHistory(supabase);
    await refreshRecentJobsHint(supabase);
  };
  refresh();
  supabase
    .channel("screening-jobs")
    .on("postgres_changes", { event: "*", schema: "public", table: "screening_jobs", filter: `workspace_id=eq.${config.workspaceId}` }, refresh)
    .subscribe();
}

async function refreshRecentJobsHint(supabase) {
  const box = document.getElementById("upload-recent-jobs");
  if (!box) return;
  const { data: jobs } = await supabase
    .from("screening_jobs")
    .select("id,title,status,created_at,candidate_count,processed_count")
    .eq("workspace_id", config.workspaceId)
    .order("created_at", { ascending: false })
    .limit(5);
  if (!jobs?.length) {
    box.innerHTML = '<p class="upload-recent-empty">还没有历史任务。完成一次筛选后会出现在这里。</p>';
    return;
  }
  box.innerHTML = jobs.map((job) => {
    const info = jobStatusLabels[job.status] || { label: job.status };
    return `<button type="button" class="upload-recent-item" data-job-id="${escapeHtml(job.id)}">
      <span><b>${escapeHtml(job.title || "未命名任务")}</b><small>${escapeHtml(info.label)} · ${escapeHtml(formatTaskTime(job.created_at))}</small></span>
      <em>${job.status === "completed" ? "查看" : "打开"}</em>
    </button>`;
  }).join("");
  box.querySelectorAll(".upload-recent-item").forEach((btn) => {
    btn.addEventListener("click", () => openJob(supabase, btn.dataset.jobId));
  });
}

async function loadResults(supabase, jobId) {
  const list = document.getElementById("result-list");
  try {
    const [{ data: matches, error: matchError }, { data: profiles }, packsResult, jobResult, requirementsResult] = await Promise.all([
      supabase.from("match_results").select("*").eq("screening_job_id", jobId).order("score", { ascending: false }),
      supabase.from("candidate_profiles").select("id,display_name,profile").eq("screening_job_id", jobId),
      supabase.from("question_packs").select("candidate_profile_id,questions,followups").eq("screening_job_id", jobId),
      supabase.from("screening_jobs").select("id,title,location,status,created_at,candidate_count").eq("id", jobId).maybeSingle(),
      supabase.from("job_requirements").select("title,requirements,hard_gates").eq("screening_job_id", jobId).maybeSingle(),
    ]);
    if (matchError) throw matchError;
    const packs = packsResult?.data || [];
    const job = jobResult?.data || null;
    const requirementRow = requirementsResult?.data || null;
    const requirements = requirementRow?.requirements && typeof requirementRow.requirements === "object"
      ? requirementRow.requirements
      : {};
    const jobDescription = {
      title: requirementRow?.title || requirements.title || job?.title || "本次筛选岗位",
      location: job?.location || "",
      minYears: Number(requirements.min_years) || 0,
      education: requirements.education || "",
      mustHaveSkills: Array.isArray(requirements.must_have_skills) ? requirements.must_have_skills : [],
      niceToHaveSkills: Array.isArray(requirements.nice_to_have_skills) ? requirements.nice_to_have_skills : [],
      summary: requirements.summary || requirements.job_summary || "",
      rawText: String(requirements.raw_text || "").slice(0, 4000),
    };
    const byProfile = new Map((profiles || []).map((profile) => [profile.id, profile]));
    const byPack = new Map((packs || []).map((pack) => [pack.candidate_profile_id, pack]));
    if (!list) return;
    if (!matches?.length) {
      latestResultsSnapshot = null;
      updateResultsSummary([]);
      list.innerHTML = '<p class="task-empty">这次筛选还没有候选人结果。请回到「发起筛选」，点「再发起一次」或重新选样例后点「一键解析」。解析服务需保持 <code>./dev.sh</code> 运行中。</p>';
      return;
    }
    updateResultsSummary(matches);
    window.profiles ||= {};
    const candidates = matches.map((match) => {
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
      const SCORE_LABELS = {
        skill: "技能匹配",
        skill_coverage: "技能覆盖",
        skills: "技能匹配",
        experience: "经验匹配",
        education: "学历匹配",
        project_relevance: "项目相关度",
        risk: "风险控制",
        text: "文本相关度",
        text_score: "文本相关度",
        text_tfidf: "文本相关度",
        evidence: "证据质量",
        evidence_quality: "证据质量",
        score_deterministic: "规则分",
        score_llm: "模型辅助分",
        score_total: "综合分",
        required_coverage: "必备覆盖率",
        preferred_coverage: "加分覆盖率",
      };
      const SCORE_HIDE = new Set([
        "questions", "followups", "checker_demotion", "checker_status", "score_llm_source",
        "missing_required", "matched_required", "text_semantic", "text_semantic_source",
        "text_tfidf", "years_reestimated", "checker_correction", "checker_audit",
      ]);
      const SCORE_ORDER = [
        "skill", "skills", "skill_coverage", "experience", "education",
        "required_coverage", "preferred_coverage", "text", "text_score", "evidence", "evidence_quality",
        "score_deterministic", "score_llm", "score_total", "project_relevance", "risk",
      ];
      const scoreEntries = SCORE_ORDER
        .filter((key) => !SCORE_HIDE.has(key) && typeof breakdown[key] === "number")
        .map((key) => [SCORE_LABELS[key] || key, Number(breakdown[key]) || 0])
        .filter((entry, index, arr) => arr.findIndex((other) => other[0] === entry[0]) === index);
      const resumeEvidence = evidence
        .filter((item) => !item?.source || item.source === "resume" || item.source === "llm")
        .map((item) => ({
          quote: item?.quote || item?.text || String(item || ""),
          text: item?.quote || item?.text || String(item || ""),
          source: item?.source || "resume",
          page: item?.page,
          evidence_id: item?.evidence_id,
        }))
        .filter((item) => item.quote && !/^【(岗位|任职|工作地点|加分)/.test(item.quote));
      const displayEvidence = resumeEvidence.length
        ? resumeEvidence
        : evidence.map((item) => ({
          quote: item?.quote || item?.text || String(item || ""),
          text: item?.quote || item?.text || String(item || ""),
          source: item?.source || "resume",
          page: item?.page,
          evidence_id: item?.evidence_id,
        })).filter((item) => item.quote);
      const skills = Array.isArray(profile?.profile?.skills) ? profile.profile.skills : [];
      const checker = checkerPresentation(breakdown, match, risks, displayEvidence);
      const drawerProfile = {
        candidateProfileId: match.candidate_profile_id,
        screeningJobId: jobId,
        jobTitle: job?.title || "",
        jobDescription,
        skills,
        role: `${profile?.profile?.years_experience || 0} 年经验`,
        score: Math.round(Number(match.score) || 0),
        decision: { recommend: "建议优先面试", review: "建议人工复核", reject: "当前不建议推进" }[match.decision] || "已完成分析",
        summary: checker.summary,
        gates: checker.gates,
        scores: scoreEntries,
        checkerAudit: checker.audit,
        quotes: displayEvidence,
        evidenceIds: displayEvidence.map((item) => item.evidence_id).filter(Boolean),
        risks: risks.map((item) => (typeof item === "string" ? item : item?.text || "")).filter(Boolean),
        questions,
        followups,
        question: match.interview_question || followups[0]?.question || questions[0]?.question || "",
      };
      window.profiles[name] = drawerProfile;
      return {
        name,
        years: profile?.profile?.years_experience || 0,
        skills,
        decision: match.decision || "review",
        decisionLabel: drawerProfile.decision,
        score: drawerProfile.score,
        hardGatePass: Boolean(match.hard_gate_pass),
        checkerDegraded: checker.degraded,
        checkerStatus: checker.status,
        checkerAudit: checker.audit,
        summary: drawerProfile.summary,
        evidence: displayEvidence.map((item) => item.quote || item.text || item).filter(Boolean),
        risks: risks.map((item) => (typeof item === "string" ? item : item?.text || "")).filter(Boolean),
        scores: scoreEntries,
        gates: checker.gates,
        questions,
        followups,
      };
    });
    window.dispatchEvent(new CustomEvent("profilesupdated"));
    latestResultsSnapshot = {
      jobId,
      title: job?.title || document.querySelector("#results .intro p")?.textContent || "筛选任务",
      createdAt: job?.created_at || new Date().toISOString(),
      candidates,
    };
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
      const breakdown = match.score_breakdown || {};
      const checkerDegraded = Boolean(breakdown.checker_degraded);
      const coverage = Math.round(Number(breakdown.required_coverage) || 0);
      const risks = (match.risks || [])
        .map((item) => (typeof item === "string" ? item : item?.text || ""))
        .filter(Boolean);
      const auditIssue = breakdown.checker_audit?.issues?.[0];
      const attention = auditIssue?.note || risks[0] || "";
      return `<article class="person-card" data-kind="${escapeHtml(kind)}" data-name="${escapeHtml(name)}" role="link" tabindex="0" aria-label="查看 ${escapeHtml(name)} 的完整分析">
      <div class="person-head"><div class="initial">${escapeHtml(name.slice(0, 1))}</div><div><h3>${escapeHtml(name)}</h3><p>${profile?.profile?.years_experience || 0} 年经验</p></div><div class="ring" style="--score:${Number(match.score) || 0}"><span>${Math.round(Number(match.score) || 0)}</span></div></div>
      <div class="tags">${skills.map((skill) => `<span class="tag">${escapeHtml(skill)}</span>`).join("")}</div>
      <div class="decision-facts">
        <span class="${match.hard_gate_pass ? "fact-pass" : "fact-risk"}">硬门槛 ${match.hard_gate_pass ? "通过" : "未通过"}</span>
        <span>必备覆盖 ${coverage}%</span>
        <span>证据 ${evidence.length} 条</span>
      </div>
      <div class="evidence"><strong>匹配结果：</strong>${escapeHtml(evidence[0]?.text || "等待分析证据")}</div>
      ${attention ? `<div class="card-attention"><strong>${auditIssue ? `AI 质检 · ${escapeHtml(auditIssue.severity || "提示")}` : "风险提示"}：</strong>${escapeHtml(attention)}</div>` : ""}
      ${checkerDegraded ? '<div class="card-attention"><strong>质检状态：</strong>未通过/不可用，结果已标记为人工复核。</div>' : ""}
      <div class="person-footer"><span class="badge ${escapeHtml(kind)}">${escapeHtml(label)}</span><span class="qcount">${questionCount ? `${questionCount} 道面试题` : "暂无面试题"}</span><span class="detail" aria-hidden="true">打开详情 →</span></div>
    </article>`;
    }).join("");
  } catch (error) {
    latestResultsSnapshot = null;
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
  const meta = document.getElementById("live-pipeline-meta");
  const progress = meta?.querySelector("span");
  if (progress) {
    progress.textContent = job.candidate_count
      ? `已处理 ${job.processed_count || 0} / ${job.candidate_count} 份`
      : (jobStatusLabels[job.status]?.label || job.status);
  }
  if (meta) {
    const label = meta.childNodes[0];
    if (label && label.nodeType === Node.TEXT_NODE) {
      label.textContent = `${job.title || "当前筛选"} `;
    } else {
      meta.innerHTML = `${escapeHtml(job.title || "当前筛选")}<span>${escapeHtml(
        job.candidate_count
          ? `已处理 ${job.processed_count || 0} / ${job.candidate_count} 份`
          : (jobStatusLabels[job.status]?.label || job.status),
      )}</span>`;
    }
  }
  setPipelineNodes(pipelineStatesForJob(job));
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
  if (meter.displayed >= 100 && (job.status === "completed" || meter.backendDone)) {
    PARSE_STEP_ORDER.forEach((step) => { stepStates[step] = "completed"; });
  } else if (job.status === "completed") {
    Object.keys(stepStates).forEach((key) => { stepStates[key] = "completed"; });
  } else {
    mergeAgentChainIntoSteps(stepStates, window.__agentChainSteps || []);
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

function mergeAgentChainIntoSteps(stepStates, steps) {
  if (!Array.isArray(steps) || !steps.length) return stepStates;
  const blob = (step) => `${step.id || ""} ${step.label || ""}`.toLowerCase();
  const statusOf = (step) => String(step.status || "").toLowerCase();
  const done = (test) => steps.some((step) => test(blob(step)) && statusOf(step) === "completed");
  const running = (test) => steps.some((step) => test(blob(step)) && ["running", "processing"].includes(statusOf(step)));
  const bump = (key, next) => {
    const rank = { queued: 0, wait: 0, processing: 1, uploading: 1, completed: 2, failed: 2 };
    if ((rank[next] || 0) > (rank[stepStates[key]] || 0)) stepStates[key] = next;
  };
  if (done((text) => text.includes("parse_jd"))) bump("parse_jd", "completed");
  else if (running((text) => text.includes("parse_jd"))) bump("parse_jd", "processing");
  if (done((text) => /parse_resume|解析候选人/.test(text))) bump("parse_resume", "completed");
  else if (running((text) => /parse_resume|解析候选人/.test(text))) bump("parse_resume", "processing");
  if (done((text) => /llm_judge|decision \+|match\.start|construction/.test(text))) bump("match", "completed");
  else if (running((text) => /llm_judge|decision \+|match\.start|construction/.test(text))) bump("match", "processing");
  if (done((text) => /generate_questions|checker/.test(text))) bump("questions", "completed");
  else if (running((text) => /generate_questions|checker|面试题/.test(text))) bump("questions", "processing");
  return stepStates;
}

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

function beginSyntheticParseProgress() {
  const meter = getParseMeterState();
  stopSyntheticParseProgress();
  if (!meter.startedAt) meter.startedAt = Date.now();
  meter.backendDone = false;
  meter.jobCompleted = false;
  meter.syntheticTimer = window.setInterval(() => {
    updateParseMeter(meter.lastStepStates || {
      upload: "processing",
      parse_jd: "queued",
      parse_resume: "queued",
      match: "queued",
      questions: "queued",
    }, { status: meter.backendDone ? "completed" : "processing" });
  }, 400);
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

function waitForParseMeterComplete(timeoutMs = 12000) {
  const meter = getParseMeterState();
  meter.backendDone = true;
  meter.jobCompleted = true;
  meter.finishedAt = meter.finishedAt || Date.now();
  ensureParseMeterAnimation();
  return new Promise((resolve) => {
    const started = Date.now();
    const timer = window.setInterval(() => {
      meter.target = 100;
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
    const done = meter.backendDone || meter.jobCompleted;
    if (done) meter.target = 100;
    const gap = meter.target - meter.displayed;
    if (gap > 0) {
      const step = done ? Math.max(2, Math.ceil(gap / 6)) : 1;
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

  for (const [step, weight] of Object.entries(PARSE_STEP_WEIGHTS)) {
    const state = stepStates?.[step];
    const safeWeight = Number(weight);
    if (!Number.isFinite(safeWeight)) continue;
    if (state === "completed") {
      percent += safeWeight;
      continue;
    }
    if (state === "processing" || state === "uploading") {
      activeStep = step;
      percent += Math.round(safeWeight * 0.28);
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
    const base = Math.round(stepWeight * 0.28);
    const room = Math.max(0, stepWeight - base - 2);
    const elapsedStep = Math.max(0, Date.now() - (meter.stepStartedAt || Date.now()));
    const creep = Math.min(room, Math.floor(elapsedStep / 2200));
    percent += creep;
  }

  if (job?.status === "completed" || meter.backendDone) {
    meter.backendDone = true;
    meter.jobCompleted = true;
    if (!activeStep && !waitingStep) {
      currentLabel = "正在汇总匹配结果与面试题…";
    }
  } else if (job?.status === "failed") {
    currentLabel = job.error_message || "解析失败";
  }

  percent = Number(percent);
  if (!Number.isFinite(percent)) percent = 0;
  const finished = meter.backendDone || job?.status === "completed";
  percent = Math.max(0, Math.min(finished ? 100 : 96, Math.round(percent)));

  meter.floor = Math.max(meter.floor || 0, percent);
  meter.target = finished ? 100 : Math.max(meter.floor, percent);

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
  const detail = `上传失败：${message}`;
  if (statusNode) statusNode.textContent = detail;
  notifyUi(detail);
}

function greetingForNow(date = new Date()) {
  const hour = date.getHours();
  if (hour < 5) return "夜深了";
  if (hour < 12) return "早上好";
  if (hour < 18) return "下午好";
  return "晚上好";
}

function startOfDay(date) {
  const next = new Date(date);
  next.setHours(0, 0, 0, 0);
  return next;
}

function addDays(date, days) {
  const next = new Date(date);
  next.setDate(next.getDate() + days);
  return next;
}

function dayKey(date) {
  const d = new Date(date);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

function relativeTimeLabel(value) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "—";
  const diffMs = Date.now() - date.getTime();
  const mins = Math.round(diffMs / 60000);
  if (mins < 1) return "刚刚";
  if (mins < 60) return `${mins} 分钟前`;
  const hours = Math.round(mins / 60);
  if (hours < 24) return `${hours} 小时前`;
  const days = Math.round(hours / 24);
  if (days === 1) return "昨天";
  if (days < 7) return `${days} 天前`;
  return formatTaskTime(value);
}

function setDashGreeting(extra = "") {
  const greet = document.getElementById("dash-greeting");
  const sub = document.getElementById("dash-subtitle");
  if (greet) greet.textContent = greetingForNow();
  if (sub) {
    sub.textContent = extra || "这里是你的招聘筛选概览，数据来自当前工作区。";
  }
}

function setStatValue(key, value, deltaText = "", deltaKind = "") {
  const card = document.querySelector(`#dash-stats [data-stat="${key}"]`);
  if (!card) return;
  const num = card.querySelector(".stat-num");
  const delta = card.querySelector(".stat-delta");
  if (num) num.innerHTML = value;
  if (delta) {
    if (deltaText) {
      delta.hidden = false;
      delta.textContent = deltaText;
      delta.className = `stat-delta ${deltaKind || "flat"}`.trim();
    } else {
      delta.hidden = true;
      delta.textContent = "";
    }
  }
}

function setPipelineNodes(states) {
  document.querySelectorAll("#live-pipeline .pipe-node").forEach((node) => {
    const state = states[node.dataset.pipe] || "queued";
    node.classList.toggle("done", state === "completed");
    node.classList.toggle("now", state === "processing");
    const icon = node.querySelector(".pipe-icon");
    if (!icon) return;
    if (state === "completed") icon.textContent = "✓";
    else if (state === "processing") icon.textContent = "…";
    else icon.textContent = { parse: "1", extract: "2", match: "3", questions: "4" }[node.dataset.pipe] || "·";
  });
}

function pipelineStatesForJob(job) {
  if (!job) {
    return { parse: "queued", extract: "queued", match: "queued", questions: "queued" };
  }
  if (job.status === "completed") {
    return { parse: "completed", extract: "completed", match: "completed", questions: "completed" };
  }
  if (job.status === "failed") {
    return { parse: "completed", extract: "completed", match: "failed", questions: "queued" };
  }
  if (job.status === "uploading" || job.status === "queued") {
    return { parse: "processing", extract: "queued", match: "queued", questions: "queued" };
  }
  // processing: estimate from counts
  const total = Number(job.candidate_count) || 0;
  const done = Number(job.processed_count) || 0;
  if (total > 0 && done >= total) {
    return { parse: "completed", extract: "completed", match: "completed", questions: "processing" };
  }
  if (done > 0) {
    return { parse: "completed", extract: "completed", match: "processing", questions: "queued" };
  }
  return { parse: "completed", extract: "processing", match: "queued", questions: "queued" };
}

function renderDashboardPipeline(job, { onOpen } = {}) {
  const meta = document.getElementById("live-pipeline-meta");
  const actions = document.getElementById("dash-pipeline-actions");
  const openBtn = document.getElementById("dash-open-job");
  setPipelineNodes(pipelineStatesForJob(job));
  if (!meta) return;
  if (!job) {
    meta.innerHTML = '还没有进行中的筛选。<span>去发起一次</span>';
    if (actions) actions.hidden = true;
    return;
  }
  const info = jobStatusLabels[job.status] || { label: job.status };
  const progress = job.candidate_count
    ? `已处理 ${job.processed_count || 0} / ${job.candidate_count} 份`
    : info.label;
  meta.innerHTML = `${escapeHtml(job.title || "未命名任务")}<span>${escapeHtml(progress)}</span>`;
  if (actions) {
    actions.hidden = false;
    if (openBtn) {
      openBtn.textContent = job.status === "completed" ? "查看结果" : "打开任务";
      openBtn.onclick = () => onOpen?.(job);
    }
  }
}

function renderDashboardActivity(items, { onOpen } = {}) {
  const box = document.getElementById("dash-activity");
  if (!box) return;
  if (!items.length) {
    box.innerHTML = '<p class="dash-empty">暂无动态。完成一次筛选后，任务进度会出现在这里。</p>';
    return;
  }
  box.innerHTML = items.map((item) => `
    <button type="button" class="act" data-job-id="${escapeHtml(item.jobId || "")}">
      <i class="dot" style="background:${escapeHtml(item.color || "#177247")}"></i>
      <div><b>${escapeHtml(item.title)}</b><span>${escapeHtml(item.detail || "")}</span></div>
      <time>${escapeHtml(item.time || "")}</time>
      <em class="act-open">${item.jobId ? "查看" : "记录"}</em>
    </button>`).join("");
  box.onclick = (event) => {
    const row = event.target.closest(".act");
    if (!row || !box.contains(row)) return;
    onOpen?.(row.dataset.jobId || "");
  };
}

function renderDashboardPriority(rows, { onOpen } = {}) {
  const box = document.getElementById("dash-priority");
  if (!box) return;
  if (!rows.length) {
    box.innerHTML = '<p class="dash-empty">完成筛选后，推荐与待复核候选人会出现在这里。</p>';
    return;
  }
  box.innerHTML = rows.map((row) => `
    <div class="candidate" data-job-id="${escapeHtml(row.jobId || "")}" data-name="${escapeHtml(row.name)}">
      <div class="initial">${escapeHtml(row.name.slice(0, 1))}</div>
      <div><b>${escapeHtml(row.name)}</b><p>${escapeHtml(row.role || "候选人")}</p></div>
      <div><div class="score">${escapeHtml(String(row.score))}</div><span class="badge ${escapeHtml(row.kind)}">${escapeHtml(row.label)}</span></div>
    </div>`).join("");
  box.querySelectorAll(".candidate").forEach((node) => {
    node.addEventListener("click", () => onOpen?.(node.dataset.jobId, node.dataset.name));
  });
}

function renderDashboardTrend(points) {
  const svg = document.getElementById("dash-trend-svg");
  const labels = document.getElementById("dash-trend-labels");
  const hint = document.getElementById("dash-trend-hint");
  if (!svg || !labels) return;
  if (hint) hint.textContent = "最近 7 天 · 日均匹配分";
  if (!points.length || points.every((p) => p.count === 0)) {
    svg.innerHTML = "";
    labels.innerHTML = "";
    const host = document.getElementById("dash-trend");
    if (host && !host.querySelector(".dash-trend-empty")) {
      const empty = document.createElement("p");
      empty.className = "dash-trend-empty";
      empty.textContent = "近 7 天还没有匹配分数。跑完筛选后这里会显示日均趋势。";
      host.appendChild(empty);
    }
    return;
  }
  document.querySelector("#dash-trend .dash-trend-empty")?.remove();
  const width = 640;
  const height = 170;
  const padX = 16;
  const padY = 18;
  const values = points.map((p) => (p.count ? p.avg : null));
  const known = values.filter((v) => v != null);
  const min = Math.max(0, Math.min(...known) - 8);
  const max = Math.min(100, Math.max(...known) + 8);
  const span = Math.max(1, max - min);
  const coords = points.map((point, index) => {
    const x = padX + (index * (width - padX * 2)) / Math.max(1, points.length - 1);
    const y = point.count
      ? padY + ((max - point.avg) / span) * (height - padY * 2)
      : null;
    return { x, y, ...point };
  });
  const drawn = coords.filter((c) => c.y != null);
  const line = drawn.map((c, i) => `${i === 0 ? "M" : "L"}${c.x.toFixed(1)} ${c.y.toFixed(1)}`).join(" ");
  const area = drawn.length
    ? `${line} L${drawn[drawn.length - 1].x.toFixed(1)} ${height} L${drawn[0].x.toFixed(1)} ${height} Z`
    : "";
  svg.innerHTML = `
    <defs>
      <linearGradient id="dashTrendFill" x1="0" x2="0" y1="0" y2="1">
        <stop stop-color="#b7ec65" stop-opacity=".42"/>
        <stop offset="1" stop-color="#b7ec65" stop-opacity="0"/>
      </linearGradient>
    </defs>
    ${area ? `<path d="${area}" fill="url(#dashTrendFill)"></path>` : ""}
    ${line ? `<path d="${line}" fill="none" stroke="#177247" stroke-width="3"></path>` : ""}
    <g fill="#177247">${drawn.map((c) => `<circle cx="${c.x.toFixed(1)}" cy="${c.y.toFixed(1)}" r="4"><title>${c.label}: ${Math.round(c.avg)} 分 · ${c.count} 人</title></circle>`).join("")}</g>
  `;
  labels.innerHTML = points.map((p) => `<span>${escapeHtml(p.label)}</span>`).join("");
}

function buildActivityFromJobs(jobs, matchesByJob) {
  const items = [];
  for (const job of jobs.slice(0, 8)) {
    const matches = matchesByJob.get(job.id) || [];
    const recommend = matches.filter((m) => m.decision === "recommend").length;
    const review = matches.filter((m) => m.decision === "review").length;
    if (job.status === "completed") {
      items.push({
        jobId: job.id,
        title: `${job.title || "筛选任务"} 已完成`,
        detail: `共分析 ${job.processed_count || matches.length || 0} 份简历` +
          (recommend || review ? ` · 推荐 ${recommend} / 复核 ${review}` : ""),
        time: relativeTimeLabel(job.created_at),
        color: "#177247",
        at: job.created_at,
      });
    } else if (job.status === "failed") {
      items.push({
        jobId: job.id,
        title: `${job.title || "筛选任务"} 失败`,
        detail: (job.error_message || "核心产物未完整落库").slice(0, 80),
        time: relativeTimeLabel(job.created_at),
        color: "#a8463e",
        at: job.created_at,
      });
    } else if (["processing", "queued", "uploading"].includes(job.status)) {
      items.push({
        jobId: job.id,
        title: `${job.title || "筛选任务"} 进行中`,
        detail: job.candidate_count
          ? `已处理 ${job.processed_count || 0} / ${job.candidate_count} 份`
          : "排队或解析中",
        time: relativeTimeLabel(job.created_at),
        color: "#e4a821",
        at: job.created_at,
      });
    }
    if (review > 0 && job.status === "completed") {
      items.push({
        jobId: job.id,
        title: `${review} 位候选人进入人工复核`,
        detail: `${job.title || "本次筛选"} · 建议优先人工确认`,
        time: relativeTimeLabel(job.created_at),
        color: "#e4a821",
        at: job.created_at,
      });
    }
  }
  return items
    .sort((a, b) => new Date(b.at).getTime() - new Date(a.at).getTime())
    .slice(0, 6);
}

function buildTrendPoints(matches) {
  const today = startOfDay(new Date());
  const points = [];
  for (let i = 6; i >= 0; i -= 1) {
    const day = addDays(today, -i);
    const key = dayKey(day);
    const dayMatches = matches.filter((m) => dayKey(m.created_at) === key);
    const avg = dayMatches.length
      ? dayMatches.reduce((sum, m) => sum + (Number(m.score) || 0), 0) / dayMatches.length
      : 0;
    const weekday = day.toLocaleDateString("zh-CN", { weekday: "short" });
    points.push({
      key,
      label: i === 0 ? "今天" : weekday.replace("周", "周"),
      avg,
      count: dayMatches.length,
    });
  }
  return points;
}

function pctDelta(current, previous) {
  if (!previous) return current ? { text: "新", kind: "up" } : { text: "", kind: "" };
  const delta = Math.round(((current - previous) / previous) * 100);
  if (delta === 0) return { text: "持平", kind: "flat" };
  if (delta > 0) return { text: `+${delta}%`, kind: "up" };
  return { text: `${delta}%`, kind: "down" };
}

async function loadDashboard(supabase) {
  setDashGreeting("正在同步工作区数据…");
  try {
    const { data: jobs, error } = await supabase
      .from("screening_jobs")
      .select("id,title,status,created_at,candidate_count,processed_count,error_message")
      .eq("workspace_id", config.workspaceId)
      .order("created_at", { ascending: false })
      .limit(40);
    if (error) throw error;
    const list = jobs || [];
    const jobIds = list.map((j) => j.id);
    let matches = [];
    let profiles = [];
    if (jobIds.length) {
      const [matchRes, profileRes] = await Promise.all([
        supabase
          .from("match_results")
          .select("id,screening_job_id,candidate_profile_id,score,decision,created_at")
          .in("screening_job_id", jobIds)
          .order("score", { ascending: false })
          .limit(400),
        supabase
          .from("candidate_profiles")
          .select("id,display_name,profile,screening_job_id")
          .in("screening_job_id", jobIds)
          .limit(400),
      ]);
      matches = matchRes.data || [];
      profiles = profileRes.data || [];
    }

    const matchesByJob = new Map();
    matches.forEach((m) => {
      if (!matchesByJob.has(m.screening_job_id)) matchesByJob.set(m.screening_job_id, []);
      matchesByJob.get(m.screening_job_id).push(m);
    });
    const profileById = new Map(profiles.map((p) => [p.id, p]));
    const jobById = new Map(list.map((j) => [j.id, j]));

    const now = new Date();
    const weekStart = addDays(startOfDay(now), -((now.getDay() + 6) % 7)); // Monday
    const prevWeekStart = addDays(weekStart, -7);
    const inRange = (value, start, end) => {
      const t = new Date(value).getTime();
      return t >= start.getTime() && t < end.getTime();
    };
    const thisWeek = matches.filter((m) => inRange(m.created_at, weekStart, addDays(weekStart, 7)));
    const lastWeek = matches.filter((m) => inRange(m.created_at, prevWeekStart, weekStart));
    const recommendWeek = thisWeek.filter((m) => m.decision === "recommend").length;
    const recommendPrev = lastWeek.filter((m) => m.decision === "recommend").length;
    const reviewOpen = matches.filter((m) => m.decision === "review").length;
    const avgWeek = thisWeek.length
      ? Math.round(thisWeek.reduce((s, m) => s + (Number(m.score) || 0), 0) / thisWeek.length)
      : 0;
    const resumeDelta = pctDelta(thisWeek.length, lastWeek.length);
    const recommendDelta = pctDelta(recommendWeek, recommendPrev);

    setStatValue("resumes", String(thisWeek.length), resumeDelta.text, resumeDelta.kind);
    setStatValue("recommend", String(recommendWeek), recommendDelta.text, recommendDelta.kind);
    setStatValue("review", String(reviewOpen));
    setStatValue(
      "avg",
      thisWeek.length ? `${avgWeek}<span style="font-size:15px">%</span>` : "—",
    );

    const liveJob = list.find((j) => ["processing", "queued", "uploading"].includes(j.status))
      || list.find((j) => j.status === "failed")
      || list[0]
      || null;
    const openDashboardJob = (jobOrId) => {
      const id = typeof jobOrId === "string" ? jobOrId : jobOrId?.id;
      if (!id) {
        window.showView?.("tasks");
        return;
      }
      openJob(supabase, id);
    };
    renderDashboardPipeline(liveJob, { onOpen: openDashboardJob });
    renderDashboardActivity(buildActivityFromJobs(list, matchesByJob), {
      onOpen: openDashboardJob,
    });
    renderDashboardTrend(buildTrendPoints(matches));

    let priority = matches
      .filter((m) => m.decision === "recommend" || m.decision === "review")
      .slice(0, 5);
    if (!priority.length) {
      priority = matches.slice(0, 5);
    }
    priority = priority.map((m) => {
      const profile = profileById.get(m.candidate_profile_id);
      const job = jobById.get(m.screening_job_id);
      const kind = m.decision === "recommend" ? "good" : m.decision === "reject" ? "reject" : "review";
      const label = { recommend: "推荐", review: "待复核", reject: "不匹配" }[m.decision] || "已分析";
      return {
        jobId: m.screening_job_id,
        name: profile?.display_name || "未命名候选人",
        role: job?.title || `${profile?.profile?.years_experience || 0} 年经验`,
        score: Math.round(Number(m.score) || 0),
        kind,
        label,
      };
    });
    renderDashboardPriority(priority, {
      onOpen: async (jobId, name) => {
        if (!jobId) return;
        await openJob(supabase, jobId);
        if (name) window.openCandidateDetail?.(name);
      },
    });

    const activeCount = list.filter((j) => ["processing", "queued", "uploading"].includes(j.status)).length;
    const completedCount = list.filter((j) => j.status === "completed").length;
    setDashGreeting(
      activeCount
        ? `当前有 ${activeCount} 个筛选进行中 · 工作区共 ${completedCount} 次已完成任务`
        : completedCount
          ? `本周已分析 ${thisWeek.length} 份简历 · 工作区共 ${completedCount} 次已完成任务`
          : "还没有筛选任务，点右上角发起第一次智能筛选。",
    );
  } catch (error) {
    setDashGreeting(`工作台加载失败：${error.message || "请刷新重试"}`);
    renderDashboardActivity([]);
    renderDashboardPriority([]);
  }
}

function renderDemoDashboard() {
  setDashGreeting("演示模式 · 下列为示意数据，连接 Supabase 后显示真实工作区");
  setStatValue("resumes", "12", "示意", "flat");
  setStatValue("recommend", "4", "示意", "flat");
  setStatValue("review", "3");
  setStatValue("avg", '76<span style="font-size:15px">%</span>');
  renderDashboardPipeline(
    {
      title: "AI Agent / LLM 应用工程师",
      status: "processing",
      candidate_count: 12,
      processed_count: 8,
    },
    { onOpen: () => window.showView?.("upload") },
  );
  renderDashboardActivity(
    [
      {
        jobId: "",
        title: "演示：JD 解析完成",
        detail: "提取必备技能与硬门槛（示意）",
        time: "刚刚",
        color: "#177247",
      },
      {
        jobId: "",
        title: "演示：3 位候选人待复核",
        detail: "连接真实数据后会按任务更新",
        time: "—",
        color: "#e4a821",
      },
    ],
    { onOpen: () => window.showView?.("tasks") },
  );
  const today = startOfDay(new Date());
  renderDashboardTrend(
    Array.from({ length: 7 }, (_, i) => {
      const day = addDays(today, i - 6);
      return {
        key: dayKey(day),
        label: i === 6 ? "今天" : day.toLocaleDateString("zh-CN", { weekday: "short" }),
        avg: 58 + i * 3 + (i % 2) * 4,
        count: 2 + (i % 3),
      };
    }),
  );
  renderDashboardPriority(
    [
      { name: "林知远", role: "Agent 平台工程师", score: 92, kind: "good", label: "推荐", jobId: "" },
      { name: "韩沐辰", role: "高级 LLM 应用工程师", score: 89, kind: "good", label: "推荐", jobId: "" },
      { name: "陈思齐", role: "NLP 应用工程师", score: 68, kind: "review", label: "待复核", jobId: "" },
    ],
    { onOpen: () => window.showView?.("results") },
  );
}

function wireDemoViews() {
  enhanceDemoResultCards();
  renderDemoTasks();
  renderDemoSettings();
  renderDemoDashboard();
  loadScreeningConfig(null).then((screeningConfig) => applyScreeningConfigToForm(screeningConfig));
  const recent = document.getElementById("upload-recent-jobs");
  if (recent) {
    recent.innerHTML = '<p class="upload-recent-empty">演示模式无真实任务记录。连接 Supabase 后会显示历史筛选。</p>';
  }
  document.addEventListener("viewchange", (event) => {
    if (event.detail.view === "upload") {
      resetUploadWorkspace();
      if (startButton) startButton.textContent = "运行真实样例 →";
    }
    if (event.detail.view === "dashboard") renderDemoDashboard();
    if (event.detail.view === "tasks") renderDemoTasks();
    if (event.detail.view === "settings") {
      renderDemoSettings();
      loadScreeningConfig(null).then((screeningConfig) => applyScreeningConfigToForm(screeningConfig));
    }
  });
}

function showStaticDemoResults() {
  setRuntimeMode("demo", "当前模式：静态产品示例");
  if (statusNode) {
    statusNode.textContent = "正在展示预制示例结果，未调用 Worker 或模型。";
  }
  window.showView?.("results");
  enhanceDemoResultCards();
}

function wireDemoOneClick() {
  document.getElementById("view-demo-results")?.addEventListener("click", showStaticDemoResults);
  if (!startButton) return;
  startButton.textContent = "运行真实样例 →";
  startButton.addEventListener("click", () => {
    showUploadError("运行真实样例需要配置 supabase-config.js 并保持 ./dev.sh 运行。当前可先点「查看示例结果」。");
  });
}

function enhanceDemoResultCards() {
  document.querySelectorAll(".person-card").forEach((card) => {
    if (card.querySelector(".decision-facts")) return;
    const profile = window.profiles?.[card.dataset.name] || {};
    const gates = profile.gates || [];
    const skillGate = gates.find((gate) => String(gate?.[0] || "").includes("技能"));
    const hardGatePass = gates.every((gate) => String(gate?.[2] || "") !== "未通过");
    const evidenceCount = Array.isArray(profile.quotes) ? profile.quotes.length : 0;
    const facts = document.createElement("div");
    facts.className = "decision-facts";
    const entries = [
      [`硬门槛 ${hardGatePass ? "通过" : "未通过"}`, hardGatePass ? "fact-pass" : "fact-risk"],
      [skillGate ? `必备覆盖 ${skillGate[1]}` : "必备覆盖待核验", ""],
      [`证据 ${evidenceCount} 条`, ""],
    ];
    entries.forEach(([text, className]) => {
      const item = document.createElement("span");
      item.textContent = text;
      if (className) item.className = className;
      facts.append(item);
    });
    const evidence = card.querySelector(".evidence");
    evidence?.before(facts);
    const issue = profile.checkerAudit?.issues?.[0];
    if (issue && evidence) {
      const attention = document.createElement("div");
      attention.className = "card-attention";
      const label = document.createElement("strong");
      label.textContent = `AI 质检 · ${issue.severity || "提示"}：`;
      attention.append(label, document.createTextNode(issue.note || issue.recommendation || ""));
      evidence.after(attention);
    }
  });
}

function wireLiveViews(supabase, sessionReady) {
  sessionReady.then((user) => {
    renderSettings(config, user);
    loadDashboard(supabase);
  });
  loadScreeningConfig(supabase).then((screeningConfig) => applyScreeningConfigToForm(screeningConfig));
  document.addEventListener("viewchange", (event) => {
    if (event.detail.view === "dashboard") loadDashboard(supabase);
    if (event.detail.view === "upload") {
      if (window.__openingHistoryJob) {
        window.__openingHistoryJob = false;
      } else {
        const busy = document.getElementById("upload")?.classList.contains("upload-busy");
        const followingLive = Boolean(followLiveJobId) && busy;
        if (!followingLive) {
          stopJobPolling();
          resetUploadWorkspace();
          refreshRecentJobsHint(supabase);
        }
      }
    }
    if (event.detail.view === "tasks") loadTaskHistory(supabase);
    if (event.detail.view === "results" && activeJobId) loadResults(supabase, activeJobId);
    if (event.detail.view === "settings") {
      sessionReady.then((user) => renderSettings(config, user));
      loadScreeningConfig(supabase).then((screeningConfig) => applyScreeningConfigToForm(screeningConfig));
    }
  });
  document.getElementById("view-demo-results")?.addEventListener("click", showStaticDemoResults);
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
  setText(
    "settings-auth-mode",
    !user ? "未登录" : (user.is_anonymous ? "匿名会话" : "邮箱登录"),
  );
  setText("settings-user-id", user?.id || "—");
  setText("settings-session-state", user ? "已就绪" : "待登录");
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
  list.onclick = (event) => {
    const row = event.target.closest(".task-row");
    if (!row || !list.contains(row)) return;
    openJob(supabase, row.dataset.jobId);
  };
}

async function openJob(supabase, jobId) {
  if (!jobId) {
    window.showView?.("tasks");
    return;
  }
  const { data: job } = await supabase
    .from("screening_jobs")
    .select("id,title,status,candidate_count,processed_count,error_message")
    .eq("id", jobId)
    .single();
  if (!job) return;
  activeJobId = job.id;
  const inFlight = ["queued", "processing", "uploading"].includes(job.status);
  if (job.status === "completed") {
    followLiveJobId = null;
    await loadResults(supabase, job.id);
    if (latestResultsSnapshot?.candidates?.length) {
      window.showView?.("results");
      return;
    }
  }
  followLiveJobId = inFlight ? job.id : null;
  const { data: documents } = await supabase
    .from("documents")
    .select("original_filename,document_type,size_bytes")
    .eq("screening_job_id", job.id)
    .order("document_type");
  showSubmittedPanel(job, documents || []);
  await updateParseProgress(supabase, job);
  await refreshAgentChain(supabase, job.id);
  if (inFlight) startJobPolling(supabase, job.id);
  window.__openingHistoryJob = true;
  window.showView?.("upload");
}

function taskRowMarkup(job) {
  const info = jobStatusLabels[job.status] || { label: job.status };
  const progress = job.candidate_count
    ? `已处理 ${job.processed_count || 0} / ${job.candidate_count} 份`
    : "等待开始";
  const time = formatTaskTime(job.created_at);
  const jobId = job.id ? ` data-job-id="${job.id}"` : "";
  return `<div class="task-row"${jobId}><div><b>${escapeHtml(job.title || "未命名任务")}</b><span>${progress}</span></div><time class="task-meta">${escapeHtml(time)}</time><span class="task-status" data-state="${escapeHtml(job.status)}">${escapeHtml(info.label)}</span><em class="act-open">查看</em></div>`;
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
    const response = await fetch(`samples/manifest.json?v=${Date.now()}`);
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

function sampleChipMeta(type, sample) {
  const salary = normalizeSalaryBand(sample.salary);
  if (type === "jd") {
    // Unified placeholder: 25-45K · 上海
    const city = String(sample.city || "").trim();
    return city ? `${salary} · ${city}` : salary;
  }
  // Resume: keep match hint, but always lead with the same salary band format.
  const tag = String(sample.tag || "样例").trim();
  return `${salary} · ${tag}`;
}

function normalizeSalaryBand(value) {
  const raw = String(value || "").trim().toUpperCase().replace(/\s+/g, "");
  const match = raw.match(/^(\d{1,3})\s*[-~—–]\s*(\d{1,3})\s*K?$/i);
  if (match) return `${Number(match[1])}-${Number(match[2])}K`;
  if (/^\d{1,3}-\d{1,3}K$/.test(raw)) return raw;
  return "25-45K";
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
      <span class="sample-chip-title">${escapeHtml(sample.title)}</span>
      <small class="sample-chip-meta">${escapeHtml(sampleChipMeta(type, sample))}</small>
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

function wireExportReport() {
  const button = document.getElementById("export-report-btn");
  if (!button || button.dataset.wired === "1") return;
  button.dataset.wired = "1";
  button.addEventListener("click", () => {
    try {
      const snapshot = getExportableResultsSnapshot();
      if (!snapshot?.candidates?.length) {
        notifyUi("暂无可导出的候选人结果");
        return;
      }
      const stamp = formatExportStamp(snapshot.createdAt);
      const baseName = sanitizeFilename(`筛选报告_${snapshot.title}_${stamp}`);
      downloadTextFile(`${baseName}.html`, buildScreeningReportHtml(snapshot), "text/html;charset=utf-8");
      // Second download may be gated by the browser; HTML is the primary deliverable.
      setTimeout(() => {
        downloadTextFile(`${baseName}.md`, buildScreeningReportMarkdown(snapshot), "text/markdown;charset=utf-8");
      }, 350);
      notifyUi(`已导出报告（HTML + Markdown）· ${snapshot.candidates.length} 人`);
    } catch (error) {
      notifyUi(`导出失败：${error.message || "请重试"}`);
    }
  });
}

function getExportableResultsSnapshot() {
  if (latestResultsSnapshot?.candidates?.length) return latestResultsSnapshot;
  return buildSnapshotFromDomProfiles();
}

function buildSnapshotFromDomProfiles() {
  const cards = [...document.querySelectorAll("#result-list .person-card")];
  if (!cards.length) return null;
  const profiles = window.profiles || {};
  const decisionFromKind = {
    good: "recommend",
    review: "review",
    reject: "reject",
  };
  const candidates = cards.map((card) => {
    const name = card.dataset.name || "未命名候选人";
    const profile = profiles[name] || {};
    const kind = card.dataset.kind || "review";
    const decision = decisionFromKind[kind] || "review";
    return {
      name,
      years: Number(String(profile.role || "").match(/(\d+)\s*年/)?.[1] || 0),
      skills: [...card.querySelectorAll(".tag")].map((node) => node.textContent.trim()).filter(Boolean),
      decision,
      decisionLabel: profile.decision || ({ recommend: "建议优先面试", review: "建议人工复核", reject: "当前不建议推进" }[decision]),
      score: Number(profile.score) || Number(card.querySelector(".ring span")?.textContent) || 0,
      hardGatePass: decision !== "reject",
      summary: profile.summary || card.querySelector(".evidence")?.textContent?.replace(/^匹配结果：/, "").trim() || "",
      evidence: (profile.quotes || []).map((quote) => String(quote).replace(/^“|”$/g, "")),
      risks: [],
      scores: Array.isArray(profile.scores) ? profile.scores : [],
      gates: Array.isArray(profile.gates) ? profile.gates : [],
      questions: Array.isArray(profile.questions) ? profile.questions : [],
      followups: Array.isArray(profile.followups) ? profile.followups : [],
    };
  });
  return {
    jobId: activeJobId || "demo",
    title: document.querySelector("#results .intro p")?.textContent || "演示筛选任务",
    createdAt: new Date().toISOString(),
    candidates,
  };
}

function buildScreeningReportMarkdown(snapshot) {
  const recommend = snapshot.candidates.filter((item) => item.decision === "recommend").length;
  const review = snapshot.candidates.filter((item) => item.decision === "review").length;
  const reject = snapshot.candidates.filter((item) => item.decision === "reject").length;
  const lines = [
    `# 筛选报告 · ${snapshot.title}`,
    "",
    `- 导出时间：${new Date().toLocaleString("zh-CN")}`,
    `- 任务时间：${formatTaskTime(snapshot.createdAt)}`,
    `- 候选人：${snapshot.candidates.length}（推荐 ${recommend} / 复核 ${review} / 不匹配 ${reject}）`,
    "",
    "## 候选人总览",
    "",
    "| 姓名 | 分数 | 结论 | 年限 | 技能 |",
    "| --- | ---: | --- | ---: | --- |",
  ];
  snapshot.candidates.forEach((candidate) => {
    lines.push(
      `| ${mdCell(candidate.name)} | ${candidate.score} | ${mdCell(candidate.decisionLabel)} | ${candidate.years} | ${mdCell((candidate.skills || []).slice(0, 6).join("、") || "—")} |`,
    );
  });
  lines.push("", "## 详细分析", "");
  snapshot.candidates.forEach((candidate, index) => {
    lines.push(`### ${index + 1}. ${candidate.name}（${candidate.score} 分 · ${candidate.decisionLabel}）`, "");
    lines.push(`- 经验：${candidate.years} 年`);
    lines.push(`- 技能：${(candidate.skills || []).join("、") || "—"}`);
    lines.push(`- 摘要：${candidate.summary || "—"}`);
    if (candidate.checkerDegraded) {
      lines.push(`- 质检状态：未通过/不可用（${candidate.checkerStatus || "unknown"}）；结论需人工复核。`);
    }
    if (candidate.gates?.length) {
      lines.push("- 硬门槛：");
      candidate.gates.forEach((gate) => {
        const row = Array.isArray(gate) ? gate : [gate];
        lines.push(`  - ${row.filter(Boolean).join(" · ")}`);
      });
    }
    if (candidate.scores?.length) {
      lines.push("- 分数拆解：");
      candidate.scores.forEach(([label, score]) => lines.push(`  - ${label}：${score}`));
    }
    if (candidate.evidence?.length) {
      lines.push("- 关键证据：");
      candidate.evidence.forEach((text) => lines.push(`  - ${text}`));
    }
    if (candidate.risks?.length) {
      lines.push("- 风险点：");
      candidate.risks.forEach((text) => lines.push(`  - ${text}`));
    }
    const questions = candidate.questions || [];
    if (questions.length) {
      lines.push("- 面试题：");
      questions.forEach((item, qIndex) => {
        const text = typeof item === "string" ? item : item?.question || "";
        const kp = typeof item === "object" && item?.knowledge_point ? `（${item.knowledge_point}）` : "";
        if (text) lines.push(`  ${qIndex + 1}. ${text}${kp}`);
      });
    }
    const followups = candidate.followups || [];
    if (followups.length) {
      lines.push("- 建议追问：");
      followups.forEach((item) => {
        const text = typeof item === "string" ? item : item?.question || "";
        if (text) lines.push(`  - ${text}`);
      });
    }
    lines.push("");
  });
  lines.push("---", "", "_由简历中台自动生成_");
  return lines.join("\n");
}

function buildScreeningReportHtml(snapshot) {
  const recommend = snapshot.candidates.filter((item) => item.decision === "recommend").length;
  const review = snapshot.candidates.filter((item) => item.decision === "review").length;
  const reject = snapshot.candidates.filter((item) => item.decision === "reject").length;
  const rows = snapshot.candidates.map((candidate, index) => {
    const decisionClass = candidate.decision === "recommend" ? "good" : candidate.decision === "reject" ? "reject" : "review";
    const scores = (candidate.scores || [])
      .map(([label, score]) => `<li><span>${escapeHtml(label)}</span><b>${escapeHtml(String(score))}</b></li>`)
      .join("");
    const gates = (candidate.gates || [])
      .map((gate) => {
        const row = Array.isArray(gate) ? gate : [gate];
        return `<li>${escapeHtml(row.filter(Boolean).join(" · "))}</li>`;
      })
      .join("");
    const evidence = (candidate.evidence || []).map((text) => `<li>${escapeHtml(text)}</li>`).join("");
    const risks = (candidate.risks || []).map((text) => `<li>${escapeHtml(text)}</li>`).join("");
    const questions = (candidate.questions || []).map((item, qIndex) => {
      const text = typeof item === "string" ? item : item?.question || "";
      const kp = typeof item === "object" && item?.knowledge_point ? ` · ${item.knowledge_point}` : "";
      return text ? `<li><small>Q${String(qIndex + 1).padStart(2, "0")}${escapeHtml(kp)}</small>${escapeHtml(text)}</li>` : "";
    }).join("");
    const followups = (candidate.followups || []).map((item) => {
      const text = typeof item === "string" ? item : item?.question || "";
      return text ? `<li>${escapeHtml(text)}</li>` : "";
    }).join("");
    const skills = (candidate.skills || []).map((skill) => `<span class="tag">${escapeHtml(skill)}</span>`).join("");
    return `<section class="card">
      <header>
        <div>
          <h2>${index + 1}. ${escapeHtml(candidate.name)}</h2>
          <p>${escapeHtml(String(candidate.years))} 年经验 · ${skills || "暂无技能标签"}</p>
        </div>
        <div class="score"><b>${escapeHtml(String(candidate.score))}</b><span class="badge ${decisionClass}">${escapeHtml(candidate.decisionLabel)}</span></div>
      </header>
      <p class="summary">${escapeHtml(candidate.summary || "暂无摘要")}</p>
      ${candidate.checkerDegraded ? `<p class="summary">质检状态：未通过/不可用（${escapeHtml(candidate.checkerStatus || "unknown")}），结果需人工复核。</p>` : ""}
      ${gates ? `<h3>硬门槛</h3><ul>${gates}</ul>` : ""}
      ${scores ? `<h3>分数拆解</h3><ul class="scores">${scores}</ul>` : ""}
      ${evidence ? `<h3>关键证据</h3><ul>${evidence}</ul>` : ""}
      ${risks ? `<h3>风险点</h3><ul>${risks}</ul>` : ""}
      ${questions ? `<h3>面试题</h3><ol class="questions">${questions}</ol>` : ""}
      ${followups ? `<h3>建议追问</h3><ul>${followups}</ul>` : ""}
    </section>`;
  }).join("\n");

  return `<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>筛选报告 · ${escapeHtml(snapshot.title)}</title>
  <style>
    :root { color-scheme: light; --ink:#111; --muted:#6e6e73; --line:#e8e8ed; --good:#1f7a4d; --review:#b15c00; --reject:#a8463e; }
    * { box-sizing: border-box; }
    body { margin: 0; font: 15px/1.6 -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Noto Sans SC", sans-serif; color: var(--ink); background: #f5f5f7; }
    main { max-width: 880px; margin: 0 auto; padding: 32px 20px 64px; }
    .hero { background: #111; color: #fff; border-radius: 18px; padding: 28px 26px; margin-bottom: 22px; }
    .hero h1 { margin: 0 0 8px; font-size: 28px; letter-spacing: -0.04em; }
    .hero p { margin: 0; color: #c7c7cc; }
    .stats { display: grid; grid-template-columns: repeat(4, 1fr); gap: 10px; margin-top: 18px; }
    .stats div { background: rgba(255,255,255,.08); border-radius: 12px; padding: 12px 14px; }
    .stats b { display: block; font-size: 22px; }
    .stats span { color: #a1a1a6; font-size: 12px; }
    .card { background: #fff; border: 1px solid var(--line); border-radius: 16px; padding: 20px 22px; margin-bottom: 14px; }
    .card header { display: flex; justify-content: space-between; gap: 16px; align-items: flex-start; }
    .card h2 { margin: 0 0 4px; font-size: 20px; }
    .card header p { margin: 0; color: var(--muted); font-size: 13px; }
    .score { text-align: right; }
    .score b { display: block; font-size: 28px; line-height: 1; margin-bottom: 8px; }
    .summary { margin: 14px 0 0; color: #333; }
    h3 { margin: 18px 0 8px; font-size: 13px; letter-spacing: .04em; text-transform: uppercase; color: var(--muted); }
    ul, ol { margin: 0; padding-left: 18px; }
    .scores { list-style: none; padding: 0; display: grid; gap: 6px; }
    .scores li { display: flex; justify-content: space-between; gap: 12px; border-bottom: 1px dashed var(--line); padding: 4px 0; }
    .questions li { margin-bottom: 8px; }
    .questions small { display: block; color: var(--muted); margin-bottom: 2px; }
    .tag { display: inline-block; margin: 2px 4px 0 0; padding: 2px 8px; border-radius: 999px; background: #f5f5f7; font-size: 12px; }
    .badge { display: inline-block; padding: 4px 10px; border-radius: 999px; font-size: 12px; font-weight: 600; }
    .badge.good { background: #e8f7ee; color: var(--good); }
    .badge.review { background: #fff4e8; color: var(--review); }
    .badge.reject { background: #ffedeb; color: var(--reject); }
    footer { margin-top: 28px; color: var(--muted); font-size: 12px; text-align: center; }
    @media print {
      body { background: #fff; }
      main { padding: 0; }
      .card { break-inside: avoid; }
    }
    @media (max-width: 720px) { .stats { grid-template-columns: repeat(2, 1fr); } }
  </style>
</head>
<body>
  <main>
    <section class="hero">
      <h1>筛选报告</h1>
      <p>${escapeHtml(snapshot.title)}</p>
      <p>导出时间 ${escapeHtml(new Date().toLocaleString("zh-CN"))} · 任务时间 ${escapeHtml(formatTaskTime(snapshot.createdAt))}</p>
      <div class="stats">
        <div><b>${snapshot.candidates.length}</b><span>已分析</span></div>
        <div><b>${recommend}</b><span>推荐</span></div>
        <div><b>${review}</b><span>待复核</span></div>
        <div><b>${reject}</b><span>不匹配</span></div>
      </div>
    </section>
    ${rows}
    <footer>由简历中台自动生成 · 可直接打印或另存为 PDF</footer>
  </main>
</body>
</html>`;
}

function downloadTextFile(filename, content, mimeType) {
  const blob = new Blob([content], { type: mimeType });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1500);
}

function sanitizeFilename(value) {
  return String(value || "report")
    .replace(/[\\/:*?"<>|]+/g, "_")
    .replace(/\s+/g, "_")
    .slice(0, 80);
}

function formatExportStamp(value) {
  const date = value ? new Date(value) : new Date();
  if (Number.isNaN(date.getTime())) return "export";
  const pad = (n) => String(n).padStart(2, "0");
  return `${date.getFullYear()}${pad(date.getMonth() + 1)}${pad(date.getDate())}_${pad(date.getHours())}${pad(date.getMinutes())}`;
}

function mdCell(value) {
  return String(value ?? "—").replace(/\|/g, "\\|").replace(/\n+/g, " ");
}

function notifyUi(message) {
  const toast = document.getElementById("toast");
  if (!toast) {
    console.info(message);
    return;
  }
  toast.textContent = message;
  toast.classList.add("show");
  clearTimeout(notifyUi._timer);
  notifyUi._timer = setTimeout(() => toast.classList.remove("show"), 2400);
}

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, (character) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;",
  }[character]));
}
