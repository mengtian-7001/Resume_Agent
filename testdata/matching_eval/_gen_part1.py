#!/usr/bin/env python3
"""Generate matching-eval batch 1: header + CASE_001..CASE_003."""
from __future__ import annotations

import json
from pathlib import Path


EXTRAS = {
    "CV_012": (
        "补充：会员等级变更走出站消息，接口超时 200ms 内；曾用 Postgres 解释器对照实际执行计划。"
        "容器镜像每周重建，漏洞扫描不过不发布。团队口语把 FastAPI 叫「那个 ASGI 服务」。"
    ),
    "CV_013": (
        "补充：对账差异按商户维度分页回放，支持指定自然日补跑。"
        "FastAPI 依赖注入统一鉴权与审计日志。Docker 健康检查探活 /healthz，失败自动摘流。"
        "带 2 名后端，Code Review 强制覆盖事务边界。"
    ),
    "CV_014": (
        "补充：审批流只有通过/驳回两态，没有会签。接口没有分页和幂等键，偶发重复提交。"
        "测试数据与生产混用过一次，靠手工删行恢复。希望做更复杂的后端但尚未有机会。"
        "Redis 未设 TTL 导致验证码缓存常驻。整体是能交差的 CRUD 后端，离 JD 的治理要求有缺口。"
    ),
    "CV_015": (
        "补充：取数 API 把 SQL 拼在查询参数里，有过注入风险被安全打回。"
        "对后端关心的事务隔离、连接池和缓存击穿几乎没处理过。转岗后端意愿强，证据仍偏数据开发。"
        "Docker 镜像里还装着 Jupyter。PostgreSQL 账号是只读分析账号，发过一次误写被拦。"
    ),
    "CV_016": (
        "补充：React 工作台占工作量约六成。后端 BFF 多数是透传。"
        "一次 Redis 缓存击穿导致登录接口打满数据库，事后只加了 sleep 重试，没有加锁或单飞。"
        "发布失败时需要找运维点控制台。表结构评审从未参加，索引由 DBA 加。"
        "自称全栈，后端基本功停留在能调通接口。"
    ),
    "CV_017": (
        "补充：转正材料里的「独立负责用户模块」实际是改字段校验提示。"
        "PostgreSQL 权限只有 DML。Docker 构建在 CI 红了也不会排障。"
        "职业年限从毕业起算不足一年，不满足硬门槛。导师评价「上手快但还不能值班」。"
        "校园二手书项目无鉴权、无事务、无容器生产发布，不能折算工作年限。应届口径不可放行。工作时间线从 2025 年毕业起算。"
    ),
    "CV_018": (
        "补充：夜班值过生产，处理过 Redis 内存打满和 PostgreSQL 连接数爆。"
        "FastAPI 服务有完整 OpenAPI。专升本考试未通过，人事档案学历仍为大专。"
        "技能与年限足够，卡在学历硬门槛。带过 2 名初级讲解过事务与锁。"
        "大专学历是硬门槛失败原因，能力叙述再完整也不应放行。用于回归教育门槛。技能栈与 JD 高度同构。档案学历字段必须拦截。应 reject。"
    ),
    "CV_019": (
        "补充：设计系统覆盖 120+ 组件，服务过 8 条业务线。"
        "与后端协作仅通过 Swagger 联调。明确不会写 Python，也不想维护 Dockerfile。"
        "跨领域资深前端，不能当作 FastAPI 候选人。"
    ),
    "CV_020": (
        "补充：简历三个版本日期互相打架。技能表按 JD 自动生成痕迹明显。"
        "「亿级」无单位。复制的 JD 原句未改岗位地点杭州/上海混用。"
        "关键词堆砌反作弊样本，结构化字段好看但原文不可信。无仓库、无事故复盘、无同事背书。"
    ),
    "CV_021": (
        "补充：8TB 日批按业务日期分区，倾斜 key 做过加盐打散。"
        "Airflow 失败重试 3 次后转人工；质量探针阻塞下游 ADS。"
        "指标口径与财务对账一致，差错按日清。"
    ),
    "CV_022": (
        "补充：SCD2 生效时间精确到小时。宽表字段 400+，用配置表驱动而不是硬编码。"
        "气流调度（Airflow）与上游 binlog 传感器对过时区问题。"
        "团队内部文档把 Spark 作业称作「分布式计算任务」。"
    ),
    "CV_023": (
        "补充：1200 核作业做过动态分配与推测执行。血缘登记能追到报表字段。"
        "dbt 管核心指标测试，Flink 仅做曝光实时补数。"
        "带 3 人数仓小组，值班手册覆盖补数与回滚。广告域分层从 ODS 到 ADS 齐全，口径评审过财务。"
        "Python 补数框架支持按分区重跑，避免全量回刷。"
    ),
    "CV_024": (
        "补充：看板需求来自运营周会，SQL 以即席为主，很少沉淀成作业。"
        "一次误把全表扫描跑在生产 Impala/Spark SQL，被数据组叫停。"
        "对分区、压缩和幂等补数缺少概念，更像分析师。Airflow 改时间是唯一「调度经验」。"
        "Tableau 仪表盘很漂亮，但数仓工程证据不足，应进灰区而非推荐。"
    ),
    "CV_025": (
        "补充：Java 接口仍偶尔要帮忙改字段。数据组工作以改 YAML 配置为主。"
        "说不清 shuffle 内存溢出怎么调。拉链与快照分不清，评审未通过建模文档。"
        "转岗态度积极，但相关深度不够。Python 质量探针只会断言行数>0。"
        "相关数据工作约一年出头，总年限看起来够但领域匹配偏弱。灰区样本。Java 经历不可直接折算数仓年限。"
    ),
    "CV_026": (
        "补充：crontab 同步夜里两点跑，失败靠第二天有人发现。"
        "POC 用 Spark 读了 3 天日志，没有写入正式数仓。"
        "1:1 复制导致源库改字段下游全断。工程化缺口明显。无 SLA、无补数手册、无维度表。"
        "培训作业不能替代生产 Spark/Airflow 经验，适合灰区观察。"
    ),
    "CV_027": (
        "补充：助理工作是改调度时间、贴错误日志到群里。"
        "独立写过的 SQL 不超过 20 条，且需组长改写才能提交。"
        "入职不满一年，硬门槛年限不通过。无生产作业归属。"
        "Spark 日志看不懂 shuffle 阶段。Airflow 权限只有只读。建模课只听过一次分享。"
        "年限硬门槛失败：约 1 年助理经验，即使关键字齐全也应直接拒绝。不可用实习凑三年。无独立作业、无补数、无建模产出。助理不等于工程师。应 reject。"
    ),
    "CV_028": (
        "补充：零售域日批稳定，库存事实表按门店+SKU+日。"
        "独立处理过上游延迟导致的空分区。人事认定最高学历为大专，专升本在读未毕业。"
        "技能达标，学历硬门槛失败。Python 补数脚本有重跑开关，Airflow SLA 8:30 前必须完成。"
        "大专学历是唯一硬拒绝点，用于验证教育门槛是否被正确拦截。专升本在读不等于本科。其余技能与年限可视为达标。教育门槛回归样本。应 reject。硬拒。"
    ),
    "CV_029": (
        "补充：GMV 目标拆解、渠道扣点和促销节奏是日常。"
        "所谓数据团队是外包报表组。本人不写作业、不值班、不处理失败 DAG。"
        "跨领域运营高管，不能当作数据工程师。Excel 透视表是最复杂的「数据处理」。"
        "资历看起来很高，方向完全不对，应因缺必备技能拒绝。跨领域 senior 反作弊。缺 SQL/Spark/Airflow/Python/数据建模。"
    ),
    "CV_030": (
        "补充：技能词按招聘启事顺序排列。万亿级无表规模、无集群规模。"
        "「正在学习 SELECT」出现在自我评价末行。手机闹钟调度与 Airflow 并列。"
        "关键词堆砌反作弊样本，原文自相矛盾。无表名、无分区、无失败案例、无血缘。"
        "确定性字段可能通过，LLM 侧必须低分并拒绝。"
    ),
}


def resume(
    resume_id,
    raw_text,
    name,
    years,
    education,
    skills,
    experiences,
    projects,
    label,
    hard_gate_pass,
    expected_decision,
    score_min,
    score_max,
    match_reasons,
    penalty_reasons,
    quotes,
):
    extra = EXTRAS.get(resume_id)
    if extra:
        raw_text = raw_text.rstrip() + "\n" + extra
    for q in quotes:
        if q not in raw_text:
            raise ValueError(f"{resume_id}: quote not in raw_text: {q!r}")
    n = len(raw_text)
    if not (400 <= n <= 900):
        raise ValueError(f"{resume_id}: raw_text len={n} not in 400-900")
    return {
        "resume_id": resume_id,
        "raw_text": raw_text,
        "structured": {
            "name": name,
            "years_experience": years,
            "education": education,
            "skills": skills,
            "experiences": experiences,
            "projects": projects,
        },
        "ground_truth": {
            "label": label,
            "hard_gate_pass": hard_gate_pass,
            "expected_decision": expected_decision,
            "score_band": {"min": score_min, "max": score_max},
            "must_match_reasons": match_reasons,
            "must_penalty_reasons": penalty_reasons,
            "critical_evidence_quotes": quotes,
        },
    }


def check_job(job):
    n = len(job["raw_text"])
    if not (300 <= n <= 600):
        raise ValueError(f"{job['job_id']}: raw_text len={n} not in 300-600")


