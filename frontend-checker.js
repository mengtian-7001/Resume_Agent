/** Presentation-only mapping for Checker quality-gate outcomes. */

export function checkerPresentation(breakdown, match, risks, evidence) {
  const degraded = Boolean(breakdown?.checker_degraded);
  const status = String(breakdown?.checker_status || "unknown");
  const firstEvidence = typeof evidence?.[0] === "string"
    ? evidence[0]
    : evidence?.[0]?.quote || evidence?.[0]?.text;
  const ordinarySummary = (typeof risks?.[0] === "string" ? risks[0] : firstEvidence)
    || "已完成自动分析。";
  return {
    degraded,
    status,
    summary: degraded
      ? `质检未通过：${typeof risks?.[0] === "string" ? risks[0] : "结果已降级，请人工复核。"}`
      : ordinarySummary,
    gates: [
      ["硬门槛", match.hard_gate_pass ? "已通过" : "未通过", match.hard_gate_pass ? "通过" : "未通过"],
      ...(degraded ? [["Checker 质检", `状态：${status}`, "未通过"]] : []),
    ],
    audit: breakdown?.checker_audit && typeof breakdown.checker_audit === "object"
      ? breakdown.checker_audit
      : {
        summary: degraded ? "质检未通过或不可用，需人工复核。" : "暂无额外质检问题。",
        reasoning_path: [],
        assumptions: [],
        evidence_summary: [],
        issues: [],
        revised_decision: match.decision,
      },
  };
}
