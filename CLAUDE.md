# Crucible 项目上下文

## 项目是什么

基于 CODE 方法论（Capture-Organize-Distill-Express）的个人知识管理产品，用 AI 把知识沉淀闭环自动化。

**作品集项目**，目标：证明 Tracy 有 AI 产品能力（文档完整度 + 用户故事 + AI Agent 架构理解），用于找 AI 结合方向的 PM 岗位。

## 用户是谁

Tracy 本人：商业化广告 PM，已离职，最大痛点是「只输入不输出」——完美主义导致有想法但不敢整理，内容收藏了从不回看，越积越焦虑。

## 产品定位

一个 AI 思考伙伴，让移动端碎片输入不消失——通过对话式追问变深，自动沉淀为 Obsidian 里的结构化知识，最终能产出输出。

**核心差异**：市场上所有工具解决 Capture + Organize（存进去、找得到），没有工具解决 **Distill**（帮你把模糊感受变成清晰认知）。这是产品的位置。

**定位对标**（v0.4）：Karpathy LLM Wiki 代表的是"知识编译"——用户已有完整文档，LLM 负责整理索引。Crucible 做的是"知识蒸馏"——用户只有模糊感受，LLM 通过追问催化思考。编译需要原材料已存在；蒸馏创造原材料本身。

## 关键架构决策（V1）

| 层 | 方案 | 备注 |
|----|------|------|
| 入口 | 飞书 Bot | WebSocket，无需公网 URL |
| Agent 框架 | OpenClaw | 消息路由 + Agent 运行时；**不内置向量检索** |
| 核心 AI | Claude Sonnet（追问）+ Haiku（路由） | 自研 Socratic Agent |
| 沉淀端 | Obsidian 本地库 | 直接写文件系统 |
| 评估存储 | SQLite（crucible.db） | 与 Obsidian 同目录 |
| 向量库 | LanceDB 本地 | **V2 新增**，V1 不需要 |

CODE 四阶段在后台运作，用户只感知「在和 AI 对话」。

## 版本规划

- **V1 MVP**：Capture → Distill → Organize（飞书接收 → Socratic 追问 → 写入 Obsidian + 评估入库）
- **V2**：收藏加工器 + 标签关联 + 收藏唤起（标签系统取代向量库）
- **V3**：Distill Engine 深层蒸馏（知识成熟度 + 碎片聚合 + 认知挑战 + 知识库 Lint）
- **V4**：Express 输出闭环（成熟触发 + 草稿生成 + 输出归档反哺知识库，闭合 CODE 循环）

## 当前阶段

- [x] 需求前讨论（2026-03-31）→ `需求前讨论记录.md`
- [x] 脑暴记录（2026-04-02）→ `脑暴记录_2026-04-02.md`
- [x] 正式设计文档 v0.2（2026-04-02）→ `设计文档_2026-04-02.md`
- [x] 实现计划
- [x] V1 开发执行
- [ ] V2 开发执行 ← **当前位置**

## V1 实现计划（已完成）

- [x] **Plan 1**：基础设施 + 飞书消息跑通
- [x] **Plan 2**：Router Agent + Socratic Agent
- [x] **Plan 3**：Writer Agent + Obsidian 写入
- [x] **Plan 4**：crucible.db 评估入库 + /stats 统计指令

## V2 实现计划

- [x] **Plan 5**：标签系统 + 笔记关联基础（TAG_INDEX + Obsidian 双链）
- [x] **Plan 6**：收藏入库（bookmark 路由 + pending_digests）
- [x] **Plan 7**：收藏加工引擎（DIGEST_REPORT + /pending + /digest + HEARTBEAT 主动提醒）
- [x] **Plan 8**：链接内容抓取（web_fetch + browser fallback）

## 开发规范

1. **每次修改都必须 commit + push**，并更新 CLAUDE.md 的进度记录
2. **每个模块必须打详细日志**，项目根目录下有 `logs/` 文件夹，所有日志统一放在这里
3. **每次修改必须建对应的 test case**（单元测试 + 集成测试）
4. **核心技术架构必须由 Tracy 本人掌握**——每次开发涉及核心架构的部分，单独提炼出来给 Tracy 做详细讲解，由 Tracy 审查确认后再推进
5. **整个项目共用一个 repo**
6. **使用 worktree 开多个分支**，每个分支代表一种技术方案，分支间不要互相依赖
7. **每次修改必须 commit + push 到对应 worktree 的分支上**

## 测试覆盖（test_sync_notes.py，共 45 个测试）

### 单元测试（不依赖网络）

| 模块 | 测试内容 | 对应测评用例 |
|------|---------|------------|
| 消息解析 | text/post/image 消息提取、异常 JSON 处理 | — |
| 笔记识别 | `is_crucible_note` 判断、用户/Bot 消息判断 | — |
| 笔记解析 | 日期/洞察/触发/场景/标签提取、frontmatter 格式、文件名格式 | 用例 1、3 |
| 对话分段 | 30 分钟间隔切分、空消息跳过、错误消息跳过 | — |
| 对话分析 | 完成状态、应用场景、输入类型（article/thought）、轮数统计 | 用例 5、13 |
| URL 提取 | 链接提取、fallback 识别、URL+想法场景、纯文字无 URL | Plan 8 |

### 集成测试（内存 SQLite）

| 模块 | 测试内容 | 对应测评用例 |
|------|---------|------------|
| 对话指标 | 写入、重复写入覆盖 | 用例 11 |
| 标签系统 | 标签写入、标签关联（重合≥2）、无标签处理 | 用例 12 |
| TAG_INDEX | 索引构建、多笔记聚合 | 用例 12 |
| 收藏消化 | 报告生成、空报告处理、status 过滤 | 用例 9 |
| 同步状态 | 默认值、保存/读取 | 用例 11 |

运行命令：`python3 -m unittest test_sync_notes -v`

## 遗留问题（不影响开发启动）

- [ ] 产品名称：Crucible 是否最终确定？
- [ ] Obsidian Vault 路径（用户本地路径，开发时用环境变量 `OBSIDIAN_VAULT_PATH` 配置）
- [ ] Express 阶段（V3）交互设计细节，待 V1 验收后讨论
