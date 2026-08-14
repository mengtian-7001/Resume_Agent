# 招聘筛选评测案例

这些案例用于证明产品不会只靠关键词给出推荐。自动化回归由 `./test.sh` 执行，真实文档标签位于 `testdata/`。

| 类型 | 数据来源 | 预期 | 核验重点 |
| --- | --- | --- | --- |
| 优秀匹配 | `jd-ai-agent-llm × resume-cv_001` | 推荐 | 多智能体生产实践、量化结果、完整硬门槛与 10 道验证题。 |
| 部分匹配 | `jd-ai-agent-llm × resume-cv_004` | 人工复核 | 有 LangChain/Function Calling，但复杂编排未上线；需展示风险和追问。 |
| 不匹配 | `jd-etl × resume-cv_001` | 不匹配 | Agent 经历不能替代 ETL/数仓岗位的核心要求。 |
| 信息缺失 | `test_information_missing_is_not_promoted` | 不匹配 | 年限、学历或关键项目缺失时不得自动放行。 |
| 关键词堆砌 | `test_keyword_stuffing_is_a_risk_not_production_proof` | 风险标记/复核 | 技能词出现不等于生产能力；系统必须标记“疑似关键词堆砌”和生产证据不足。 |

## 运行方式

```bash
./test.sh
```

其中：

- 单元测试验证信息缺失、关键词堆砌、证据冲突和 Checker 降级规则。
- `run_sample_doc_eval.py` 使用 8 对人工标注文档，门槛为至少 6/8。
- `run_heldout_doc_eval.py` 解析未参与规则调优的文档，并输出 accuracy、macro-F1、推荐误报和拒绝误杀。

## 解释原则

评测中出现的误差不通过降低门槛掩盖。优先检查是否存在错误硬门槛、错误技能分类、证据不足或不合理的分数—结论映射，再调整规则并留下回归样例。
