import { createClient } from "https://esm.sh/@supabase/supabase-js@2";

const config = window.SUPABASE_CONFIG;
const statusNode = document.querySelector(".upload-side p");
const startButton = document.getElementById("start-screening");
const jdInput = document.getElementById("jd-input");
const resumeInput = document.getElementById("resume-input");
const selected = { jd: null, resumes: [] };

document.getElementById("result-list").addEventListener("click", (event) => {
  const button = event.target.closest(".detail");
  const card = button?.closest(".person-card");
  if (card) window.openDrawer?.(card.dataset.name);
});

if (!config?.url || !config?.anonKey || !config?.workspaceId) {
  if (statusNode) {
    statusNode.textContent = "当前为演示数据。复制 supabase-config.example.js 为 supabase-config.js 并填写项目配置后，即可启用真实上传与任务状态。";
  }
} else {
  const supabase = createClient(config.url, config.anonKey);
  wireAuth(supabase);
  wireUpload(supabase);
  watchLatestJob(supabase);
}

function wireAuth(supabase) {
  const action = document.getElementById("login-action");
  const name = document.getElementById("account-name");
  const role = document.getElementById("account-role");

  supabase.auth.getUser().then(({ data: { user } }) => {
    if (!user) {
      name.textContent = "登录";
      role.textContent = "使用邮箱链接登录";
      return;
    }
    name.textContent = user.email?.split("@")[0] || "已登录用户";
    role.textContent = "招聘负责人";
  });

  action.addEventListener("click", async () => {
    const { data: { user } } = await supabase.auth.getUser();
    if (user) return;
    const email = window.prompt("请输入登录邮箱，我们会发送一个安全登录链接：");
    if (!email) return;
    const { error } = await supabase.auth.signInWithOtp({
      email,
      options: { emailRedirectTo: window.location.origin },
    });
    if (error) return showUploadError(`登录链接发送失败：${error.message}`);
    showUploadError("登录链接已发送，请在邮箱中打开后返回本页面。");
  });
}

function wireUpload(supabase) {
  window.demoAdd = (type) => (type === "jd" ? jdInput : resumeInput).click();

  jdInput.addEventListener("change", () => {
    const [file] = jdInput.files;
    if (!file) return;
    selected.jd = file;
    renderFiles();
  });

  resumeInput.addEventListener("change", () => {
    selected.resumes.push(...Array.from(resumeInput.files || []));
    resumeInput.value = "";
    renderFiles();
  });

  startButton.addEventListener("click", async () => {
    try {
      startButton.disabled = true;
      startButton.textContent = "正在上传…";
      await startScreening(supabase);
      startButton.textContent = "已提交，等待解析";
    } catch (error) {
      startButton.disabled = false;
      startButton.textContent = "开始解析与筛选 →";
      showUploadError(error.message);
    }
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
}

function validateFile(file) {
  const allowed = [
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  ];
  if (!allowed.includes(file.type)) throw new Error(`${file.name} 不是 PDF 或 DOCX 文件。`);
  if (file.size > 10 * 1024 * 1024) throw new Error(`${file.name} 超过 10MB 限制。`);
}

function renderFiles() {
  const jdBox = document.getElementById("jd-file");
  const resumeBox = document.getElementById("resume-files");
  if (selected.jd) jdBox.innerHTML = fileMarkup(selected.jd);
  resumeBox.innerHTML = selected.resumes.map(fileMarkup).join("");
}

function fileMarkup(file) {
  return `<div class="file"><div class="filetype">${file.name.endsWith(".docx") ? "DOCX" : "PDF"}</div><div class="file-name">${escapeHtml(file.name)}<span>${Math.ceil(file.size / 1024)} KB · 等待上传</span></div><div class="check">✓</div></div>`;
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
    updateJobProgress(job);
    if (job.status === "completed") await loadResults(supabase, job.id);
  };
  refresh();
  supabase
    .channel("screening-jobs")
    .on("postgres_changes", { event: "*", schema: "public", table: "screening_jobs", filter: `workspace_id=eq.${config.workspaceId}` }, refresh)
    .subscribe();
}

async function loadResults(supabase, jobId) {
  const [{ data: matches }, { data: profiles }] = await Promise.all([
    supabase.from("match_results").select("*").eq("screening_job_id", jobId).order("score", { ascending: false }),
    supabase.from("candidate_profiles").select("id,display_name,profile").eq("screening_job_id", jobId),
  ]);
  const byProfile = new Map((profiles || []).map((profile) => [profile.id, profile]));
  const list = document.getElementById("result-list");
  if (!matches?.length || !list) return;
  window.profiles ||= {};
  matches.forEach((match) => {
    const profile = byProfile.get(match.candidate_profile_id);
    const name = profile?.display_name || "未命名候选人";
    window.profiles[name] = {
      role: `${profile?.profile?.years_experience || 0} 年经验`,
      score: Math.round(match.score),
      decision: { recommend: "建议优先面试", review: "建议人工复核", reject: "当前不建议推进" }[match.decision],
      summary: match.risks?.[0] || match.evidence?.[0]?.text || "已完成自动分析。",
      gates: [["硬门槛", match.hard_gate_pass ? "已通过" : "未通过", match.hard_gate_pass ? "通过" : "未通过"]],
      scores: Object.entries(match.score_breakdown || {}).map(([label, score]) => [label, score]),
      quotes: (match.evidence || []).map((item) => `“${item.text}”`),
      question: match.interview_question || "请结合一段真实经历说明你在这个项目中的具体贡献。",
    };
  });
  list.innerHTML = matches.map((match) => {
    const profile = byProfile.get(match.candidate_profile_id);
    const name = profile?.display_name || "未命名候选人";
    const skills = (profile?.profile?.skills || []).slice(0, 3);
    const label = { recommend: "推荐面试", review: "建议复核", reject: "不匹配" }[match.decision];
    return `<article class="person-card" data-kind="${match.decision === "recommend" ? "good" : match.decision}" data-name="${escapeHtml(name)}">
      <div class="person-head"><div class="initial">${escapeHtml(name.slice(0, 1))}</div><div><h3>${escapeHtml(name)}</h3><p>${profile?.profile?.years_experience || 0} 年经验</p></div><div class="ring" style="--score:${match.score}"><span>${Math.round(match.score)}</span></div></div>
      <div class="tags">${skills.map((skill) => `<span class="tag">${escapeHtml(skill)}</span>`).join("")}</div>
      <div class="evidence"><strong>匹配结果：</strong>${escapeHtml(match.evidence?.[0]?.text || "等待分析证据")}</div>
      <div class="person-footer"><span class="badge ${match.decision === "recommend" ? "good" : match.decision}">${label}</span><button class="detail">查看分析 →</button></div>
    </article>`;
  }).join("");
}

function updateJobProgress(job) {
  const progress = document.querySelector(".pipeline + div span");
  if (progress) progress.textContent = `已处理 ${job.processed_count} / ${job.candidate_count} 份`;
  if (job.status === "failed" && statusNode) statusNode.textContent = job.error_message || "任务处理失败，请检查文件并重试。";
}

function showUploadError(message) {
  if (statusNode) statusNode.textContent = `上传失败：${message}`;
}

function safeFilename(name) {
  return name.replace(/[^a-zA-Z0-9.\-_()]/g, "_");
}

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, (character) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;",
  }[character]));
}
