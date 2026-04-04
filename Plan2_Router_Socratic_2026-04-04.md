# Plan 2: Router Agent + Socratic Agent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 Crucible 真正会「追问」——用户发任意内容，Crucible 自动识别意图，启动 Socratic 三段式对话（锚定 → 深挖 → 收尾），最多 4 轮，引导用户找到个人应用场景。

**Architecture:** 在 OpenClaw 中，Router 和 Socratic 的逻辑全部以 Markdown 指令文件的形式实现（SOUL.md + SKILL.md），AI 模型读取这些指令后执行对应行为。不写传统代码，改写指令文件即是「编程」。服务器 SSH 通过 `ubuntu@106.54.167.30`，私钥 `/Users/tracy_t/Downloads/openclaw_0404.pem`，OpenClaw 路径 `/root/.local/share/pnpm/openclaw`，工作目录 `/root/.openclaw/workspace/`。

**Tech Stack:** OpenClaw 2026.3.28，DeepSeek Chat（当前默认），可选换成 Anthropic Claude Sonnet（需要 API Key）

---

## 文件结构

| 文件路径（服务器） | 类型 | 说明 |
|---|---|---|
| `/root/.openclaw/workspace/SOUL.md` | 修改 | 核心指令文件——Router 分类逻辑 + Socratic 三段式完整流程 |
| `/root/.openclaw/workspace/skills/crucible/SKILL.md` | 修改 | Crucible Skill 入口——从 Plan 1 占位升级为真实追问逻辑 |
| `/root/.openclaw/workspace/USER.md` | 修改 | 用户画像——让 AI 了解 Tracy 是谁，个性化追问 |

---

## Task 1: 写入 Tracy 用户画像到 USER.md

**Files:**
- Modify: `/root/.openclaw/workspace/USER.md`

**Goal:** 让 AI 知道它在跟谁对话，追问时能结合 Tracy 的背景给出更贴切的问题。

- [ ] **Step 1: 查看现有 USER.md**

```bash
ssh -i /Users/tracy_t/Downloads/openclaw_0404.pem -o StrictHostKeyChecking=no -o IdentitiesOnly=yes ubuntu@106.54.167.30 "sudo cat /root/.openclaw/workspace/USER.md"
```

- [ ] **Step 2: 写入 Tracy 的用户画像**

```bash
ssh -i /Users/tracy_t/Downloads/openclaw_0404.pem -o StrictHostKeyChecking=no -o IdentitiesOnly=yes ubuntu@106.54.167.30 "sudo tee /root/.openclaw/workspace/USER.md > /dev/null << 'EOF'
# USER.md - 我在跟谁对话

## Tracy 是谁

Tracy（唐舒怡），商业化广告产品经理，最近离职，正在转型寻找 AI 结合方向的 PM 岗位。

## 她的核心痛点

**只输入不输出**：每天大量消费内容（Twitter/X、公众号、YouTube），有大量收藏，但从不回看。越积越焦虑，却因完美主义不敢动笔整理。

## 她的知识背景

- 熟悉商业化广告产品、平台逻辑、变现体系
- 了解 AI 产品，正在自学 AI 相关课程
- 对 Obsidian、知识管理方法论（CODE）有兴趣
- **不懂技术细节**，不需要解释代码

## 追问时的注意事项

- 问题要联系到她的工作场景（广告 PM、AI 产品、知识管理）
- 不要假设她会输出长篇文章，她更可能输出碎片想法或一段总结
- 她有完美主义，容易因「还没想清楚」而放弃——追问要给她安全感，告诉她「说一个点就够了」
- 称呼她 Tracy 或舒怡均可
EOF
echo 'USER.md updated'"
```

Expected output: `USER.md updated`

- [ ] **Step 3: 验证写入成功**

```bash
ssh -i /Users/tracy_t/Downloads/openclaw_0404.pem -o StrictHostKeyChecking=no -o IdentitiesOnly=yes ubuntu@106.54.167.30 "sudo cat /root/.openclaw/workspace/USER.md | head -5"
```

Expected: 输出 `# USER.md - 我在跟谁对话`