CASE_001_JOB = {
    "job_id": "JD_001",
    "title": "AI Agent / LLM 应用工程师",
    "raw_text": (
        "【岗位名称】AI Agent / LLM 应用工程师\n"
        "【工作地点】上海（可接受每周 2 天远程）\n"
        "【岗位职责】\n"
        "1. 负责智能助手与内部 Copilot 的 Agent 编排，将业务系统以 Function Calling 接入大模型。\n"
        "2. 基于 LangChain 搭建 Multi-Agent 协作：任务拆解、记忆、重试与人工接管。\n"
        "3. 设计 Prompt Engineering 方案与评测回归，降低幻觉和工具误调用。\n"
        "4. 与后端协作完成 Agent 服务化，关注延迟、成本与 tracing。\n"
        "【任职要求】\n"
        "1. 本科及以上学历，3 年及以上 Python 开发经验。\n"
        "2. 熟悉 LangChain，能独立设计 Agent 工作流。\n"
        "3. 有 Function Calling / Tool Use 落地经验，能处理鉴权、幂等与失败重试。\n"
        "4. 有 Multi-Agent 协作或复杂任务编排经验。\n"
        "5. 能针对任务编写系统提示词与少样本示例。\n"
        "【加分项】LangGraph、FastAPI、MCP、Agent 评测体系。"
    ),
    "structured": {
        "must_have_skills": [
            "Python",
            "LangChain",
            "Function Calling",
            "Multi-Agent",
            "Prompt Engineering",
        ],
        "nice_to_have_skills": ["LangGraph", "FastAPI", "MCP", "评测体系"],
        "min_years": 3,
        "education": "本科",
        "location": "上海",
        "hard_gates": [
            {"field": "min_years", "op": ">=", "value": 3},
            {"field": "education", "op": ">=", "value": "本科"},
            {
                "field": "must_have_skills",
                "op": "covers_all",
                "value": [
                    "Python",
                    "LangChain",
                    "Function Calling",
                    "Multi-Agent",
                    "Prompt Engineering",
                ],
            },
        ],
    },
}

CASE_002_JOB = {
    "job_id": "JD_002",
    "title": "后端开发工程师（Python / FastAPI）",
    "raw_text": (
        "【岗位名称】后端开发工程师（Python / FastAPI）\n"
        "【工作地点】杭州\n"
        "【岗位职责】\n"
        "1. 基于 FastAPI 设计并实现对外 HTTP API 与内部服务，保证接口契约稳定。\n"
        "2. 使用 PostgreSQL 进行建模、索引与事务设计，支撑核心业务写路径。\n"
        "3. 基于 Redis 实现缓存、分布式锁与限流，控制热点与超卖。\n"
        "4. 将服务容器化（Docker）并接入 CI，编写可回滚的发布方案。\n"
        "【任职要求】\n"
        "1. 本科及以上学历，3 年及以上 Python 后端经验。\n"
        "2. 熟练使用 FastAPI（或同等 ASGI 框架）及 Pydantic 数据校验。\n"
        "3. 熟悉 PostgreSQL：索引、锁、慢查询与迁移。\n"
        "4. 有 Redis 生产使用经验。\n"
        "5. 能独立编写 Dockerfile 与多环境配置。\n"
        "【加分项】Kubernetes、Celery、gRPC、OpenTelemetry、AWS。"
    ),
    "structured": {
        "must_have_skills": ["Python", "FastAPI", "PostgreSQL", "Redis", "Docker"],
        "nice_to_have_skills": ["Kubernetes", "Celery", "gRPC", "OpenTelemetry"],
        "min_years": 3,
        "education": "本科",
        "location": "杭州",
        "hard_gates": [
            {"field": "min_years", "op": ">=", "value": 3},
            {"field": "education", "op": ">=", "value": "本科"},
            {
                "field": "must_have_skills",
                "op": "covers_all",
                "value": ["Python", "FastAPI", "PostgreSQL", "Redis", "Docker"],
            },
        ],
    },
}

CASE_003_JOB = {
    "job_id": "JD_003",
    "title": "数据工程师（ETL / 数仓）",
    "raw_text": (
        "【岗位名称】数据工程师（ETL / 数仓）\n"
        "【工作地点】北京\n"
        "【岗位职责】\n"
        "1. 负责业务库到数仓的 ETL，使用 Spark 完成日批与补数作业。\n"
        "2. 用 Airflow 编排依赖、告警与 SLA，保障早晨报表按时产出。\n"
        "3. 基于维度建模设计 ODS/DWD/DWS/ADS 分层，沉淀可复用指标。\n"
        "4. 用 SQL 与 Python 做质量校验：空值、主键冲突、波动监控。\n"
        "【任职要求】\n"
        "1. 本科及以上学历，3 年及以上数据开发经验。\n"
        "2. 精通 SQL，能独立完成复杂关联与窗口分析。\n"
        "3. 有 Spark（PySpark）生产作业经验，了解分区、shuffle 与倾斜处理。\n"
        "4. 熟悉 Airflow DAG 编写、重试与依赖管理。\n"
        "5. 具备数据建模能力，理解事实表与维度表。\n"
        "【加分项】dbt、Flink、Hive、Kafka、ClickHouse。"
    ),
    "structured": {
        "must_have_skills": ["SQL", "Spark", "Airflow", "Python", "数据建模"],
        "nice_to_have_skills": ["dbt", "Flink", "Hive", "Kafka"],
        "min_years": 3,
        "education": "本科",
        "location": "北京",
        "hard_gates": [
            {"field": "min_years", "op": ">=", "value": 3},
            {"field": "education", "op": ">=", "value": "本科"},
            {
                "field": "must_have_skills",
                "op": "covers_all",
                "value": ["SQL", "Spark", "Airflow", "Python", "数据建模"],
            },
        ],
    },
}


