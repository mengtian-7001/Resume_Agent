import { expect, test } from "@playwright/test";

test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => {
    window.localStorage.setItem("resume_agent_onboarding_v1", "completed");
  });
  // Keep smoke tests offline and deterministic: exercise the built-in demo path.
  await page.route("**/supabase-config.js*", (route) =>
    route.fulfill({ contentType: "application/javascript", body: "window.SUPABASE_CONFIG = {};" }),
  );
  await page.route("**/supabase-config.worker.js*", (route) =>
    route.fulfill({ contentType: "application/javascript", body: "" }),
  );
});

test("demo runs one-click flow and opens a standalone candidate analysis page", async ({ page }) => {
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto("/index.html");
  await expect(page.locator("#runtime-mode")).toContainText("静态产品示例");
  await page.waitForTimeout(100);
  expect(errors).toEqual([]);
  await page.locator('[data-view="upload"]').click();
  await expect(page.getByRole("button", { name: "查看示例结果" })).toBeVisible();
  await expect(page.locator("#start-screening")).toHaveText("运行真实样例 →");
  await page.getByRole("button", { name: "查看示例结果" }).click();
  await expect(page.locator("#results")).toHaveClass(/active/);
  await expect(page.locator(".person-card").first()).toBeVisible();
  await page.locator(".person-card").first().click();
  await expect(page.locator("#candidate-detail")).toHaveClass(/active/);
  await expect(page.locator("#candidate-name")).toHaveText("林知远");
  await expect(page.locator("#candidate-checker")).toContainText("结论：");
  await expect(page.locator("#candidate-questions .question-item")).toHaveCount(10);
  await page.getByRole("button", { name: "打开面试说明" }).click();
  await expect(page.locator("#candidate-info-dialog")).toBeVisible();
  await expect(page.locator("#candidate-info-title")).toContainText("面试说明");
  await expect(page.locator("#candidate-info-body")).toContainText("推荐面试流程");
  await page.getByRole("button", { name: "关闭说明" }).click();
  await page.getByRole("button", { name: "打开 JD 介绍" }).click();
  await expect(page.locator("#candidate-info-title")).toHaveText("JD 介绍");
  await expect(page.locator("#candidate-info-body")).toContainText("必备技能");
  await page.getByRole("button", { name: "关闭说明" }).click();
  const followupCount = await page.locator("#candidate-followups").evaluate((node) =>
    node.textContent.split("\n").filter(Boolean).length,
  );
  expect(followupCount).toBeGreaterThanOrEqual(3);
  expect(followupCount).toBeLessThanOrEqual(5);
  await page.getByRole("button", { name: "返回候选人列表" }).click();
  await expect(page.locator("#results")).toHaveClass(/active/);
  expect(errors).toEqual([]);
});

test("demo query explicitly keeps the page offline even when a live config exists", async ({ page }) => {
  await page.unroute("**/supabase-config.js*");
  await page.route("**/supabase-config.js*", (route) =>
    route.fulfill({
      contentType: "application/javascript",
      body: `window.SUPABASE_CONFIG = {
        url: "https://example.supabase.co",
        anonKey: "test-publishable-key",
        workspaceId: "test-workspace",
        allowAnonymousBootstrap: true
      };`,
    }),
  );
  await page.goto("/index.html?demo=1");
  await expect(page.locator("#runtime-mode")).toContainText("静态产品示例");
  await expect(page.locator("#start-screening")).toHaveText("运行真实样例 →");
});