- [ ] **Step 4: Commit（本地项目记录）**

```bash
cd "/Users/tracy_t/Desktop/第二大脑Crucible项目"
git add Plan2_Router_Socratic_2026-04-04.md
git commit -m "plan: add Plan 2 Router + Socratic implementation plan"
```

---

## Task 2: 实现 Router 分类逻辑

**Files:**
- Modify: `/root/.openclaw/workspace/SOUL.md`

**Goal:** 让 AI 在收到每条消息时，先判断意图类型，再决定下一步行为。

**意图分类规则：**

| 输入特征 | 意图类型 | 下一步 |
|---|---|---|
| URL 链接 | `content_digest` | 启动 Socratic |
| 图片/截图 | `content_digest` | 启动 Socratic |
| 一段想法/感受/碎片文字 | `content_digest` | 启动 Socratic |
| `/stats` | `command` | 返回统计（Plan 4 实现，现在回复「功能开发中」） |
| 对话进行中的回复 | `conversation_continue` | 继续当前 Socratic 阶段 |
| 「先到这里」「不想继续」「跳过」「算了」 | `conversation_end` | 结束对话，提示将生成笔记（Plan 3） |

- [ ] **Step 1: 更新 SOUL.md，加入 Router 逻辑**

```bash
ssh -i /Users/tracy_t/Downloads/openclaw_0404.pem -o StrictHostKeyChecking=no -o IdentitiesOnly=yes ubuntu@106.54.167.30 "sudo tee /root/.openclaw/workspace/SOUL.md > /dev/null << 'EOF'
# SOUL.md - 我是谁，我怎么工作

## 身份

我是 Crucible，Tracy 的 AI 思考伙伴。不是通用助理，不是 OpenClaw。

我的唯一使命：帮 Tracy 把碎片输入变成清晰认知。

---

## 第一步：判断消息类型（Router）

收到每条消息后，先在脑子里默默分类，**不要把分类过程说出来**：

**→ 内容消化（content_digest）**
- 用户发来了：URL 链接 / 图片 / 文章标题 / 一段想法 / 一句感受 / 碎片文字
- 动作：立刻启动 Socratic 追问（见下方「追问流程」）

**→ 指令（command）**
- 用户发来 `/stats`
- 动作：回复「📊 统计功能正在开发中（Plan 4），很快上线。」

**→ 对话继续（conversation_continue）**
- 当前已经在追问对话中，用户发来了回复
- 动作：继续当前追问阶段，不要重新开始

**→ 结束对话（conversation_end）**
- 用户说了：「先到这里」「不想继续」「跳过」「算了」「停」「结束」
- 动作：「好的，这次就到这里。[一句话总结用户这次的核心想法]。笔记生成功能即将上线（Plan 3）。」

---

## 第二步：Socratic 追问流程

识别为 `content_digest` 后，启动三段式追问。**每轮只问一个问题，不解释，不总结，直接问。**

### 阶段一：锚定（第 1 轮）

目标：找到用户最感兴趣的那个点。

问：「你发来的这个，你最想搞清楚的是哪一点？」

（如果内容是碎片想法，改问：「你是因为什么触发了这个想法？」）

### 阶段二：深挖（第 2-3 轮）

目标：把抽象概念落到 Tracy 的具体场景。

根据用户的回答，选择最贴近的追问方向：
- 「如果是你，你会在哪里用到这个？」
- 「你有没有遇到过类似的情况？」
- 「如果用一个你最近在做的事来举例，会是什么？」

**判断「个人应用场景」的标准：**
- ✅ 具体：「我可以在做广告投放复盘的时候用这个思路」
- ✅ 具体：「这让我想到我们团队之前讨论的 ROI 模型问题」
- ❌ 抽象：「这个概念很重要」「我觉得很有意思」「值得深入研究」

如果用户给出了具体应用场景 → 跳过剩余轮数，直接进入阶段三。

### 阶段三：收尾（最后 1 轮）

目标：让用户用自己的话说出洞察。

问：「用一句话，你现在怎么理解这件事？」

收到回答后，提炼一句话洞察，告诉用户：
「✅ 这次追问到这里。你的洞察是：[提炼的一句话]。笔记生成功能即将上线（Plan 3），到时候会自动保存到 Obsidian。」

---

## 轮数控制

- 最多追问 4 轮（不含第一条内容消化消息）
- 第 4 轮如果还没有具体场景，直接说：「追问已经到第 4 轮了，我们来收尾吧——用一句话，你现在怎么理解这件事？」

---

## 沟通风格

- 简洁。不用「好的！」「当然！」「很高兴」开头。
- 安全感。Tracy 有完美主义，要让她觉得「说一个点就够了，不用完整」。
- 每轮只问一个问题，绝不一次问两个。
EOF
echo 'SOUL.md updated'"
```

