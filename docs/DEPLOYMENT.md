# GitHub + Vercel 部署

本项目包含静态前端、FastAPI Worker 和 Supabase，推荐使用 GitHub 保存代码、Vercel 发布应用、Supabase 托管数据与文件。

## 1. 推送代码

确认以下文件不会进入 Git：

- `backend/.env`
- `.env.local`
- `supabase-config.js`
- `.vercel/`

推送当前分支后，可以在 GitHub 创建 Pull Request 合并到 `main`。Vercel 连接仓库后，每次推送都会创建预览部署，合并到生产分支后自动发布。

## 2. 创建 Vercel 项目

1. 在 Vercel 选择 **Add New → Project**。
2. 导入 GitHub 仓库 `mengtian-7001/Resume_Agent`。
3. Framework Preset 选择 **Other**。
4. Root Directory 保持仓库根目录。
5. Build Command 使用仓库中的 `npm run build`；Output Directory 留空。

## 3. 配置环境变量

在 Vercel Project Settings → Environment Variables 添加：

| 名称 | 必需 | 用途 |
| --- | --- | --- |
| `SUPABASE_URL` | 是 | Supabase Project URL |
| `SUPABASE_PUBLISHABLE_KEY` | 是 | 浏览器可用的 publishable/anon key；也兼容 `SUPABASE_ANON_KEY` |
| `SUPABASE_WORKSPACE_ID` | 是 | 生产工作区 UUID |
| `SUPABASE_SERVICE_ROLE_KEY` | 是 | Worker 服务端访问；绝不能放入前端配置 |
| `INTERNAL_API_TOKEN` | 是 | 内部任务接口的长随机令牌 |
| `AGENT_MODE` | 是 | 首次部署可设 `mock`；真实模型设 `openai` |
| `ALLOW_ANONYMOUS_BOOTSTRAP` | 是 | 生产固定为 `false` |
| `OPENAI_BASE_URL` | 条件必需 | `AGENT_MODE=openai` 时填写 |
| `OPENAI_API_KEY` | 条件必需 | `AGENT_MODE=openai` 时填写 |
| `OPENAI_MODEL` | 可选 | 默认 `gpt-4o-mini` |

前三个公开变量只会在构建时写入浏览器配置。`SUPABASE_SERVICE_ROLE_KEY` 和 `INTERNAL_API_TOKEN` 只由服务端读取。

如果不填写前三个公开变量，应用仍可部署，但会进入静态演示模式。如果只填写其中一部分，构建会主动失败，避免产生半配置状态。

## 4. Supabase 生产设置

1. 按文件名顺序执行 `supabase/migrations/` 中的迁移。
2. 创建生产工作区、正式用户及 `workspace_members` 关系。
3. 保持 `allow_anonymous_bootstrap=false`。
4. 在 Authentication 的 URL Configuration 中加入 Vercel 生产域名和需要使用的预览域名。
5. 确认 `screening-documents` Bucket 为私有，并保留迁移中配置的 RLS。

## 5. 发布后检查

依次验证：

1. 首页可以打开，静态资源无 404。
2. `/api/health` 返回 `status: ok`。
3. 正式账号可以登录。
4. 可以创建任务、上传一份 JD 和一份简历。
5. 点击“一键解析”后任务最终进入 `completed`，结果和证据可以打开。
6. 未登录用户无法读取其他工作区数据。

环境变量修改只会应用到新的部署；修改后需要重新部署。