test("390px viewport has no document horizontal overflow", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/index.html");
  const widths = await page.evaluate(() => ({
    client: document.documentElement.clientWidth,
    scroll: document.documentElement.scrollWidth,
  }));
  expect(widths.scroll).toBeLessThanOrEqual(widths.client);
  await expect(page.getByRole("button", { name: "发起新筛选" })).toBeVisible();
  await page.locator('[data-view="upload"]').click();
  await expect(page.locator("#start-screening")).toBeVisible();
  const uploadWidths = await page.evaluate(() => {
    const viewport = document.documentElement.clientWidth;
    const drop = document.querySelector("#upload .drop")?.getBoundingClientRect();
    return {
      viewport,
      document: document.documentElement.scrollWidth,
      dropRight: drop?.right || 0,
      dropWidth: drop?.width || 0,
    };
  });
  expect(uploadWidths.document).toBeLessThanOrEqual(uploadWidths.viewport);
  expect(uploadWidths.dropRight).toBeLessThanOrEqual(uploadWidths.viewport);
  expect(uploadWidths.dropWidth).toBeLessThan(uploadWidths.viewport);
});

test("selected sample JD and resume can be opened without losing the selection", async ({ page }) => {
  await page.goto("/index.html");
  await page.locator('[data-view="upload"]').click();

  const jdChip = page.locator("#jd-sample-chips .sample-chip").first();
  const jdSampleId = await jdChip.getAttribute("data-sample-id");
  await jdChip.click();
  await expect(page.locator("#jd-file .file-openable")).toContainText("查看内容");
  await page.locator(`#jd-sample-chips [data-sample-id="${jdSampleId}"]`).click();
  await expect(page.locator("#sample-preview-dialog")).toBeVisible();
  await expect(page.locator("#sample-preview-kicker")).toHaveText("JD 文档");
  await expect(page.locator("#sample-preview-body")).toContainText("岗位名称");
  await page.getByRole("button", { name: "关闭文件预览" }).click();
  await expect(page.locator("#jd-file .file-openable")).toBeVisible();

  await page.locator("#resume-sample-chips .sample-chip").first().click();
  await page.locator("#resume-files .file-openable").first().click();
  await expect(page.locator("#sample-preview-kicker")).toHaveText("候选人简历");
  await expect(page.locator("#sample-preview-body")).toContainText("姓名：");
  await page.getByRole("button", { name: "关闭文件预览" }).click();
  await page.locator("#resume-files .file-remove").first().click();
  await expect(page.locator("#resume-files .file-empty")).toBeVisible();
});

test("JD can be entered as text, selected, reopened, and edited", async ({ page }) => {
  await page.goto("/index.html");
  await page.locator('[data-view="upload"]').click();

  await page.getByRole("button", { name: "直接输入" }).click();
  await expect(page.locator("#jd-text-panel")).toBeVisible();
  await page.locator("#jd-text-title").fill("AI Agent 工程师");
  await page.locator("#jd-textarea").fill(
    "岗位职责：负责企业级 Agent 的设计、开发、评测与上线。\n"
      + "任职要求：3 年以上经验，本科及以上学历，熟悉 Python、LangChain、Function Calling 和 Multi-Agent。",
  );
  await expect(page.locator("#jd-text-count")).not.toHaveText("0 / 20,000");
  await page.getByRole("button", { name: "使用这段文字" }).click();

  const selectedJd = page.locator("#jd-file .file-openable");
  await expect(selectedJd).toContainText("AI Agent 工程师.docx");
  await expect(selectedJd).toContainText("DOCX");
  await expect(selectedJd).toContainText("直接输入");
  await selectedJd.click();
  await expect(page.locator("#sample-preview-kicker")).toHaveText("JD 文档");
  await expect(page.locator("#sample-preview-body")).toContainText("岗位职责");
  await expect(page.locator("#sample-preview-body")).toContainText("Function Calling");
  await page.getByRole("button", { name: "关闭文件预览" }).click();

  await page.getByRole("button", { name: "编辑文字" }).click();
  await expect(page.locator("#jd-textarea")).toHaveValue(/企业级 Agent/);
  await page.getByRole("button", { name: "取消" }).click();
  await expect(page.locator("#jd-text-panel")).toBeHidden();
});