def case_001_resumes():
    return [
        resume(
            resume_id="CV_001",
            raw_text=(
                "姓名：林知远\n"
                "求职意向：AI Agent / LLM 应用工程师\n"
                "教育经历：东湖理工大学 计算机科学与技术 本科 2016-2020\n"
                "工作经历：\n"
                "澄空智能 | Agent 平台工程师 | 2021.03-至今\n"
                "主导设计了工单处理 Multi-Agent：拆解、指派、工具调用与人工接管。"
                "使用 LangChain 与自研编排层对接 20+ 内部 API，统一 Function Calling 协议（鉴权、幂等、超时重试）。"
                "负责 Prompt Engineering 与评测集建设，将工具误调用率从 11% 降至 3.2%。\n"
                "拾光纪科技 | Python 后端工程师 | 2020.07-2021.03\n"
                "用 Python 维护客服中台接口与审计日志，为后续 Agent 化改造打底。\n"
                "项目：\n"
                "「澄助手」企业内部 Copilot，支持查库、提单、改权限，日均调用约 1.2 万次。"
                "多 Agent 协作编排器实现规划-执行-反思循环，支持并行工具调用。\n"
                "技能：Python、LangChain、Function Calling、Multi-Agent、Prompt Engineering、FastAPI、LangGraph\n"
                "合计 5 年 Python 开发，近 4 年专注 LLM 应用与 Agent 生产落地。"
            ),
            name="林知远",
            years=5,
            education="本科",
            skills=[
                "Python",
                "LangChain",
                "Function Calling",
                "Multi-Agent",
                "Prompt Engineering",
                "FastAPI",
                "LangGraph",
            ],
            experiences=[
                {
                    "company": "澄空智能",
                    "title": "Agent 平台工程师",
                    "years": 4.4,
                    "bullets": [
                        "主导工单 Multi-Agent 与 Function Calling 协议",
                        "建设 Prompt 评测集，误调用率 11%→3.2%",
                    ],
                },
                {
                    "company": "拾光纪科技",
                    "title": "Python 后端工程师",
                    "years": 0.7,
                    "bullets": ["维护客服中台接口与审计日志"],
                },
            ],
            projects=[
                {
                    "name": "澄助手 Copilot",
                    "bullets": ["日均 1.2 万次调用，覆盖查库/提单/改权限"],
                }
            ],
            label="good",
            hard_gate_pass=True,
            expected_decision="recommend",
            score_min=86,
            score_max=100,
            match_reasons=[
                "年限与学历达标，必备技能全覆盖",
                "有生产级 Multi-Agent 与 Function Calling 证据",
                "Prompt 评测与误调用率有量化结果",
            ],
            penalty_reasons=[],
            quotes=[
                "主导设计了工单处理 Multi-Agent：拆解、指派、工具调用与人工接管。",
                "将工具误调用率从 11% 降至 3.2%。",
            ],
        ),
        resume(
            resume_id="CV_002",
            raw_text=(
                "姓名：周启明\n"
                "求职意向：大模型应用开发\n"
                "教育经历：南港大学 软件工程 硕士 2018-2021；江宁学院 软件工程 本科 2014-2018\n"
                "工作经历：\n"
                "北纬实验室 | 智能体研发工程师 | 2022.01-至今\n"
                "基于大模型搭建客服中台多智能体：意图识别智能体、工具执行智能体与质检智能体协同。"
                "把业务接口封装成工具调用（tool calling），含签名校验与失败补偿。"
                "用提示词工程维护系统提示词版本，按场景做少样本替换。\n"
                "问舟信息 | 后端开发 | 2021.07-2021.12\n"
                "使用 Python 做内部审批流，后续被智能体侧复用为可调用工具。\n"
                "项目：\n"
                "「舟问」客服中台：日均会话 8000+，人工接管率下降 18%。"
                "编排侧使用 LangChain 生态的 LCEL 与图工作流（常称 LangGraph）做状态机。\n"
                "技能：Python、大模型、工具调用、多智能体、提示词工程、LCEL、LangGraph\n"
                "4 年相关经验。技能表述多用中文别名，与英文栈等价。"
            ),
            name="周启明",
            years=4,
            education="硕士",
            skills=[
                "Python",
                "大模型",
                "工具调用",
                "多智能体",
                "提示词工程",
                "LCEL",
                "LangGraph",
            ],
            experiences=[
                {
                    "company": "北纬实验室",
                    "title": "智能体研发工程师",
                    "years": 3.6,
                    "bullets": [
                        "客服中台多智能体协同",
                        "业务接口封装为 tool calling",
                    ],
                },
                {
                    "company": "问舟信息",
                    "title": "后端开发",
                    "years": 0.5,
                    "bullets": ["内部审批流，后复用为智能体工具"],
                },
            ],
            projects=[
                {
                    "name": "舟问客服中台",
                    "bullets": ["日均 8000+ 会话，人工接管率下降 18%"],
                }
            ],
            label="good",
            hard_gate_pass=True,
            expected_decision="recommend",
            score_min=82,
            score_max=96,
            match_reasons=[
                "同义改写样本：大模型/工具调用/多智能体/提示词工程对应 JD 必备项",
                "有多智能体生产协作与量化结果",
                "学历硕士、年限达标",
            ],
            penalty_reasons=["未直接写 LangChain 英文词，需同义识别"],
            quotes=[
                "基于大模型搭建客服中台多智能体：意图识别智能体、工具执行智能体与质检智能体协同。",
                "把业务接口封装成工具调用（tool calling），含签名校验与失败补偿。",
            ],
        ),
        resume(
            resume_id="CV_003",
            raw_text=(
                "姓名：韩沐辰\n"
                "求职意向：LLM 应用 / Agent 工程师\n"
                "教育经历：西麓大学 人工智能 本科 2015-2019\n"
                "工作经历：\n"
                "星河对话 | 高级应用工程师 | 2019.07-至今\n"
                "6 年持续做对话与 Agent 产品。近三年把单轮 Bot 升级为可执行工具的 Agent。"
                "用 Python + LangChain 实现代码 Agent 与浏览器 Agent 双通道，生产环境日活约 3000。"
                "Function Calling 层对接 IDE 插件与内部知识检索，含并发限流。"
                "Multi-Agent 采用规划器+执行器+评论器，失败任务自动降级为人工。"
                "Prompt Engineering 方面维护 40+ 模板，按任务类型做自动路由。\n"
                "项目：\n"
                "「星河码助手」：补全、修单测、查日志三位一体，平均每任务调用工具 6.4 次。"
                "自研 tracing 把每步工具参数落盘，便于复盘幻觉。\n"
                "技能：Python、LangChain、Function Calling、Multi-Agent、Prompt Engineering、MCP（试用）"
            ),
            name="韩沐辰",
            years=6,
            education="本科",
            skills=[
                "Python",
                "LangChain",
                "Function Calling",
                "Multi-Agent",
                "Prompt Engineering",
                "MCP",
            ],
            experiences=[
                {
                    "company": "星河对话",
                    "title": "高级应用工程师",
                    "years": 6.1,
                    "bullets": [
                        "代码 Agent 与浏览器 Agent 生产落地",
                        "规划器+执行器+评论器 Multi-Agent",
                    ],
                }
            ],
            projects=[
                {
                    "name": "星河码助手",
                    "bullets": ["平均每任务调用工具 6.4 次", "tracing 落盘复盘"],
                }
            ],
            label="good",
            hard_gate_pass=True,
            expected_decision="recommend",
            score_min=88,
            score_max=100,
            match_reasons=[
                "年限最长且与岗位强相关",
                "代码/浏览器双 Agent 生产证据充分",
                "必备技能与评测/tracing 加分项都有",
            ],
            penalty_reasons=[],
            quotes=[
                "用 Python + LangChain 实现代码 Agent 与浏览器 Agent 双通道，生产环境日活约 3000。",
                "Multi-Agent 采用规划器+执行器+评论器，失败任务自动降级为人工。",
            ],
        ),
        resume(
            resume_id="CV_004",
            raw_text=(
                "姓名：陈思齐\n"
                "求职意向：大模型应用工程师\n"
                "教育经历：青城学院 计算机 本科 2018-2022\n"
                "工作经历：\n"
                "木兰计算 | NLP 应用工程师 | 2022.07-至今（满 3 年）\n"
                "主要做企业知识库问答与 FAQ 机器人，技术栈含 Python、LangChain 检索链。"
                "Prompt Engineering 以改系统提示词和拒答策略为主，缺少复杂工具规划。"
                "Function Calling 仅对接过「查文档」「查工单状态」两个只读接口，无写操作、无幂等设计。"
                "Multi-Agent 停留在技术预研：做过一次规划器 Demo，未上线。\n"
                "实习（2021 暑期）做过数据标注平台后端，与 Agent 无关。\n"
                "项目：\n"
                "「木兰问问」RAG 问答，准确率 71%，用户反馈「不会办事只会答」。\n"
                "技能：Python、LangChain、Function Calling、Multi-Agent、Prompt Engineering、RAG\n"
                "年限刚达标，方向偏问答而非可执行 Agent。"
            ),
            name="陈思齐",
            years=3,
            education="本科",
            skills=[
                "Python",
                "LangChain",
                "Function Calling",
                "Multi-Agent",
                "Prompt Engineering",
                "RAG",
            ],
            experiences=[
                {
                    "company": "木兰计算",
                    "title": "NLP 应用工程师",
                    "years": 3.0,
                    "bullets": [
                        "知识库问答与 FAQ 机器人",
                        "Function Calling 仅两个只读接口",
                    ],
                }
            ],
            projects=[
                {
                    "name": "木兰问问",
                    "bullets": ["RAG 问答准确率 71%，不能执行业务动作"],
                }
            ],
            label="partial",
            hard_gate_pass=True,
            expected_decision="gray",
            score_min=64,
            score_max=73,
            match_reasons=[
                "学历年限达标，结构化技能覆盖必备项",
                "有 LangChain 与浅层 Function Calling",
            ],
            penalty_reasons=[
                "项目以 FAQ/RAG 为主，可执行 Agent 弱",
                "Multi-Agent 仅 Demo 未上线",
            ],
            quotes=[
                "Function Calling 仅对接过「查文档」「查工单状态」两个只读接口，无写操作、无幂等设计。",
                "Multi-Agent 停留在技术预研：做过一次规划器 Demo，未上线。",
            ],
        ),
        resume(
            resume_id="CV_005",
            raw_text=(
                "姓名：吴晓岚\n"
                "求职意向：LLM 应用开发（由算法转应用）\n"
                "教育经历：滨江大学 电子信息 本科 2017-2021\n"
                "工作经历：\n"
                "蓝桉网络 | 算法工程师 | 2021.07-2025.06\n"
                "4 年以训练与评测为主：分类模型、小规模指令微调、BLEU/人工抽检。"
                "2024 年起用 LangChain 把微调模型包成对话 Demo，供销售演示。"
                "Prompt Engineering 经验来自标注规范与指令模板，不是工具编排。"
                "Function Calling 与 Multi-Agent 均为跟读开源教程后的本地实验，无生产流量。\n"
                "项目：\n"
                "垂类客服 SFT 模型，实验室准确率 78%；应用侧无 SLA。\n"
                "技能：Python、PyTorch、LangChain、Function Calling、Multi-Agent、Prompt Engineering\n"
                "领域略偏模型训练，应用编排证据不足。转岗动机是觉得训练周期长、想做可见的产品闭环，但目前仍无法独立设计工具权限与重试。"
            ),
            name="吴晓岚",
            years=4,
            education="本科",
            skills=[
                "Python",
                "PyTorch",
                "LangChain",
                "Function Calling",
                "Multi-Agent",
                "Prompt Engineering",
            ],
            experiences=[
                {
                    "company": "蓝桉网络",
                    "title": "算法工程师",
                    "years": 4.0,
                    "bullets": [
                        "分类/指令微调与评测",
                        "用 LangChain 包装演示 Demo",
                    ],
                }
            ],
            projects=[
                {
                    "name": "垂类客服 SFT",
                    "bullets": ["实验室准确率 78%，无生产 SLA"],
                }
            ],
            label="partial",
            hard_gate_pass=True,
            expected_decision="gray",
            score_min=60,
            score_max=70,
            match_reasons=[
                "年限学历达标，技能列表覆盖必备关键字",
                "有 LangChain 包装与提示词模板经验",
            ],
            penalty_reasons=[
                "主业是训练/微调，Agent 编排无生产流量",
                "Function Calling 与 Multi-Agent 仅为本地实验",
            ],
            quotes=[
                "4 年以训练与评测为主：分类模型、小规模指令微调、BLEU/人工抽检。",
                "Function Calling 与 Multi-Agent 均为跟读开源教程后的本地实验，无生产流量。",
            ],
        ),
        resume(
            resume_id="CV_006",
            raw_text=(
                "姓名：赵予安\n"
                "求职意向：Agent 开发\n"
                "教育经历：禾山大学 软件工程 硕士 2019-2022\n"
                "工作经历：\n"
                "橙湾科技 | 全栈开发 | 2022.07-至今（3 年）\n"
                "前两年做管理后台（Vue + 普通 Python 脚本），2024 下半年才接触大模型。"
                "用 LangChain 做过一个内部周报摘要 Bot，Prompt Engineering 主要是改语气。"
                "Function Calling 接过日历查询；写操作被安全组禁止，没有重试与幂等。"
                "自称了解 Multi-Agent，实际是顺序调用两个 chain，无辩论/投票/角色分工。\n"
                "技能：Python、LangChain、Function Calling、Multi-Agent、Prompt Engineering、Vue\n"
                "相关深度不足一年，整体偏全栈转 LLM。周报 Bot 仅在 12 人团队内使用，没有评测集，也没有 tracing。"
            ),
            name="赵予安",
            years=3,
            education="硕士",
            skills=[
                "Python",
                "LangChain",
                "Function Calling",
                "Multi-Agent",
                "Prompt Engineering",
                "Vue",
            ],
            experiences=[
                {
                    "company": "橙湾科技",
                    "title": "全栈开发",
                    "years": 3.0,
                    "bullets": [
                        "前两年管理后台",
                        "近一年周报摘要 Bot 与日历查询工具",
                    ],
                }
            ],
            projects=[
                {
                    "name": "周报摘要 Bot",
                    "bullets": ["顺序调用两条 chain，无真正多角色协作"],
                }
            ],
            label="partial",
            hard_gate_pass=True,
            expected_decision="gray",
            score_min=61,
            score_max=71,
            match_reasons=[
                "硕士学历、3 年总年限、技能字段齐",
                "有 LangChain 与一次 Function Calling 接入",
            ],
            penalty_reasons=[
                "LLM/Agent 深度不足一年",
                "Multi-Agent 名不副实，仅为顺序 chain",
            ],
            quotes=[
                "前两年做管理后台（Vue + 普通 Python 脚本），2024 下半年才接触大模型。",
                "自称了解 Multi-Agent，实际是顺序调用两个 chain，无辩论/投票/角色分工。",
            ],
        ),
        resume(
            resume_id="CV_007",
            raw_text=(
                "姓名：孙博文\n"
                "求职意向：AI Agent 工程师\n"
                "教育经历：榕城大学 计算机 本科 2021-2025\n"
                "工作经历：\n"
                "云帆数字 | AI 应用实习生/初级工程师 | 2025.07-至今（约 1 年）\n"
                "在导师带领下用 Python 和 LangChain 改过内部 Demo。"
                "跟做过 Function Calling 实验：调用天气 API。"
                "阅读过 Multi-Agent 开源项目 README，未独立上线。"
                "Prompt Engineering 主要是把产品给的提示词贴进配置文件。\n"
                "校园项目：毕业设计「基于 LangChain 的课程问答」，无工具执行。\n"
                "技能：Python、LangChain、Function Calling、Multi-Agent、Prompt Engineering\n"
                "学历达标且关键字齐全，但从业年限约 1 年，不满足 3 年硬门槛。实习周报显示工作以改配置和跑通 notebook 为主。"
            ),
            name="孙博文",
            years=1,
            education="本科",
            skills=[
                "Python",
                "LangChain",
                "Function Calling",
                "Multi-Agent",
                "Prompt Engineering",
            ],
            experiences=[
                {
                    "company": "云帆数字",
                    "title": "初级工程师",
                    "years": 1.0,
                    "bullets": ["导师带领改 Demo", "天气 API Function Calling 实验"],
                }
            ],
            projects=[
                {
                    "name": "课程问答毕业设计",
                    "bullets": ["LangChain 问答，无工具执行"],
                }
            ],
            label="poor",
            hard_gate_pass=False,
            expected_decision="reject",
            score_min=18,
            score_max=45,
            match_reasons=["本科，技能关键字能对上"],
            penalty_reasons=["年限约 1 年，低于 min_years=3", "无独立生产落地"],
            quotes=[
                "云帆数字 | AI 应用实习生/初级工程师 | 2025.07-至今（约 1 年）",
                "从业年限约 1 年，不满足 3 年硬门槛。",
            ],
        ),
        resume(
            resume_id="CV_008",
            raw_text=(
                "姓名：郑小禾\n"
                "求职意向：LLM 应用工程师\n"
                "教育经历：滨海职业技术学院 计算机应用 大专 2016-2019\n"
                "工作经历：\n"
                "磐石软件 | 应用开发 | 2019.08-至今（5 年+）\n"
                "长期用 Python 做内部工具。2023 后用 LangChain 做销售助手。"
                "Function Calling 对接 CRM 查询与写跟进记录，有重试。"
                "做过销售/售前两个角色的 Multi-Agent 试点，小范围使用。"
                "Prompt Engineering 按行业话术维护提示词库。\n"
                "技能：Python、LangChain、Function Calling、Multi-Agent、Prompt Engineering\n"
                "补充：销售助手覆盖话术推荐与 CRM 回写，小范围 30 人使用，无正式 SLA。"
                "自学补过专升本课程但未取得本科学历。团队无硕士以上同事带教。\n"
                "能力描述接近岗位，但最高学历为大专，不满足本科硬门槛。"
            ),
            name="郑小禾",
            years=5,
            education="大专",
            skills=[
                "Python",
                "LangChain",
                "Function Calling",
                "Multi-Agent",
                "Prompt Engineering",
            ],
            experiences=[
                {
                    "company": "磐石软件",
                    "title": "应用开发",
                    "years": 5.8,
                    "bullets": [
                        "销售助手 LangChain 落地",
                        "CRM Function Calling 与双角色试点",
                    ],
                }
            ],
            projects=[
                {
                    "name": "销售助手",
                    "bullets": ["查询 CRM 并写跟进，小范围 Multi-Agent"],
                }
            ],
            label="poor",
            hard_gate_pass=False,
            expected_decision="reject",
            score_min=22,
            score_max=48,
            match_reasons=["年限与技能看起来匹配"],
            penalty_reasons=["学历大专，低于本科硬门槛"],
            quotes=[
                "教育经历：滨海职业技术学院 计算机应用 大专 2016-2019",
                "最高学历为大专，不满足本科硬门槛。",
            ],
        ),
        resume(
            resume_id="CV_009",
            raw_text=(
                "姓名：马景行\n"
                "求职意向：高级工程师 / 技术专家\n"
                "教育经历：京华大学 计算机 本科 2008-2012\n"
                "工作经历：\n"
                "银杏网络 | 首席 Java 架构师 | 2016.03-至今\n"
                "12 年 Java / Spring Cloud 经验，带过 30 人中台团队。"
                "主导过交易中台、权限中台与全链路压测，QPS 峰值 8 万。"
                "技术栈以 Java、Kafka、MySQL、Kubernetes 为主，日常不写 Python。"
                "未使用过 LangChain，无 Function Calling、无 Multi-Agent、无 Prompt Engineering 实践。"
                "认为大模型「可以以后再学」，当前简历不含任何 LLM 项目。\n"
                "技能：Java、Spring Cloud、MySQL、Kafka、Kubernetes、系统架构\n"
                "补充：近期述职仍以服务治理、容量规划与容灾演练为主，AI 只出现在战略 PPT。"
                "拒绝把核心链路交给不确定的模型输出。简历中的「智能化」实为规则引擎。\n"
                "跨领域资深样本：资历强但方向与 JD 完全不符。"
            ),
            name="马景行",
            years=12,
            education="本科",
            skills=[
                "Java",
                "Spring Cloud",
                "MySQL",
                "Kafka",
                "Kubernetes",
                "系统架构",
            ],
            experiences=[
                {
                    "company": "银杏网络",
                    "title": "首席 Java 架构师",
                    "years": 9.4,
                    "bullets": ["交易/权限中台", "峰值 QPS 8 万"],
                }
            ],
            projects=[
                {
                    "name": "交易中台",
                    "bullets": ["Spring Cloud 微服务，与 LLM/Agent 无关"],
                }
            ],
            label="poor",
            hard_gate_pass=False,
            expected_decision="reject",
            score_min=5,
            score_max=35,
            match_reasons=["本科，资历深，工程能力强"],
            penalty_reasons=[
                "跨领域：Java 架构，缺失全部 Agent 必备技能",
                "明确无 LangChain / Function Calling / Multi-Agent",
            ],
            quotes=[
                "12 年 Java / Spring Cloud 经验，带过 30 人中台团队。",
                "未使用过 LangChain，无 Function Calling、无 Multi-Agent、无 Prompt Engineering 实践。",
            ],
        ),
        resume(
            resume_id="CV_010",
            raw_text=(
                "姓名：钱自立\n"
                "求职意向：AI Agent / LLM 应用工程师\n"
                "教育经历：自称 某 985 本科 计算机\n"
                "工作经历：\n"
                "10 年 AI 专家 / 同时写 3 年应届经历（互相矛盾）。\n"
                "技能堆砌：Python、LangChain、Function Calling、Multi-Agent、Prompt Engineering、"
                "LangGraph、FastAPI、MCP、RAG、Agent、LLM、工具调用、多智能体、提示词工程、"
                "OpenAI、Claude、LlamaIndex、AutoGPT、CrewAI、Semantic Kernel。\n"
                "项目：「超级 AGI 操作系统」，职责为熟悉以上全部关键词；无仓库、无指标、无用户。"
                "另一处写「从未独立上线过任何服务」。\n"
                "自我评价同时出现「十年架构师」「应届可实习」和「远程兼职一周交付 AGI」。"
                "联系方式与教育信息互相涂改痕迹明显。无 Git 链接、无演示、无同事可背书。\n"
                "经历空洞且自相矛盾，属于关键词堆砌反作弊样本。"
            ),
            name="钱自立",
            years=5,
            education="本科",
            skills=[
                "Python",
                "LangChain",
                "Function Calling",
                "Multi-Agent",
                "Prompt Engineering",
                "LangGraph",
                "FastAPI",
                "MCP",
            ],
            experiences=[
                {
                    "company": "未具名",
                    "title": "AI 专家",
                    "years": 5,
                    "bullets": ["罗列关键词，无具体职责"],
                }
            ],
            projects=[
                {
                    "name": "超级 AGI 操作系统",
                    "bullets": ["无仓库、无指标、无用户"],
                }
            ],
            label="poor",
            hard_gate_pass=True,
            expected_decision="reject",
            score_min=0,
            score_max=40,
            match_reasons=["结构化字段看起来齐（技能/学历/年限）"],
            penalty_reasons=[
                "关键词堆砌，经历空洞",
                "年限与应届描述自相矛盾",
                "明确承认从未独立上线过任何服务",
            ],
            quotes=[
                "10 年 AI 专家 / 同时写 3 年应届经历（互相矛盾）。",
                "另一处写「从未独立上线过任何服务」。",
            ],
        ),
    ]


