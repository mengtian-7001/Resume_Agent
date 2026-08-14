import { expect, test } from "@playwright/test";

test.beforeEach(async ({ page }) => {
  // Keep smoke tests offline and deterministic: exercise the built-in demo path.
  await page.route("**/supabase-config.js", (route) =>
    route.fulfill({ contentType: "application/javascript", body: "window.SUPABASE_CONFIG = {};" }),
  );
  await page.route("**/supabase-config.worker.js", (route) =>
    route.fulfill({ contentType: "application/javascript", body: "" }),
  );
});

test("demo runs one-click JD-plus-resumes flow and opens a candidate", async ({ page }) => {
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
  await expect(page.locator(".detail").first()).toBeVisible();
  await page.locator(".detail").first().click();
  await expect(page.locator("#candidate-drawer")).toHaveClass(/open/);
  await expect(page.locator("#drawer-checker")).toContainText("结论：");
  expect(errors).toEqual([]);
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
});

test("interview workspace records and restores candidate answers and scores", async ({ page }) => {
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto("/index.html");
  await page.locator('[data-view="results"]').click();
  await page.locator(".detail").first().click();
  await page.getByRole("button", { name: "进入面试工作台" }).click();
  await expect(page.locator("#interview")).toHaveClass(/active/);
  await expect(page.locator("#interview-candidate-meta")).toContainText("林知远");
  await page.locator("#interview-answer-0").fill("候选人说明了幂等键与人工接管边界。");
  await page.locator("#interview-score-0").fill("8");
  await expect(page.locator("#interview-total")).toHaveText("8 / 20");

  await page.reload();
  await page.locator('[data-view="interview"]').click();
  await page.locator("#interview-candidate-select").selectOption("林知远");
  await expect(page.locator("#interview-answer-0")).toHaveValue("候选人说明了幂等键与人工接管边界。");
  await expect(page.locator("#interview-score-0")).toHaveValue("8");
  await expect(page.locator("#interview-total")).toHaveText("8 / 20");
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