test("live upload keeps selected files and clearly asks unauthenticated users to sign in", async ({ page }) => {
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.unroute("**/supabase-config.js*");
  await page.route("**/supabase-config.js*", (route) =>
    route.fulfill({
      contentType: "application/javascript",
      body: `window.SUPABASE_CONFIG = {
        url: "https://example.supabase.co",
        anonKey: "test-publishable-key",
        workspaceId: "test-workspace",
        allowAnonymousBootstrap: false
      };`,
    }),
  );
  await page.route("https://esm.sh/**", (route) =>
    route.fulfill({
      contentType: "application/javascript",
      headers: { "Access-Control-Allow-Origin": "*" },
      body: `
        const emptyResult = { data: [], error: null };
        function query() {
          let proxy;
          proxy = new Proxy({}, {
            get(_target, key) {
              if (key === "then") return (resolve, reject) => Promise.resolve(emptyResult).then(resolve, reject);
              return () => proxy;
            }
          });
          return proxy;
        }
        export function createClient() {
          return {
            auth: {
              getUser: async () => ({ data: { user: null } }),
              getSession: async () => ({ data: { session: null }, error: null })
            },
            from: () => query(),
            rpc: async () => emptyResult,
            storage: { from: () => ({}) }
          };
        }
      `,
    }),
  );

  await page.goto("/index.html");
  await page.locator('[data-view="upload"]').click();
  await page.locator("#jd-sample-chips .sample-chip").first().click();
  await page.locator("#resume-sample-chips .sample-chip").first().click();
  await page.getByRole("button", { name: "一键解析 →" }).click();

  await expect(page.locator("#jd-file .file")).toBeVisible();
  await expect(page.locator("#resume-files .file")).toBeVisible();
  await expect(page.locator(".upload-side > p")).toContainText("登录工作区账号");
  await expect(page.locator("#toast")).toContainText("登录工作区账号");
  expect(errors).toEqual([]);
});

test("public deployment automatically opens an isolated anonymous workspace", async ({ page }) => {
  const workspaceId = "8d4874a6-33d0-4ae8-bdb8-48f27daf6715";
  await page.unroute("**/supabase-config.js*");
  await page.route("**/supabase-config.js*", (route) =>
    route.fulfill({
      contentType: "application/javascript",
      body: `window.SUPABASE_CONFIG = {
        url: "https://example.supabase.co",
        anonKey: "test-publishable-key",
        workspaceId: "shared-placeholder",
        allowAnonymousBootstrap: true
      };`,
    }),
  );
  await page.route("https://esm.sh/**", (route) =>
    route.fulfill({
      contentType: "application/javascript",
      headers: { "Access-Control-Allow-Origin": "*" },
      body: `
        let user = null;
        let session = null;
        const emptyResult = { data: [], error: null };
        function query() {
          let proxy;
          proxy = new Proxy({}, {
            get(_target, key) {
              if (key === "then") return (resolve, reject) => Promise.resolve(emptyResult).then(resolve, reject);
              return () => proxy;
            }
          });
          return proxy;
        }
        export function createClient() {
          return {
            auth: {
              getUser: async () => ({ data: { user } }),
              getSession: async () => ({ data: { session }, error: null }),
              signInAnonymously: async () => {
                user = { id: "anonymous-user", is_anonymous: true };
                session = { access_token: "anonymous-token" };
                return { data: { user, session }, error: null };
              }
            },
            from: () => query(),
            rpc: async () => emptyResult,
            storage: { from: () => ({}) }
          };
        }
      `,
    }),
  );
  await page.route("**/session/bootstrap", (route) =>
    route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({ workspace_id: workspaceId, mode: "anonymous" }),
    }),
  );
  await page.route("**/api/health", (route) =>
    route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({ status: "ok", construction: "OpenAIConstructionAgent", checker: "OpenAICheckerAgent" }),
    }),
  );

  await page.goto("/index.html");
  await expect(page.getByRole("button", { name: "匿名会话已就绪" })).toBeVisible();
  await expect(page.locator("#account-name")).toHaveText("匿名会话");
  await expect(page.locator("#account-role")).toHaveText("匿名体验工作区");
  await expect.poll(() => page.evaluate(() => window.SUPABASE_CONFIG.workspaceId)).toBe(workspaceId);
});