def case_002_resumes():
    return [
        resume(
            resume_id="CV_011",
            raw_text=(
                "姓名：何景川\n"
                "求职意向：Python 后端 / FastAPI\n"
                "教育经历：钱塘大学 软件工程 本科 2016-2020\n"
                "工作经历：\n"
                "潮汐后端 | 后端工程师 | 2021.03-至今\n"
                "负责订单服务：FastAPI + Pydantic 对外提供 REST，峰值 QPS 1200。"
                "PostgreSQL 设计订单/库存表，处理超卖用事务与部分唯一索引；慢查询从 800ms 降到 40ms。"
                "Redis 做库存预扣与分布式锁，热点 key 拆分。"
                "全部服务 Docker 多阶段构建，开发/预发/生产三套 compose。\n"
                "镜湖数据 | Python 开发 | 2020.07-2021.02\n"
                "写内部 ETL 小工具，开始接触 PostgreSQL。\n"
                "项目：促销活动中台，支持秒杀令牌桶限流。\n"
                "技能：Python、FastAPI、PostgreSQL、Redis、Docker、Celery\n"
                "4.5 年后端，业务与 JD 高度同构。"
            ),
            name="何景川",
            years=4,
            education="本科",
            skills=["Python", "FastAPI", "PostgreSQL", "Redis", "Docker", "Celery"],
            experiences=[
                {
                    "company": "潮汐后端",
                    "title": "后端工程师",
                    "years": 4.4,
                    "bullets": [
                        "FastAPI 订单服务 QPS 1200",
                        "PostgreSQL 慢查询 800ms→40ms，Redis 预扣",
                    ],
                },
                {
                    "company": "镜湖数据",
                    "title": "Python 开发",
                    "years": 0.6,
                    "bullets": ["内部 ETL 小工具"],
                },
            ],
            projects=[
                {
                    "name": "促销活动中台",
                    "bullets": ["秒杀令牌桶限流", "Docker 三环境"],
                }
            ],
            label="good",
            hard_gate_pass=True,
            expected_decision="recommend",
            score_min=85,
            score_max=100,
            match_reasons=[
                "必备技能均有生产证据",
                "有 QPS、慢查询等量化结果",
                "年限学历达标",
            ],
            penalty_reasons=[],
            quotes=[
                "负责订单服务：FastAPI + Pydantic 对外提供 REST，峰值 QPS 1200。",
                "PostgreSQL 设计订单/库存表，处理超卖用事务与部分唯一索引；慢查询从 800ms 降到 40ms。",
            ],
        ),
        resume(
            resume_id="CV_012",
            raw_text=(
                "姓名：冯嘉树\n"
                "求职意向：后端开发\n"
                "教育经历：西湖研究院 计算机 硕士 2017-2020\n"
                "工作经历：\n"
                "青柠云 | 服务端开发 | 2020.08-至今\n"
                "用 Python 编写会员中台。接口层采用 ASGI 框架 FastAPI 的等价写法（Starlette+Pydantic），"
                "对外 OpenAPI 自动生成。\n"
                "主库为 Postgres（即 PostgreSQL），做过分区表与逻辑复制演练。"
                "缓存中间件使用 Redis 集群，完成会话与验证码。"
                "镜像方面独立编写 Dockerfile 与 distroless 基础镜像，走 GitLab CI 发布。\n"
                "技能：Python、ASGI、Pydantic、Postgres、Redis 集群、容器化、Docker\n"
                "同义改写样本：Postgres/ASGI/容器化对应 PostgreSQL/FastAPI/Docker。"
            ),
            name="冯嘉树",
            years=5,
            education="硕士",
            skills=["Python", "ASGI", "Pydantic", "Postgres", "Redis 集群", "容器化", "Docker"],
            experiences=[
                {
                    "company": "青柠云",
                    "title": "服务端开发",
                    "years": 5.0,
                    "bullets": [
                        "会员中台 ASGI + Pydantic",
                        "Postgres 分区表，Redis 集群会话",
                    ],
                }
            ],
            projects=[
                {
                    "name": "会员中台",
                    "bullets": ["OpenAPI 自动生成", "distroless 镜像 CI 发布"],
                }
            ],
            label="good",
            hard_gate_pass=True,
            expected_decision="recommend",
            score_min=80,
            score_max=95,
            match_reasons=[
                "同义改写：ASGI/Postgres/容器化应对应必备技能，不应因字面否决",
                "5 年会员中台，证据具体",
            ],
            penalty_reasons=["FastAPI 未作为标题词出现，需别名识别"],
            quotes=[
                "接口层采用 ASGI 框架 FastAPI 的等价写法（Starlette+Pydantic），对外 OpenAPI 自动生成。",
                "主库为 Postgres（即 PostgreSQL），做过分区表与逻辑复制演练。",
            ],
        ),
        resume(
            resume_id="CV_013",
            raw_text=(
                "姓名：曹念秋\n"
                "求职意向：Python 后端工程师\n"
                "教育经历：运河大学 计算机 本科 2014-2018\n"
                "工作经历：\n"
                "折纸互联 | 高级后端 | 2018.07-至今\n"
                "6 年 Python。主导账户与支付对账服务，框架从 Flask 迁到 FastAPI，迁移 80+ 路由。"
                "PostgreSQL 负责对账差异表与日切任务，引入 advisory lock 防并发对账。"
                "Redis 实现幂等键与接口限流。"
                "Docker 化后接入公司 K8s，但本人能独立写 Dockerfile 与健康检查。\n"
                "项目：跨境对账平台，日处理 300 万流水，差错率 0.02%。\n"
                "技能：Python、FastAPI、PostgreSQL、Redis、Docker、Kubernetes、gRPC"
            ),
            name="曹念秋",
            years=6,
            education="本科",
            skills=[
                "Python",
                "FastAPI",
                "PostgreSQL",
                "Redis",
                "Docker",
                "Kubernetes",
                "gRPC",
            ],
            experiences=[
                {
                    "company": "折纸互联",
                    "title": "高级后端",
                    "years": 7.0,
                    "bullets": [
                        "Flask 迁 FastAPI，80+ 路由",
                        "对账 advisory lock，日 300 万流水",
                    ],
                }
            ],
            projects=[
                {
                    "name": "跨境对账平台",
                    "bullets": ["日 300 万流水，差错率 0.02%"],
                }
            ],
            label="good",
            hard_gate_pass=True,
            expected_decision="recommend",
            score_min=87,
            score_max=100,
            match_reasons=[
                "年限最长，FastAPI 迁移与 PG/Redis 生产证据强",
                "有 K8s/gRPC 加分项",
            ],
            penalty_reasons=[],
            quotes=[
                "主导账户与支付对账服务，框架从 Flask 迁到 FastAPI，迁移 80+ 路由。",
                "跨境对账平台，日处理 300 万流水，差错率 0.02%。",
            ],
        ),
        resume(
            resume_id="CV_014",
            raw_text=(
                "姓名：邓林夕\n"
                "求职意向：后端开发\n"
                "教育经历：临安学院 软件工程 本科 2019-2023\n"
                "工作经历：\n"
                "灯塔系统 | 后端开发 | 2023.07-至今（满 3 年口径含实习累计，正式约 3 年）\n"
                "用 FastAPI 写过管理后台 CRUD，Python 为主语言。"
                "PostgreSQL 会写常规 CRUD 与简单索引，未做过慢查询治理或事务拆分。"
                "Redis 只用过缓存验证码，无锁、无限流。"
                "Docker 能在本地 compose 起服务，镜像由运维维护，自己不会多阶段构建。\n"
                "项目：内部审批流 v1，日活低，无性能指标。\n"
                "技能：Python、FastAPI、PostgreSQL、Redis、Docker、Vue\n"
                "刚满年限，深度偏 CRUD。"
            ),
            name="邓林夕",
            years=3,
            education="本科",
            skills=["Python", "FastAPI", "PostgreSQL", "Redis", "Docker", "Vue"],
            experiences=[
                {
                    "company": "灯塔系统",
                    "title": "后端开发",
                    "years": 3.0,
                    "bullets": ["管理后台 CRUD", "验证码缓存"],
                }
            ],
            projects=[
                {
                    "name": "内部审批流 v1",
                    "bullets": ["日活低，无性能指标"],
                }
            ],
            label="partial",
            hard_gate_pass=True,
            expected_decision="gray",
            score_min=63,
            score_max=72,
            match_reasons=["硬门槛字段齐，能独立写 FastAPI CRUD"],
            penalty_reasons=["PG/Redis/Docker 均停留在入门用法", "无高并发或治理证据"],
            quotes=[
                "PostgreSQL 会写常规 CRUD 与简单索引，未做过慢查询治理或事务拆分。",
                "Docker 能在本地 compose 起服务，镜像由运维维护，自己不会多阶段构建。",
            ],
        ),
        resume(
            resume_id="CV_015",
            raw_text=(
                "姓名：许南风\n"
                "求职意向：Python 开发（数据方向转后端）\n"
                "教育经历：瓯江大学 统计学 本科 2016-2020\n"
                "工作经历：\n"
                "南风计算 | 数据开发 | 2020.07-至今\n"
                "4 年以离线任务为主：Python 脚本跑批、SQL 出数。"
                "2025 年起用 FastAPI 包了一层取数 API，供报表前端调用。"
                "库是 PostgreSQL 分析库，少事务、多只读。"
                "Redis 几乎不用；Docker 把 notebook 打成镜像跑批。\n"
                "技能：Python、SQL、FastAPI、PostgreSQL、Redis、Docker、Airflow\n"
                "领域略偏数据开发，后端写路径与缓存经验弱。"
            ),
            name="许南风",
            years=4,
            education="本科",
            skills=["Python", "SQL", "FastAPI", "PostgreSQL", "Redis", "Docker", "Airflow"],
            experiences=[
                {
                    "company": "南风计算",
                    "title": "数据开发",
                    "years": 5.1,
                    "bullets": ["离线跑批", "后补 FastAPI 取数 API"],
                }
            ],
            projects=[
                {
                    "name": "报表取数 API",
                    "bullets": ["只读 PostgreSQL，几乎无 Redis"],
                }
            ],
            label="partial",
            hard_gate_pass=True,
            expected_decision="gray",
            score_min=60,
            score_max=69,
            match_reasons=["年限学历达标，技能列表覆盖", "有 FastAPI 取数 API"],
            penalty_reasons=["主业 ETL/跑批，后端事务与 Redis 弱", "Docker 用于 notebook 而非服务发布"],
            quotes=[
                "4 年以离线任务为主：Python 脚本跑批、SQL 出数。",
                "Redis 几乎不用；Docker 把 notebook 打成镜像跑批。",
            ],
        ),
        resume(
            resume_id="CV_016",
            raw_text=(
                "姓名：沈星野\n"
                "求职意向：后端工程师\n"
                "教育经历：嘉禾大学 计算机 硕士 2018-2021\n"
                "工作经历：\n"
                "石桥科技 | 全栈 | 2021.07-至今（3 年）\n"
                "前后端都做。后端用 FastAPI 提供 BFF，大量逻辑在前端拼装。"
                "PostgreSQL 表结构由前任设计，自己以联调为主，未做过索引评审。"
                "Redis 用过 session；出现过缓存击穿未系统治理。"
                "Docker 文件从模板拷贝，生产发布由云厂商按钮完成。\n"
                "技能：Python、FastAPI、PostgreSQL、Redis、Docker、React、TypeScript\n"
                "能干活但后端基本功偏薄。"
            ),
            name="沈星野",
            years=3,
            education="硕士",
            skills=[
                "Python",
                "FastAPI",
                "PostgreSQL",
                "Redis",
                "Docker",
                "React",
                "TypeScript",
            ],
            experiences=[
                {
                    "company": "石桥科技",
                    "title": "全栈",
                    "years": 3.0,
                    "bullets": ["FastAPI BFF", "前端拼装业务逻辑"],
                }
            ],
            projects=[
                {
                    "name": "商家工作台",
                    "bullets": ["BFF + React，数据库设计非本人"],
                }
            ],
            label="partial",
            hard_gate_pass=True,
            expected_decision="gray",
            score_min=62,
            score_max=72,
            match_reasons=["硕士 3 年，技能字段覆盖必备项"],
            penalty_reasons=["偏全栈 BFF，库表与发布能力弱", "Redis 无系统治理"],
            quotes=[
                "PostgreSQL 表结构由前任设计，自己以联调为主，未做过索引评审。",
                "Docker 文件从模板拷贝，生产发布由云厂商按钮完成。",
            ],
        ),
        resume(
            resume_id="CV_017",
            raw_text=(
                "姓名：蒋一凡\n"
                "求职意向：FastAPI 后端\n"
                "教育经历：甬江大学 计算机 本科 2021-2025\n"
                "工作经历：\n"
                "梧桐软件 | 后端实习生转正 | 2025.03-至今（约 1 年）\n"
                "跟随导师用 Python + FastAPI 写用户模块。"
                "PostgreSQL 改过字段；Redis 存过 token；Dockerfile 按文档复制过。\n"
                "校园项目：二手书交易 API。\n"
                "技能：Python、FastAPI、PostgreSQL、Redis、Docker\n"
                "关键字齐但年限约 1 年，低于 3 年硬门槛。"
            ),
            name="蒋一凡",
            years=1,
            education="本科",
            skills=["Python", "FastAPI", "PostgreSQL", "Redis", "Docker"],
            experiences=[
                {
                    "company": "梧桐软件",
                    "title": "后端初级",
                    "years": 1.0,
                    "bullets": ["用户模块 CRUD", "按文档复制 Dockerfile"],
                }
            ],
            projects=[{"name": "二手书交易 API", "bullets": ["校园项目"]}],
            label="poor",
            hard_gate_pass=False,
            expected_decision="reject",
            score_min=15,
            score_max=42,
            match_reasons=["本科，技能关键字覆盖"],
            penalty_reasons=["年限约 1 年，不满足 min_years=3"],
            quotes=[
                "梧桐软件 | 后端实习生转正 | 2025.03-至今（约 1 年）",
                "关键字齐但年限约 1 年，低于 3 年硬门槛。",
            ],
        ),
        resume(
            resume_id="CV_018",
            raw_text=(
                "姓名：秦小白\n"
                "求职意向：Python 后端\n"
                "教育经历：钱江职业学院 软件技术 大专 2015-2018\n"
                "工作经历：\n"
                "流年信息 | 后端开发 | 2018.07-至今（6 年）\n"
                "长期维护 FastAPI 服务，PostgreSQL 做过索引与真空，Redis 做队列，Docker 生产发布。"
                "带过 2 名初级。\n"
                "技能：Python、FastAPI、PostgreSQL、Redis、Docker、Celery\n"
                "工程经验足够，但学历为大专，不满足本科硬门槛。"
            ),
            name="秦小白",
            years=6,
            education="大专",
            skills=["Python", "FastAPI", "PostgreSQL", "Redis", "Docker", "Celery"],
            experiences=[
                {
                    "company": "流年信息",
                    "title": "后端开发",
                    "years": 7.1,
                    "bullets": ["FastAPI 服务维护", "PG 索引与 Redis 队列"],
                }
            ],
            projects=[{"name": "商户开放平台", "bullets": ["独立负责发布与值班"]}],
            label="poor",
            hard_gate_pass=False,
            expected_decision="reject",
            score_min=20,
            score_max=50,
            match_reasons=["年限与技能匹配岗位"],
            penalty_reasons=["学历大专，低于本科硬门槛"],
            quotes=[
                "教育经历：钱江职业学院 软件技术 大专 2015-2018",
                "学历为大专，不满足本科硬门槛。",
            ],
        ),
        resume(
            resume_id="CV_019",
            raw_text=(
                "姓名：魏弘毅\n"
                "求职意向：高级专家\n"
                "教育经历：海州大学 计算机 本科 2009-2013\n"
                "工作经历：\n"
                "银杏视觉 | 前端技术专家 | 2015.01-至今\n"
                "11 年前端：React/TypeScript 设计系统，带 20 人团队。"
                "精通 Webpack、微前端与性能优化，Lighthouse 到 95。"
                "不会 Python 后端，未使用 FastAPI、PostgreSQL 事务、Redis 或 Docker 服务化。"
                "曾拒绝转后端，认为「接口由别人提供即可」。\n"
                "技能：React、TypeScript、Webpack、微前端、CSS、设计系统\n"
                "跨领域资深：前端专家，与 Python/FastAPI 岗位方向不符。"
            ),
            name="魏弘毅",
            years=11,
            education="本科",
            skills=["React", "TypeScript", "Webpack", "微前端", "CSS", "设计系统"],
            experiences=[
                {
                    "company": "银杏视觉",
                    "title": "前端技术专家",
                    "years": 10.6,
                    "bullets": ["设计系统", "微前端与性能优化"],
                }
            ],
            projects=[{"name": "企业设计系统", "bullets": ["Lighthouse 95，纯前端"]}],
            label="poor",
            hard_gate_pass=False,
            expected_decision="reject",
            score_min=4,
            score_max=32,
            match_reasons=["本科，资深，带队经验"],
            penalty_reasons=["跨领域前端，缺失全部后端必备技能"],
            quotes=[
                "11 年前端：React/TypeScript 设计系统，带 20 人团队。",
                "不会 Python 后端，未使用 FastAPI、PostgreSQL 事务、Redis 或 Docker 服务化。",
            ],
        ),
        resume(
            resume_id="CV_020",
            raw_text=(
                "姓名：陶知夏\n"
                "求职意向：后端开发工程师（Python / FastAPI）\n"
                "教育经历：本科（未写学校）\n"
                "技能：Python FastAPI PostgreSQL Redis Docker Kubernetes Celery gRPC OpenTelemetry AWS 高并发 微服务 中台\n"
                "经历：精通以上所有名词；负责过「亿级」系统，同时又写「在校学习中」。"
                "项目描述复制 JD 原句：基于 FastAPI 设计并实现对外 HTTP API 与内部服务。"
                "无表结构、无接口路径、无事故复盘。"
                "另写「不会写 SQL，数据库都是别人建的」与「精通 PostgreSQL」并列。\n"
                "关键词堆砌且自相矛盾。"
            ),
            name="陶知夏",
            years=4,
            education="本科",
            skills=["Python", "FastAPI", "PostgreSQL", "Redis", "Docker", "Kubernetes", "Celery"],
            experiences=[
                {
                    "company": "未具名",
                    "title": "后端",
                    "years": 4,
                    "bullets": ["复制 JD 原句，无细节"],
                }
            ],
            projects=[{"name": "亿级系统", "bullets": ["无表结构、无接口路径"]}],
            label="poor",
            hard_gate_pass=True,
            expected_decision="reject",
            score_min=0,
            score_max=38,
            match_reasons=["结构化技能与年限看起来齐"],
            penalty_reasons=["关键词堆砌并复制 JD", "精通 PostgreSQL 与不会写 SQL 矛盾"],
            quotes=[
                "另写「不会写 SQL，数据库都是别人建的」与「精通 PostgreSQL」并列。",
                "项目描述复制 JD 原句：基于 FastAPI 设计并实现对外 HTTP API 与内部服务。",
            ],
        ),
    ]


