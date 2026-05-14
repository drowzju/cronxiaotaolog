# Xiaotao WebDAV 同步脚本

从 WebDAV 获取闪记数据，使用 AI 服务转换为或合并到日志，并上传回 WebDAV。

## 功能

1. **从 WebDAV 下载** - 获取指定日期的闪记（jsonl）和日志（md）
2. **AI 处理** - 支持两种模式：
   - 转换模式：闪记不存在日志时，直接转换为日志格式
   - 合并模式：日志已存在时，将闪记内容智能合并到日志中
3. **备份机制** - 覆盖前自动备份到 `/flashnotes/backup/`
4. **本地存储** - 所有下载和处理结果保存到本地

## 目录结构

脚本完全兼容 xiaotao 的目录结构：

```
WebDAV 云端:
/flashnotes/{year}/{year}{month}/{date_dir}/{date}.jsonl           # 闪记
/All/Daily/{year}/{year}{month}/{date_dir}/{date} 星期{x}.md       # 日志
/flashnotes/backup/{year}/{year}{month}/{date_dir}/{date} 星期{x}.md  # 备份

本地存储:
~/xiaotao_data/
├── flashnotes/{year}/{year}{month}/{date_dir}/{date}.jsonl        # 下载的闪记
└── finalnotes/{year}/{year}{month}/{date_dir}/
    └── {date} 星期{x}.md                                          # 最终的日志（上传成功后覆盖）
```

## 安装

```bash
uv pip install -r requirements.txt
```

依赖：`requests`, `python-dotenv`

## 配置

复制 `.env.example` 为 `~/.xiaotao/.env`（或项目目录下的 `.env`）并填写：

```bash
mkdir -p ~/.xiaotao
cp .env.example ~/.xiaotao/.env
```

### 必需配置

```env
WEBDAV_URL=https://dav.jianguoyun.com/dav/
WEBDAV_USERNAME=your_username
WEBDAV_PASSWORD=your_app_password
AI_API_KEY=your_api_key
LOCAL_DATA_DIR=~/xiaotao_data
```

### 可选配置

```env
WEBDAV_FLASHNOTE_DIR=/flashnotes/        # 闪记目录（默认）
WEBDAV_FINALNOTE_DIR=/All/Daily/          # 日志目录（默认）
AI_BASE_URL=https://coding.dashscope.aliyuncs.com/v1
AI_MODEL=kimi-k2.5
```

### 本地目录优先级

1. **环境变量** `LOCAL_DATA_DIR`（最高优先级）
2. **命令行参数** `--local-dir`
3. **默认值** `./xiaotao_data`（最低优先级）

**推荐做法**：在 `.env` 中设置 `LOCAL_DATA_DIR=~/xiaotao_data`

## 使用

### 基本用法

```bash
# 同步今天（数据保存到 ~/xiaotao_data）
uv run python xiaotao_sync.py

# 同步指定日期
uv run python xiaotao_sync.py --date 2026-05-13

# 同步日期范围
uv run python xiaotao_sync.py --from-date 2026-05-01 --to-date 2026-05-13

# 仅预览，不上传
uv run python xiaotao_sync.py --date 2026-05-13 --dry-run

# 临时指定其他目录（覆盖环境变量）
uv run python xiaotao_sync.py --local-dir ./other_data

# 指定日志文件
uv run python xiaotao_sync.py --log-file ./logs/sync.log
```

### 命令行参数

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `--date` | 指定日期 (YYYY-MM-DD) | 今天 |
| `--from-date` | 起始日期 (范围模式) | - |
| `--to-date` | 结束日期 (范围模式) | - |
| `--local-dir` | 本地存储目录（可被环境变量覆盖） | `./xiaotao_data` |
| `--dry-run` | 仅预览，不上传 | - |
| `--test-connection` | 测试 WebDAV 连接 | - |
| `--log-file` | 日志文件路径 | `xiaotao_sync.log` |

## 处理流程

```
[1/5] 从 WebDAV 获取数据
    - 下载闪记 jsonl
    - 下载日志 md（如存在）

[2/5] 保存到本地
    - 闪记 → ~/xiaotao_data/flashnotes/
    - 日志 → ~/xiaotao_data/finalnotes/

[3/5] AI 处理
    - 日志存在 → 合并模式（闪记内容智能合并到日志）
    - 日志不存在 → 转换模式（闪记直接转日志）

[4/5] 保存处理结果到本地
    - 临时保存处理后日志（上传成功后覆盖最终日志）

[5/5] 上传回 WebDAV
    - 如云端已有日志 → 先备份到 /flashnotes/backup/
    - 上传新日志到 /All/Daily/
    - 上传成功 → 用新日志覆盖本地最终日志（只保留最终版本）
```

## 日志

每次执行生成一份日志文件（默认 `xiaotao_sync.log`），记录：
- 执行时间、配置信息
- 文件下载大小
- AI 处理模式和结果长度
- 备份和上传状态
- 成功/失败统计

**注意：** 日志文件每次执行会被覆盖。

## AI 处理规则

基于 xiaotao 的分类规则，闪记内容会被智能归类：

| 分类 | 关键词/特征 |
|------|------------|
| 琐事 | 其他琐碎事项 |
| 我想 | 复杂认知、趋势判断、人生思考 |
| 读书 | 读书、书籍 |
| 沟通 | 开会、会议、与人交流 |
| 健身 | 健身、运动 |
| 浮欢 | 娱乐、放松、吃饭、咖啡、聊天 |
| 研习 | 学习、技术深入、开发、研发 |

## 技术说明

- **WebDAV 客户端**：基于 `requests`，支持 Basic Auth、PROPFIND、MKCOL、PUT、GET
- **AI 服务**：阿里云百炼 Coding Plan API，默认模型 `kimi-k2.5`
- **日志**：Python `logging` 模块，同时输出到控制台和文件
- **配置加载优先级**：`~/.xiaotao/.env` > `./.env` > 环境变量

## 示例输出

```
============================================================
处理日期: 2026-05-13
============================================================

[1/5] 从 WebDAV 获取数据...
  闪记: ✓ (1234 字符)
  日志: ✓ (5678 字符)

[2/5] 保存到本地...
  闪记保存到: ~/xiaotao_data/flashnotes/.../2026-05-13.jsonl
  日志保存到: ~/xiaotao_data/finalnotes/.../2026-05-13 星期二.md

[3/5] AI 处理...
  模式: 合并闪记到现有日志
  处理完成: 7890 字符

[4/5] 保存处理结果到本地...
  保存到: ~/xiaotao_data/finalnotes/.../2026-05-13 星期二_processed.md

[5/5] 上传回 WebDAV...
  备份现有日志到: /flashnotes/backup/.../2026-05-13 星期二.md
  备份成功（本地备份: ~/xiaotao_data/finalnotes/...）
  上传成功: /All/Daily/.../2026-05-13 星期二.md
  本地日志已更新: ~/xiaotao_data/finalnotes/.../2026-05-13 星期二.md

============================================================
处理完成: 2026-05-13
============================================================
```

## 常见问题

### 数据保存在哪里？

默认保存在 `~/xiaotao_data/`（如果设置了 `LOCAL_DATA_DIR=~/xiaotao_data`）。

如果不设置，则按优先级：
1. `LOCAL_DATA_DIR` 环境变量
2. `--local-dir` 参数
3. 当前目录下的 `./xiaotao_data`

### 如何固定数据目录？

在 `~/.xiaotao/.env` 中添加：
```env
LOCAL_DATA_DIR=~/xiaotao_data
```

## 许可证

MIT