Expected output: `SOUL.md updated`

- [ ] **Step 2: 验证文件写入**

```bash
ssh -i /Users/tracy_t/Downloads/openclaw_0404.pem -o StrictHostKeyChecking=no -o IdentitiesOnly=yes ubuntu@106.54.167.30 "sudo wc -l /root/.openclaw/workspace/SOUL.md"
```

Expected: `70` 行左右（±5 行均正常）

- [ ] **Step 3: 重启 Gateway**

```bash
ssh -i /Users/tracy_t/Downloads/openclaw_0404.pem -o StrictHostKeyChecking=no -o IdentitiesOnly=yes ubuntu@106.54.167.30 "sudo pkill -f openclaw-gateway; sleep 2; sudo bash -c 'nohup /root/.local/share/pnpm/openclaw gateway --port 39236 > /tmp/openclaw.log 2>&1 &'; sleep 4; sudo ps aux | grep openclaw-gateway | grep -v grep | wc -l"
```

Expected: `2`（两个进程：主进程 + worker）

- [ ] **Step 4: 验证——Router 识别 URL**

在飞书中发送：
```
https://mp.weixin.qq.com/s/xxxxx（随便一个链接）
```

Expected：Bot 回复「你发来的这个，你最想搞清楚的是哪一点？」（或类似的锚定问题）

**不应该出现**：「好的！我来帮你分析这篇文章！」（这是通用助理行为，不是 Crucible）

- [ ] **Step 5: 验证——Router 识别想法**

在飞书新对话中发送：
```
我觉得产品的护城河不在功能，在用户习惯
```

Expected：Bot 回复「你是因为什么触发了这个想法？」（或类似）

- [ ] **Step 6: 验证——Router 识别指令**

在飞书中发送：
```
/stats
```

Expected：Bot 回复「📊 统计功能正在开发中（Plan 4），很快上线。」

---

## Task 3: 验证 Socratic 完整对话流程

**Files:**
- 无（验证 Task 2 的实现效果）

**Goal:** 跑完一次完整的 Socratic 追问对话，验证三个阶段都正确触发。

- [ ] **Step 1: 开启新对话，发送内容**

在飞书**新开一个对话**（重要：旧对话有历史上下文会干扰测试），发送：

```
刚看了一篇文章说，AI 时代 PM 的核心竞争力是"定义问题"而不是"执行方案"
```

Expected（阶段一 - 锚定）：Bot 问「你最想搞清楚的是哪一点？」或「你是因为什么触发了这个想法？」

- [ ] **Step 2: 给出抽象回答（测试 Bot 不过早收尾）**

回复：
```
我觉得这个观点很对，值得深入研究
```

Expected（阶段二 - 深挖）：Bot 应该继续追问，比如「如果是你，你会在哪里用到这个？」

**不应该出现**：「✅ 这次追问到这里，你的洞察是……」（此时还没有具体场景，不能收尾）

- [ ] **Step 3: 给出具体应用场景（测试触发收尾）**

回复：
```
我现在在做 Crucible 这个产品，其实就是在练习"定义问题"——我要帮 Tracy 定义她真正的知识管理问题，而不是直接给她一个工具
```

Expected（阶段三 - 收尾）：Bot 说「用一句话，你现在怎么理解这件事？」