test("interview workspace records and restores candidate answers and scores", async ({ page }) => {
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto("/index.html");
  await page.locator('[data-view="results"]').click();
  await page.locator(".person-card").first().click();
  await expect(page.locator("#candidate-detail")).toHaveClass(/active/);
  await page.getByRole("button", { name: "进入面试工作台" }).click();
  await expect(page.locator("#interview")).toHaveClass(/active/);
  await expect(page.locator("#interview-candidate-meta")).toContainText("林知远");
  await page.locator("#interview-answer-0").fill("候选人说明了幂等键与人工接管边界。");
  await page.locator("#interview-score-0").fill("8");
  await expect(page.locator("#interview-total")).toHaveText("8 / 100");

  await page.reload();
  await page.locator('[data-view="interview"]').click();
  await page.locator("#interview-candidate-select").selectOption("林知远");
  await expect(page.locator("#interview-answer-0")).toHaveValue("候选人说明了幂等键与人工接管边界。");
  await expect(page.locator("#interview-score-0")).toHaveValue("8");
  await expect(page.locator("#interview-total")).toHaveText("8 / 100");
  expect(errors).toEqual([]);
});

test("agent chain groups parallel candidate steps under person headings", async ({ page }) => {
  const steps = [
    { id: "parse_jd.extract", label: "解析 JD 文本与硬门槛", status: "completed" },
    { id: "match.start", label: "开始候选人匹配", status: "completed" },
    { id: "construction.analyze.c1", label: "Construction 匹配与出题 · 韩沐辰", status: "running", candidate_id: "c1", candidate_name: "韩沐辰" },
    { id: "react.react_plan.core.c2", label: "ReAct Plan 规划 · 孙博文", status: "completed", candidate_id: "c2", candidate_name: "孙博文" },
    { id: "react.act_observe.score_deterministic.c2", label: "Act+Observe · score_deterministic · 孙博文", status: "running", candidate_id: "c2", candidate_name: "孙博文" },
    { id: "construction.analyze.c1-legacy", label: "Construction 匹配与出题 · 韩沐辰", status: "completed" },
  ];
  await page.goto("/index.html");
  await page.locator('[data-view="upload"]').click();
  const grouped = await page.evaluate(async (liveSteps) => {
    const mod = await import("/frontend-agent-chain.js");
    const groups = mod.groupAgentChainSteps(liveSteps);
    const panel = document.getElementById("submitted-panel");
    const list = document.getElementById("agent-chain-list");
    if (panel) panel.hidden = false;
    if (list) list.innerHTML = mod.agentChainGroupedMarkup(liveSteps);
    return {
      names: groups.map((group) => group.name),
      hanLabels: groups.find((group) => group.name === "韩沐辰")?.steps.map((step) => step.label) || [],
      sunLabels: groups.find((group) => group.name === "孙博文")?.steps.map((step) => step.label) || [],
    };
  }, steps);
  expect(grouped.names).toEqual(["共用", "韩沐辰", "孙博文"]);
  expect(grouped.hanLabels.every((label) => label.includes("韩沐辰"))).toBeTruthy();
  expect(grouped.sunLabels.every((label) => label.includes("孙博文"))).toBeTruthy();

  const han = page.locator('.agent-chain-group[data-candidate="韩沐辰"]');
  const sun = page.locator('.agent-chain-group[data-candidate="孙博文"]');
  await expect(page.locator('.agent-chain-group[data-candidate="共用"]')).toContainText("解析 JD");
  await expect(han).toContainText("Construction");
  await expect(han).not.toContainText("孙博文");
  await expect(sun).toContainText("ReAct Plan");
  await expect(sun).toContainText("score_deterministic");
  await expect(sun).not.toContainText("韩沐辰");
});
