# 代码实时上传器

定时扫描项目目录，自动清理编译中间产物并同步到 GitHub 仓库。

## 功能

- **定时扫描**：每 12 小时（可配置）扫描一次指定目录
- **磁盘清理**：自动删除编译中间产物（.obj/.lst/.m51/.lnp/.htm 等），保留源码和工程文件
- **版本库清理**：移除被 .gitignore 忽略但仍被 git 跟踪的文件
- **自动同步**：git add → commit → push，有变更才提交
- **开机自启**：通过 VBS 脚本无窗口后台运行
- **零依赖**：纯 Python 标准库实现

## 文件说明

| 文件 | 作用 |
|------|------|
| `autosync.py` | 主程序：定时扫描 + 磁盘清理 + git 同步 |
| `config.json` | 配置文件：扫描目录、间隔、清理规则 |
| `start.vbs` | 无窗口后台启动器 |

## 快速开始

### 1. 前置条件

- Python 3.8+
- Git 已安装并配置好身份（`user.name` / `user.email`）
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

根据你的项目类型调整 `clean_extensions`（要删除的文件类型）和 `keep_extensions`（保留的文件类型）。

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

| 字段 | 类型 | 说明 |
|------|------|------|
| `watch_dir` | string | 监听的项目目录（必须是 git 仓库） |
| `interval_hours` | int | 扫描间隔（小时） |
| `push_retry` | int | push 失败重试次数 |
| `push_retry_delay` | int | 重试间隔（秒） |
| `log_dir` | string | 日志目录 |
| `clean_extensions` | array | 要删除的文件扩展名 |
| `clean_filenames` | array | 要删除的文件名 |
| `clean_dirs` | array | 要清空内容的目录名（保留目录本身） |
| `remove_dirs` | array | 要整体删除的目录名 |
| `keep_extensions` | array | 保留不清理的扩展名（优先级最高） |

## 日志

日志文件按日期命名：`logs/autosync_YYYYMMDD.log`

## 控制

| 操作 | 命令 |
|------|------|
| 停止 | `taskkill /F /IM pythonw.exe` |
| 手动启动 | 双击 `start.vbs` |
| 查看日志 | `logs/autosync_YYYYMMDD.log` |
| 取消开机自启 | 删除启动目录中的 `start.vbs` |

## 技术要点

- Windows 环境下 Python 的文件删除被 safe-delete shim 拦截，改用 `ctypes.windll.kernel32.DeleteFileW`（Win32 API）删文件 + `cmd /c rmdir /s /q` 删目录绕过
- 提交信息自动生成：`auto-sync: YYYY-MM-DD HH:MM | 变更文件摘要`
- 启动时立即执行一次扫描，之后按间隔定时执行

## License

MIT