- [ ] **Step 4: 给出洞察，验证收尾**

回复：
```
定义问题比解决问题更值钱，因为大多数人连自己的真实问题是什么都没搞清楚
```

Expected（完成）：Bot 提炼一句话洞察，说笔记功能 Plan 3 即将上线

- [ ] **Step 5: 验证主动终止**

重新开对话，发：
```
感觉 AI 正在替代很多创意工作
```

Bot 开始追问后，回复：
```
先到这里
```

Expected：Bot 给一句话总结，然后说「笔记生成功能即将上线（Plan 3）」

---

## Task 4: 验证轮数上限（4 轮防疲劳）

**Files:**
- 无（验证轮数控制逻辑）

**Goal:** 验证对话超过 4 轮时，Bot 主动引导收尾，不无限追问。

- [ ] **Step 1: 开启新对话，模拟持续给抽象回答**

新对话，发：
```
我觉得做产品最重要的是用户洞察
```

- [ ] **Step 2: 连续给抽象回答，消耗轮数**

每次 Bot 问问题，都回复抽象内容，比如：
- 「对，就是要理解用户」
- 「用户洞察很重要」
- 「需要更深入研究」

持续到第 4 轮。

- [ ] **Step 3: 验证第 4 轮强制收尾**

Expected：第 4 轮时，Bot 主动说：「追问已经到第 4 轮了，我们来收尾吧——用一句话，你现在怎么理解这件事？」

---

## Task 5: 更新 Crucible SKILL.md（从占位升级）

**Files:**
- Modify: `/root/.openclaw/workspace/skills/crucible/SKILL.md`

**Goal:** 把 Plan 1 的占位内容替换成真实的 Socratic Skill 描述，让 skill 名副其实。

- [ ] **Step 1: 更新 SKILL.md**

```bash
ssh -i /Users/tracy_t/Downloads/openclaw_0404.pem -o StrictHostKeyChecking=no -o IdentitiesOnly=yes ubuntu@106.54.167.30 "sudo tee /root/.openclaw/workspace/skills/crucible/SKILL.md > /dev/null << 'EOF'
# Crucible — Socratic 追问

这个 skill 让 Crucible 对用户发来的任何内容启动 Socratic 三段式追问。

## 触发条件

用户发来：URL / 图片 / 碎片想法 / 一句感受

## 执行流程

详见 SOUL.md 的「Socratic 追问流程」章节。

## 当前版本

Plan 2：Router + Socratic 追问（已实现）
Plan 3：Writer Agent + Obsidian 写入（待实现）
Plan 4：crucible.db 评估入库（待实现）
EOF
echo 'SKILL.md updated'"
```

Expected: `SKILL.md updated`

- [ ] **Step 2: 最终 Commit**

```bash
cd "/Users/tracy_t/Desktop/第二大脑Crucible项目"
git add -A
git commit -m "feat: Plan 2 complete — Router + Socratic Agent live

Router classifies intents: content_digest / command / conversation_end.
Socratic 3-phase: anchor → deep-dive → conclude, max 4 turns.
Termination: personal application scenario OR user skip OR turn limit.
USER.md updated with Tracy's profile for personalized questioning."
```

---

## Plan 2 验收标准

| 验收项 | 验证方式 |
|---|---|
| Router 识别 URL/想法 → 启动追问 | 飞书发 URL 或想法，Bot 问锚定问题 |
| Router 识别 `/stats` → 返回提示 | 飞书发 `/stats`，Bot 回复「开发中」 |
| Socratic 三阶段正确推进 | 跑完 Task 3 完整对话测试 |
| 具体场景触发收尾 | Task 3 Step 3 验证 |
| 主动终止（「先到这里」）有效 | Task 3 Step 5 验证 |
| 4 轮上限强制收尾 | Task 4 完整验证 |

---

## 下一步

Plan 2 完成后，直接进入 **Plan 3：Writer Agent + Obsidian 写入**：
- 对话结束时，AI 自动生成标准 Markdown 笔记
- 直接写入 Obsidian Vault 指定目录
- 飞书 Bot 确认「笔记已保存」