def case_003_resumes():
    return [
        resume(
            resume_id="CV_021",
            raw_text=(
                "姓名：潘远山\n"
                "求职意向：数据工程师 / ETL\n"
                "教育经历：燕山信息大学 计算机 本科 2015-2019\n"
                "工作经历：\n"
                "数川科技 | 数据开发 | 2020.03-至今\n"
                "负责交易域 ODS→DWD→DWS 链路。Spark（PySpark）日批 2.4 小时内跑完 8TB。"
                "Airflow 管理 70+ DAG，对 SLA 超时做分页告警与自动重跑。"
                "SQL 编写多层指标，窗口函数计算留存；Python 做质量探针：空值、主键冲突、环比波动。"
                "数据建模采用星型模型，订单事实表与用户/商品维度分离。\n"
                "观澜数据 | 初级数仓 | 2019.07-2020.02\n"
                "维护 Hive 表到早期 Spark 迁移。\n"
                "技能：SQL、Spark、Airflow、Python、数据建模、Hive、Kafka\n"
                "5 年数仓，与 JD 职责对应。"
            ),
            name="潘远山",
            years=5,
            education="本科",
            skills=["SQL", "Spark", "Airflow", "Python", "数据建模", "Hive", "Kafka"],
            experiences=[
                {
                    "company": "数川科技",
                    "title": "数据开发",
                    "years": 5.4,
                    "bullets": ["PySpark 日批 8TB", "Airflow 70+ DAG"],
                },
                {
                    "company": "观澜数据",
                    "title": "初级数仓",
                    "years": 0.6,
                    "bullets": ["Hive 迁 Spark"],
                },
            ],
            projects=[
                {
                    "name": "交易域数仓",
                    "bullets": ["星型模型", "质量探针覆盖空值/主键/波动"],
                }
            ],
            label="good",
            hard_gate_pass=True,
            expected_decision="recommend",
            score_min=86,
            score_max=100,
            match_reasons=["必备技能均有生产量化证据", "建模与质量体系完整"],
            penalty_reasons=[],
            quotes=[
                "Spark（PySpark）日批 2.4 小时内跑完 8TB。",
                "数据建模采用星型模型，订单事实表与用户/商品维度分离。",
            ],
        ),
        resume(
            resume_id="CV_022",
            raw_text=(
                "姓名：田青禾\n"
                "求职意向：数仓开发\n"
                "教育经历：蓟门大学 信息管理 硕士 2018-2021\n"
                "工作经历：\n"
                "墨白数仓 | 数据工程师 | 2021.07-至今\n"
                "用分布式计算引擎 PySpark（即 Spark 的 Python API）做用户行为宽表。"
                "调度侧编写 Apache Airflow DAG（团队口语称「气流调度」），含传感器等待上游 binlog。"
                "熟悉 SQL 窗口与 CTE；用 Python 写自定义 Operator。"
                "维度建模按 Kimball：一致性维度与缓慢变化维 SCD2。\n"
                "技能：SQL、PySpark、气流调度、Python、Kimball、SCD2、Hive\n"
                "同义改写：PySpark/气流调度/Kimball 对应 Spark/Airflow/数据建模。"
            ),
            name="田青禾",
            years=4,
            education="硕士",
            skills=["SQL", "PySpark", "气流调度", "Python", "Kimball", "SCD2", "Hive"],
            experiences=[
                {
                    "company": "墨白数仓",
                    "title": "数据工程师",
                    "years": 4.1,
                    "bullets": ["PySpark 用户行为宽表", "Airflow DAG + SCD2"],
                }
            ],
            projects=[
                {
                    "name": "用户行为宽表",
                    "bullets": ["Kimball 一致性维度", "自定义 Operator"],
                }
            ],
            label="good",
            hard_gate_pass=True,
            expected_decision="recommend",
            score_min=81,
            score_max=95,
            match_reasons=[
                "同义改写样本，别名应对应必备技能",
                "有 SCD2 与 DAG 传感器等具体实践",
            ],
            penalty_reasons=["Airflow/Spark 英文词未作为技能主键"],
            quotes=[
                "用分布式计算引擎 PySpark（即 Spark 的 Python API）做用户行为宽表。",
                "维度建模按 Kimball：一致性维度与缓慢变化维 SCD2。",
            ],
        ),
        resume(
            resume_id="CV_023",
            raw_text=(
                "姓名：方既明\n"
                "求职意向：数据工程师\n"
                "教育经历：北清河大学 软件工程 本科 2013-2017\n"
                "工作经历：\n"
                "青野智能 | 资深数据开发 | 2017.07-至今\n"
                "8 年数仓。从零搭建广告域分层，Spark 作业峰值 1200 核。"
                "Airflow 迁移自 crontab，引入 SLA 与血缘登记。"
                "SQL 性能调优：广播 join、倾斜打散；Python 做补数框架。"
                "数据建模产出指标字典 200+，事实表按业务过程拆分。\n"
                "技能：SQL、Spark、Airflow、Python、数据建模、Flink、dbt、ClickHouse"
            ),
            name="方既明",
            years=8,
            education="本科",
            skills=["SQL", "Spark", "Airflow", "Python", "数据建模", "Flink", "dbt", "ClickHouse"],
            experiences=[
                {
                    "company": "青野智能",
                    "title": "资深数据开发",
                    "years": 8.1,
                    "bullets": ["广告域从零分层", "Spark 1200 核", "crontab→Airflow"],
                }
            ],
            projects=[
                {
                    "name": "广告指标字典",
                    "bullets": ["200+ 指标", "补数框架"],
                }
            ],
            label="good",
            hard_gate_pass=True,
            expected_decision="recommend",
            score_min=88,
            score_max=100,
            match_reasons=["年限与规模最大，加分项 Flink/dbt 也有"],
            penalty_reasons=[],
            quotes=[
                "从零搭建广告域分层，Spark 作业峰值 1200 核。",
                "数据建模产出指标字典 200+，事实表按业务过程拆分。",
            ],
        ),
        resume(
            resume_id="CV_024",
            raw_text=(
                "姓名：石可岚\n"
                "求职意向：数据开发\n"
                "教育经历：通州学院 计算机 本科 2018-2022\n"
                "工作经历：\n"
                "北岛分析 | 数据分析师兼取数 | 2022.07-至今（3 年）\n"
                "日常以 SQL 取数和看板为主，Python 做 pandas 清洗。"
                "Spark 只用过 SQL 引擎跑临时查询，没有独立调过 shuffle。"
                "Airflow 上改过别人 DAG 的调度时间，未从零编写。"
                "数据建模停留在「宽表堆字段」，未区分事实/维度。\n"
                "技能：SQL、Spark、Airflow、Python、数据建模、Tableau\n"
                "分析师背景，工程化 ETL 偏弱。"
            ),
            name="石可岚",
            years=3,
            education="本科",
            skills=["SQL", "Spark", "Airflow", "Python", "数据建模", "Tableau"],
            experiences=[
                {
                    "company": "北岛分析",
                    "title": "数据分析师兼取数",
                    "years": 3.0,
                    "bullets": ["SQL 取数与看板", "pandas 清洗"],
                }
            ],
            projects=[{"name": "经营看板", "bullets": ["宽表堆字段，无分层"]}],
            label="partial",
            hard_gate_pass=True,
            expected_decision="gray",
            score_min=61,
            score_max=70,
            match_reasons=["年限学历达标，技能字段覆盖"],
            penalty_reasons=["分析师取数为主，Spark/Airflow/建模均浅"],
            quotes=[
                "日常以 SQL 取数和看板为主，Python 做 pandas 清洗。",
                "数据建模停留在「宽表堆字段」，未区分事实/维度。",
            ],
        ),
        resume(
            resume_id="CV_025",
            raw_text=(
                "姓名：崔望舒\n"
                "求职意向：数据工程师\n"
                "教育经历：津门大学 自动化 本科 2016-2020\n"
                "工作经历：\n"
                "长河数据 | 后端转数据 | 2020.07-至今\n"
                "前三年写 Java 接口。2024 后转入数据组。"
                "用 Python 和 SQL 维护几张报表；Spark 作业从模板改分区。"
                "Airflow 会新增任务节点，依赖关系常需别人帮忙改。"
                "数据建模按组长草图建表，说不清拉链与快照差异。\n"
                "技能：SQL、Spark、Airflow、Python、数据建模、Java\n"
                "总年限 4 年但数据岗深度约 1 年+。"
            ),
            name="崔望舒",
            years=4,
            education="本科",
            skills=["SQL", "Spark", "Airflow", "Python", "数据建模", "Java"],
            experiences=[
                {
                    "company": "长河数据",
                    "title": "后端转数据",
                    "years": 5.1,
                    "bullets": ["前三年 Java 接口", "近一年改 Spark 模板"],
                }
            ],
            projects=[{"name": "几张业务报表", "bullets": ["模板改分区"]}],
            label="partial",
            hard_gate_pass=True,
            expected_decision="gray",
            score_min=60,
            score_max=68,
            match_reasons=["总年限与学历达标，技能列表齐"],
            penalty_reasons=["数据岗相关深度短", "建模与 DAG 依赖能力弱"],
            quotes=[
                "前三年写 Java 接口。2024 后转入数据组。",
                "数据建模按组长草图建表，说不清拉链与快照差异。",
            ],
        ),
        resume(
            resume_id="CV_026",
            raw_text=(
                "姓名：姚知秋\n"
                "求职意向：ETL 工程师\n"
                "教育经历：燕园夜校合作办学 软件工程 硕士 2019-2022\n"
                "工作经历：\n"
                "拾穗科技 | ETL 开发 | 2022.07-至今（3 年）\n"
                "主要用 SQL + Python 做 MySQL 到 MySQL 的同步脚本，crontab 调度。"
                "公司计划上 Spark 与 Airflow，本人完成过培训作业和一场 POC，未接管生产。"
                "数据建模按源表 1:1 复制，无分层。\n"
                "技能：SQL、Spark、Airflow、Python、数据建模、MySQL、crontab\n"
                "同步脚本经验真实，分布式调度与建模是预研水平。"
            ),
            name="姚知秋",
            years=3,
            education="硕士",
            skills=["SQL", "Spark", "Airflow", "Python", "数据建模", "MySQL", "crontab"],
            experiences=[
                {
                    "company": "拾穗科技",
                    "title": "ETL 开发",
                    "years": 3.0,
                    "bullets": ["MySQL 同步脚本", "crontab", "Spark/Airflow 仅 POC"],
                }
            ],
            projects=[{"name": "库到库同步", "bullets": ["1:1 复制源表"]}],
            label="partial",
            hard_gate_pass=True,
            expected_decision="gray",
            score_min=62,
            score_max=71,
            match_reasons=["3 年 ETL 脚本经验，技能字段覆盖"],
            penalty_reasons=["Spark/Airflow 非生产", "无真正数据建模分层"],
            quotes=[
                "主要用 SQL + Python 做 MySQL 到 MySQL 的同步脚本，crontab 调度。",
                "公司计划上 Spark 与 Airflow，本人完成过培训作业和一场 POC，未接管生产。",
            ],
        ),
        resume(
            resume_id="CV_027",
            raw_text=(
                "姓名：陆景深\n"
                "求职意向：数据工程师\n"
                "教育经历：昌平大学 计算机 本科 2021-2025\n"
                "工作经历：\n"
                "木犀数据 | 数据开发助理 | 2025.07-至今（约 1 年）\n"
                "协助写 SQL 和改 Airflow 定时；跟做过 Spark 作业日志查看。"
                "Python 写过校验脚本。数据建模只听过分享。\n"
                "技能：SQL、Spark、Airflow、Python、数据建模\n"
                "年限约 1 年，不满足 3 年硬门槛。"
            ),
            name="陆景深",
            years=1,
            education="本科",
            skills=["SQL", "Spark", "Airflow", "Python", "数据建模"],
            experiences=[
                {
                    "company": "木犀数据",
                    "title": "数据开发助理",
                    "years": 1.0,
                    "bullets": ["协助 SQL", "查看 Spark 日志"],
                }
            ],
            projects=[{"name": "跟跑日批", "bullets": ["无独立作业"]}],
            label="poor",
            hard_gate_pass=False,
            expected_decision="reject",
            score_min=16,
            score_max=44,
            match_reasons=["本科，技能关键字在列"],
            penalty_reasons=["年限约 1 年，低于 min_years=3"],
            quotes=[
                "木犀数据 | 数据开发助理 | 2025.07-至今（约 1 年）",
                "年限约 1 年，不满足 3 年硬门槛。",
            ],
        ),
        resume(
            resume_id="CV_028",
            raw_text=(
                "姓名：侯小满\n"
                "求职意向：数仓开发\n"
                "教育经历：通州职业大学 大数据技术 大专 2016-2019\n"
                "工作经历：\n"
                "远帆商业 | 数仓工程师 | 2019.08-至今（5 年+）\n"
                "独立负责零售域 Spark 日批与 Airflow DAG，SQL 调优和 Python 补数都做过。"
                "按星型模型拆过库存事实表。\n"
                "技能：SQL、Spark、Airflow、Python、数据建模、Hive\n"
                "能力接近岗位，但学历为大专，不满足本科硬门槛。"
            ),
            name="侯小满",
            years=5,
            education="大专",
            skills=["SQL", "Spark", "Airflow", "Python", "数据建模", "Hive"],
            experiences=[
                {
                    "company": "远帆商业",
                    "title": "数仓工程师",
                    "years": 6.0,
                    "bullets": ["零售域日批", "星型模型库存事实表"],
                }
            ],
            projects=[{"name": "零售域数仓", "bullets": ["独立 DAG 与补数"]}],
            label="poor",
            hard_gate_pass=False,
            expected_decision="reject",
            score_min=21,
            score_max=49,
            match_reasons=["年限与技能匹配"],
            penalty_reasons=["学历大专，低于本科硬门槛"],
            quotes=[
                "教育经历：通州职业大学 大数据技术 大专 2016-2019",
                "学历为大专，不满足本科硬门槛。",
            ],
        ),
        resume(
            resume_id="CV_029",
            raw_text=(
                "姓名：龚万川\n"
                "求职意向：高级经理\n"
                "教育经历：商海大学 市场营销 本科 2007-2011\n"
                "工作经历：\n"
                "星图报表 | 销售运营总监 | 2014.01-至今\n"
                "12 年销售运营，带 40人团队冲 GMV。"
                "会用 Excel 透视表和看别人做好的看板，不写 SQL 作业，不用 Spark，不编 Airflow，不写 Python。"
                "所谓「数据驱动」是催报表，不是数据工程。\n"
                "技能：销售管理、Excel、CRM、渠道运营、演讲\n"
                "跨领域资深：运营高管，与 ETL/数仓方向不符。"
            ),
            name="龚万川",
            years=12,
            education="本科",
            skills=["销售管理", "Excel", "CRM", "渠道运营", "演讲"],
            experiences=[
                {
                    "company": "星图报表",
                    "title": "销售运营总监",
                    "years": 11.6,
                    "bullets": ["带 40 人冲 GMV", "催报表"],
                }
            ],
            projects=[{"name": "季度战役", "bullets": ["渠道激励，非数仓"]}],
            label="poor",
            hard_gate_pass=False,
            expected_decision="reject",
            score_min=3,
            score_max=28,
            match_reasons=["本科，资历看起来很高"],
            penalty_reasons=["跨领域销售运营，缺失全部数仓必备技能"],
            quotes=[
                "12 年销售运营，带 40人团队冲 GMV。",
                "会用 Excel 透视表和看别人做好的看板，不写 SQL 作业，不用 Spark，不编 Airflow，不写 Python。",
            ],
        ),
        resume(
            resume_id="CV_030",
            raw_text=(
                "姓名：尹不群\n"
                "求职意向：数据工程师（ETL / 数仓）\n"
                "教育经历：本科\n"
                "技能：SQL Spark Airflow Python 数据建模 dbt Flink Hive Kafka ODS DWD DWS ADS Kimball 湖仓一体\n"
                "经历：精通所有数仓名词；「从 0 到 1 建设过万亿级数仓」同时「正在学习 SELECT」。"
                "项目职责整段粘贴 JD：负责业务库到数仓的 ETL，使用 Spark 完成日批与补数作业。"
                "无表名、无分区策略、无失败案例。"
                "另称「Airflow 就是定时任务，我用手机闹钟也能调度」。\n"
                "关键词堆砌、空洞且矛盾。"
            ),
            name="尹不群",
            years=4,
            education="本科",
            skills=["SQL", "Spark", "Airflow", "Python", "数据建模", "dbt", "Flink", "Hive"],
            experiences=[
                {
                    "company": "未具名",
                    "title": "数据专家",
                    "years": 4,
                    "bullets": ["粘贴 JD，无表名"],
                }
            ],
            projects=[{"name": "万亿级数仓", "bullets": ["无分区策略、无失败案例"]}],
            label="poor",
            hard_gate_pass=True,
            expected_decision="reject",
            score_min=0,
            score_max=36,
            match_reasons=["结构化字段齐"],
            penalty_reasons=["关键词堆砌并复制 JD", "万亿级数仓与正在学习 SELECT 矛盾"],
            quotes=[
                "经历：精通所有数仓名词；「从 0 到 1 建设过万亿级数仓」同时「正在学习 SELECT」。",
                "另称「Airflow 就是定时任务，我用手机闹钟也能调度」。",
            ],
        ),
    ]


