# 觅才 · 简历筛选工作台

前端展示层配合 Supabase 托管数据库、私有文件存储和登录鉴权；Python Worker 负责文档解析与规则匹配。

## 架构

```text
浏览器 → Supabase Auth / PostgreSQL / Private Storage
                    ↑
              FastAPI Worker
```

浏览器只使用 Supabase `anon` key。`service_role` key 仅供 FastAPI Worker 使用，不能写入 `supabase-config.js` 或提交到仓库。

## 配置 Supabase

1. 创建一个 Supabase 项目，并按文件名顺序在 SQL Editor 执行 `supabase/migrations/` 下的全部 SQL。
2. 在 Authentication 中启用 Email 登录，创建至少一个用户。
3. 为该用户创建工作区和成员关系（把两个 UUID 替换为实际值）：

```sql
insert into public.workspaces (id, name)
values ('<workspace-uuid>', '默认工作区');

insert into public.workspace_members (workspace_id, user_id, role)
values ('<workspace-uuid>', '<auth-user-uuid>', 'owner');
```

4. 把 `supabase-config.example.js` 的内容复制到 `supabase-config.js`，填写 Project URL、anon key 与工作区 UUID。
5. 用静态服务器启动项目：

```bash
python3 -m http.server 4173
```

前端会在已有 Supabase 登录会话时启用真实文件上传；未配置时自动保留演示数据。

## 启动 Worker

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
uvicorn app.main:app --reload --port 8000
```

配置 `.env` 中的 `SUPABASE_SERVICE_ROLE_KEY` 与 `INTERNAL_API_TOKEN`。Worker 只暴露：

```text
GET  /health
POST /internal/tasks/run-once
```

使用部署平台的定时任务或队列消费者调用 `POST /internal/tasks/run-once`，并携带 `X-Internal-Token`。每次调用会原子领取一个队列任务。

建议每分钟调用一次，或在任务高峰期以单消费者循环调用；不要让多个定时任务在同一秒重复触发，以免浪费 Worker 实例。

## Agent 模式与知识服务

默认 `AGENT_MODE=mock`，可在**不配置模型、Neo4j 或联网 Key**的情况下跑完整链路：

```text
结构化抽取 → Fact Claim → 混合评分 → 题目/追问 → Checker → 可信记忆
```

当前 mock Agent 是可测试的确定性替身，输出与后续真实模型一致的结构化 Contract。生产环境可逐步配置：

- `OPENAI_BASE_URL` / `OPENAI_API_KEY`：OpenAI 兼容模型接口；
- `NEO4J_URI` / `NEO4J_USER` / `NEO4J_PASSWORD`：Fact Graph 多跳关系；
- `TAVILY_API_KEY`：JD Research ReAct 的受控联网检索。

Supabase 继续保存原文件、权限、审计和 Agent 状态；Neo4j 保存 Claim / Evidence / Requirement 关系；`agent_memory_chunks` 预留 pgvector 语义记忆。只有 Checker 通过的内容会标记为可信记忆。

## 数据与安全边界

- JD 和简历保存在私有 `screening-documents` Bucket，访问经由 RLS 与签名 URL。
- 单文件只接受 PDF/DOCX，最大 10MB；服务端再次校验 MIME 与可解析文本。
- 表按 `workspace_id` 启用 RLS，浏览器不能跨工作区读取候选人数据。
- `audit_logs` 只保存操作元数据，不保存简历正文或模型提示词。
- 当前 SQL 包含任务状态、三次重试上限和失败状态。生产环境应设置定期清理任务，按组织的数据保留策略删除文件及关联数据。

例如可由 Supabase Cron 每天调用一次：

```sql
select public.purge_expired_screenings(30);
```

## 当前 Worker 能力

当前 Worker 在 mock 模式下使用确定性 Agent Contract：抽取岗位技能、年限、学历，提出带 Evidence 的 Claim，计算可解释的混合分数，生成题目/追问，并由 Checker 校验。真实 LLM、Neo4j 和 Tavily 未配置时会安全降级，不阻断本地开发。
