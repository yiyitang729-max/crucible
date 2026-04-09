# Crucible 项目上下文

## 项目是什么

基于 CODE 方法论（Capture-Organize-Distill-Express）的个人知识管理产品，用 AI 把知识沉淀闭环自动化。

**作品集项目**，目标：证明 Tracy 有 AI 产品能力（文档完整度 + 用户故事 + AI Agent 架构理解），用于找 AI 结合方向的 PM 岗位。

## 用户是谁

Tracy 本人：商业化广告 PM，已离职，最大痛点是「只输入不输出」——完美主义导致有想法但不敢整理，内容收藏了从不回看，越积越焦虑。

## 产品定位

一个 AI 思考伙伴，让移动端碎片输入不消失——通过对话式追问变深，自动沉淀为 Obsidian 里的结构化知识，最终能产出输出。

**核心差异**：市场上所有工具解决 Capture + Organize（存进去、找得到），没有工具解决 **Distill**（帮你把模糊感受变成清晰认知）。这是产品的位置。

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
- **V3**：Express 草稿生成（闭合 CODE 循环）

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

## 遗留问题（不影响开发启动）

- [ ] 产品名称：Crucible 是否最终确定？
- [ ] Obsidian Vault 路径（用户本地路径，开发时用环境变量 `OBSIDIAN_VAULT_PATH` 配置）
- [ ] Express 阶段（V3）交互设计细节，待 V1 验收后讨论
