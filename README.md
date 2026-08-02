# 代码实时上传器（稳定性增强版）

定时扫描项目目录，自动清理编译中间产物并同步到 GitHub 仓库。

## 功能

- **定时扫描**：每 12 小时（可配置）扫描一次指定目录
- **磁盘清理**：自动删除编译中间产物（.obj/.lst/.m51/.lnp/.htm 等），保留源码和工程文件
- **版本库清理**：移除被 .gitignore 忽略但仍被 git 跟踪的文件
- **自动同步**：git add → commit → push，有变更才提交
- **仓库重命名自动识别**：当 GitHub 仓库被重命名时，自动通过 API 检测 301 重定向并更新 remote URL
- **Git 路径自动检测**：不依赖 PATH 环境变量，自动搜索常见安装路径和注册表
- **网络检查**：push 前检测网络连通性，避免无意义的重试
- **单实例锁**：防止多个实例同时运行造成冲突
- **状态持久化**：记录同步历史、失败次数、remote URL 变更记录
- **配置校验**：启动时验证所有配置项，无效项自动回退到默认值
- **开机自启**：通过 VBS 脚本无窗口后台运行
- **零依赖**：纯 Python 标准库实现

## 文件说明

| 文件 | 作用 |
|------|------|
| `autosync.py` | 主程序：定时扫描 + 磁盘清理 + git 同步（增强版） |
| `config.json` | 配置文件：扫描目录、间隔、清理规则、增强选项 |
| `start.vbs` | 无窗口后台启动器 |

## 快速开始

### 1. 前置条件

- Python 3.8+
- Git 已安装（程序会自动检测路径，无需手动配置 PATH）
- 目标目录已初始化为 git 仓库并配置了 remote origin
- GitHub 凭据已存储（`git config --global credential.helper store`）

### 2. 配置

编辑 `config.json`，将 `watch_dir` 改为你的项目目录路径：

```json
{
  "watch_dir": "C:\\Users\\YourName\\YourProject",
  "interval_hours": 12
}
```

### 3. 启动

```bash
# 前台运行（调试用）
python autosync.py

# 后台无窗口运行（日常用）
wscript start.vbs
```

### 4. 开机自启

将 `start.vbs` 复制到启动目录：

```powershell
Copy-Item start.vbs "$env:APPDATA\Microsoft\Windows\Start Menu\Programs\Startup\"
```

## 配置项详解

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `watch_dir` | string | - | 监听的项目目录（必须是 git 仓库） |
| `interval_hours` | int | 12 | 扫描间隔（小时） |
| `push_retry` | int | 3 | push 失败重试次数 |
| `push_retry_delay` | int | 5 | 重试间隔（秒，递增） |
| `log_dir` | string | `logs` | 日志目录 |
| `github_user` | string | `""` | GitHub 用户名（可选，用于仓库重命名检测） |
| `auto_update_remote` | bool | `true` | 仓库重命名后自动更新 remote URL |
| `network_check` | bool | `true` | push 前检查网络连通性 |
| `network_timeout` | int | 10 | 网络检查超时（秒） |
| `clean_extensions` | array | - | 要删除的文件扩展名 |
| `clean_filenames` | array | - | 要删除的文件名 |
| `clean_dirs` | array | - | 要清空内容的目录名（保留目录本身） |
| `remove_dirs` | array | - | 要整体删除的目录名 |
| `keep_extensions` | array | - | 保留不清理的扩展名（优先级最高） |

## 仓库重命名自动识别

当你在 GitHub 上重命名仓库后，传统的同步脚本会因为 remote URL 失效而持续推送失败。本程序的增强功能：

1. **自动检测**：当 push 失败且错误信息包含 404/not found/denied 等关键词时，自动触发 GitHub API 检查
2. **API 查询**：通过 `https://api.github.com/repos/{owner}/{repo}` 查询仓库状态
3. **301 重定向处理**：GitHub 在仓库重命名后会返回 301，响应中包含新仓库地址
4. **自动更新**：检测到新 URL 后，自动执行 `git remote set-url origin <新URL>` 并重试 push
5. **历史记录**：所有 remote URL 变更记录保存在 `logs/autosync_state.json` 中

## 状态文件

程序运行时会生成 `logs/autosync_state.json`，记录：

- 上次同步时间和结果
- 连续失败次数
- 总同步次数和成功/失败统计
- remote URL 变更历史

## 日志

日志文件按日期命名：`logs/autosync_YYYYMMDD.log`

## 控制

| 操作 | 命令 |
|------|------|
| 停止 | `taskkill /F /IM pythonw.exe` |
| 手动启动 | 双击 `start.vbs` |
| 查看日志 | `logs/autosync_YYYYMMDD.log` |
| 查看状态 | `logs/autosync_state.json` |
| 取消开机自启 | 删除启动目录中的 `start.vbs` |

## 技术要点

- **Git 路径检测**：依次搜索 PATH → 常见安装路径 → 注册表，确保在任何安装方式下都能找到 git.exe
- **仓库重命名检测**：利用 GitHub API 的 301 重定向机制，无需额外认证即可检测公开仓库的重命名
- **单实例锁**：使用 `msvcrt.locking`（Windows 文件锁）+ PID 存活检测双重保障
- **文件删除**：Windows 环境下 Python 的文件删除被 safe-delete shim 拦截，改用 Win32 API（`DeleteFileW`）+ `cmd /c rmdir /s /q` 绕过
- **网络检查**：通过 TCP 连接 `github.com:443` 检测网络连通性，避免 DNS-only 检查的误判
- **重试递增延迟**：push 重试间隔随次数递增（第1次 5s，第2次 10s，第3次 15s），提高网络恢复后的成功率

## License

MIT