def build():
    for job in (CASE_001_JOB, CASE_002_JOB, CASE_003_JOB):
        check_job(job)

    cases = [
        {
            "case_id": "CASE_001",
            "job": CASE_001_JOB,
            "resumes": case_001_resumes(),
            "notes": (
                "AI Agent/LLM 应用。反作弊：CV_002 同义改写 good；"
                "CV_010 关键词堆砌；CV_009 跨领域 Java 架构师。"
                "硬门槛失败：CV_007 年限、CV_008 学历、CV_009 缺必备技能。"
            ),
        },
        {
            "case_id": "CASE_002",
            "job": CASE_002_JOB,
            "resumes": case_002_resumes(),
            "notes": (
                "Python/FastAPI 后端。反作弊：CV_012 同义改写 good；"
                "CV_020 关键词堆砌；CV_019 跨领域前端专家。"
                "硬门槛失败：CV_017 年限、CV_018 学历、CV_019 缺必备技能。"
            ),
        },
        {
            "case_id": "CASE_003",
            "job": CASE_003_JOB,
            "resumes": case_003_resumes(),
            "notes": (
                "数据工程师 ETL。反作弊：CV_022 同义改写 good；"
                "CV_030 关键词堆砌；CV_029 跨领域销售运营。"
                "硬门槛失败：CV_027 年限、CV_028 学历、CV_029 缺必备技能。"
            ),
        },
    ]

    for case in cases:
        labels = [r["ground_truth"]["label"] for r in case["resumes"]]
        assert labels.count("good") == 3, case["case_id"]
        assert labels.count("partial") == 3, case["case_id"]
        assert labels.count("poor") == 4, case["case_id"]
        assert len(case["resumes"]) == 10

    return {
        "version": "1.0",
        "locale": "zh-CN",
        "total_cases": 10,
        "total_resumes": 100,
        "batch": {"index": 1, "of": 3, "case_ids": ["CASE_001", "CASE_002", "CASE_003"]},
        "scoring_ref": {
            "formula": "score_total = 0.60 * score_llm + 0.40 * score_deterministic",
            "thresholds": {
                "recommend": 75,
                "gray_zone_min": 60,
                "reject_below": 60,
            },
        },
        "cases": cases,
    }


def main():
    doc = build()
    out = Path(__file__).with_name("part1_case001_003.json")
    out.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {out} bytes={out.stat().st_size}")
    for case in doc["cases"]:
        print(case["case_id"], "job_len", len(case["job"]["raw_text"]))
        for r in case["resumes"]:
            gt = r["ground_truth"]
            print(
                f"  {r['resume_id']} {gt['label']:7} years={r['structured']['years_experience']:>4} "
                f"edu={r['structured']['education']} gate={gt['hard_gate_pass']} "
                f"raw={len(r['raw_text'])}"
            )


if __name__ == "__main__":
    main()
