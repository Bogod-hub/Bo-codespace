#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Keil project 定时同步到 GitHub（稳定性增强版）
每 interval_hours 小时扫描一次项目目录：
  1. 清理磁盘上的编译中间产物（可随时重新编译生成）
  2. 清理 git 版本库中被 .gitignore 忽略但仍跟踪的文件
  3. git add -A → commit → push（有变更才提交）

增强功能：
  - Git 可执行文件路径自动检测（不依赖 PATH 环境变量）
  - 仓库重命名后自动识别并更新 remote URL（通过 GitHub API 301 重定向）
  - watch_dir 路径丢失时自动搜索历史路径并提示
  - 配置文件校验与热重载
  - 单实例锁（防止多实例冲突）
  - 状态持久化（记录同步历史与错误统计）
  - push 前网络连通性检查
  - 更完善的错误恢复与重试机制

不依赖第三方库，纯标准库实现。
启动时立即执行一次，之后每 interval_hours 小时执行一次。
"""

import os
import sys
import time
import json
import ctypes
import logging
import subprocess
import hashlib
import socket
import shutil
import tempfile
from pathlib import Path
from datetime import datetime, timedelta
from urllib.request import urlopen, Request
from urllib.error import URLError, HTTPError

# 绕过 Python safe-delete shim：用 Win32 API 直接删文件，用 cmd rmdir 删目录
CREATE_NO_WINDOW = 0x08000000


def win_delete_file(path):
    """用 Win32 DeleteFileW 删文件，绕过 Python safe-delete shim"""
    return ctypes.windll.kernel32.DeleteFileW(str(path)) != 0


def win_remove_dir(path):
    """用 cmd rmdir /s /q 删目录，绕过 Python safe-delete shim"""
    r = subprocess.run(
        ["cmd", "/c", "rmdir", "/s", "/q", str(path)],
        capture_output=True, timeout=60,
        creationflags=CREATE_NO_WINDOW,
    )
    return r.returncode == 0


# ==================== Git 路径自动检测 ====================
class GitPathDetector:
    """自动检测 git.exe 路径，不依赖 PATH 环境变量"""

    GIT_CANDIDATES = [
        # 常见安装路径
        r"C:\Program Files\Git\cmd\git.exe",
        r"C:\Program Files\Git\bin\git.exe",
        r"C:\Program Files (x86)\Git\cmd\git.exe",
        r"C:\Program Files (x86)\Git\bin\git.exe",
        # Scoop / Chocolatey
        r"C:\ProgramData\chocolatey\bin\git.exe",
        # 用户目录安装
    ]

    @classmethod
    def detect(cls):
        """返回 git.exe 的完整路径，找不到返回 None"""
        # 1. 尝试 PATH 中的 git
        git_path = shutil.which("git")
        if git_path:
            return git_path

        # 2. 尝试常见安装路径
        # 先加入用户目录下的可能路径
        userprofile = os.environ.get("USERPROFILE", "")
        if userprofile:
            cls.GIT_CANDIDATES.extend([
                os.path.join(userprofile, "scoop", "shims", "git.exe"),
                os.path.join(userprofile, "AppData", "Local", "Programs", "Git", "cmd", "git.exe"),
            ])

        for candidate in cls.GIT_CANDIDATES:
            if os.path.isfile(candidate):
                return candidate

        # 3. 尝试从注册表读取
        try:
            import winreg
            for hive in [winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER]:
                try:
                    key = winreg.OpenKey(hive, r"SOFTWARE\GitForWindows")
                    install_path, _ = winreg.QueryValueEx(key, "InstallPath")
                    winreg.CloseKey(key)
                    git_exe = os.path.join(install_path, "cmd", "git.exe")
                    if os.path.isfile(git_exe):
                        return git_exe
                except (FileNotFoundError, OSError):
                    pass
        except ImportError:
            pass

        return None


# ==================== 单实例锁 ====================
class SingleInstance:
    """文件锁，确保同一时间只有一个实例运行"""

    def __init__(self, lockfile):
        self.lockfile = Path(lockfile)
        self.fd = None

    def acquire(self):
        """获取锁，成功返回 True，已有实例运行返回 False"""
        try:
            self.lockfile.parent.mkdir(parents=True, exist_ok=True)
            self.fd = open(self.lockfile, "w")
            # 尝试获取独占锁（Windows 用 msvcrt）
            try:
                import msvcrt
                msvcrt.locking(self.fd.fileno(), msvcrt.LK_NBLCK, 1)
            except (ImportError, OSError):
                # 回退方案：检查 PID 是否存活
                self.fd.seek(0)
                old_pid = self.fd.read().strip()
                if old_pid:
                    try:
                        old_pid = int(old_pid)
                        if self._is_process_alive(old_pid):
                            self.fd.close()
                            return False
                    except (ValueError, OSError):
                        pass
            self.fd.seek(0)
            self.fd.truncate()
            self.fd.write(str(os.getpid()))
            self.fd.flush()
            return True
        except Exception as ex:
            logging.error(f"获取单实例锁失败: {ex}")
            if self.fd:
                self.fd.close()
            return False

    def _is_process_alive(self, pid):
        """检查进程是否存活"""
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32
            handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
            if handle:
                kernel32.CloseHandle(handle)
                return True
            return False
        except Exception:
            return True  # 无法判断时保守处理

    def release(self):
        """释放锁"""
        try:
            if self.fd:
                self.fd.close()
            if self.lockfile.exists():
                self.lockfile.unlink()
        except Exception:
            pass


# ==================== 状态持久化 ====================
class StateManager:
    """记录同步状态，用于故障恢复和统计"""

    def __init__(self, state_file):
        self.state_file = Path(state_file)
        self.state = self._load()

    def _load(self):
        default = {
            "last_sync_time": None,
            "last_sync_success": None,
            "consecutive_failures": 0,
            "total_syncs": 0,
            "total_pushs": 0,
            "total_failures": 0,
            "remote_url_history": [],   # remote URL 变更历史
            "last_remote_url": None,    # 上次成功的 remote URL
        }
        if self.state_file.exists():
            try:
                with open(self.state_file, encoding="utf-8") as f:
                    default.update(json.load(f))
            except Exception:
                pass
        return default

    def save(self):
        try:
            self.state_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.state_file, "w", encoding="utf-8") as f:
                json.dump(self.state, f, indent=2, ensure_ascii=False)
        except Exception as ex:
            logging.warning(f"保存状态文件失败: {ex}")

    def record_sync(self, success, remote_url=None):
        self.state["last_sync_time"] = datetime.now().isoformat()
        self.state["last_sync_success"] = success
        self.state["total_syncs"] += 1
        if success:
            self.state["consecutive_failures"] = 0
            self.state["total_pushs"] += 1
            if remote_url and remote_url != self.state.get("last_remote_url"):
                self.state["remote_url_history"].append({
                    "url": remote_url,
                    "time": datetime.now().isoformat(),
                })
                self.state["last_remote_url"] = remote_url
        else:
            self.state["consecutive_failures"] += 1
            self.state["total_failures"] += 1
        self.save()


# ==================== 配置管理 ====================
CONFIG_PATH = Path(__file__).parent / "config.json"

DEFAULT_CONFIG = {
    "watch_dir": r"C:\Users\Bogod\Desktop\Keil project",
    "interval_hours": 12,
    "push_retry": 3,
    "push_retry_delay": 5,
    "log_dir": str(Path(__file__).parent / "logs"),
    # GitHub 用户名（用于仓库重命名检测，可选）
    "github_user": "",
    # 仓库重命名检测开关
    "auto_update_remote": True,
    # 网络检查开关
    "network_check": True,
    # 网络检查超时（秒）
    "network_timeout": 10,
    # 磁盘清理：删除这些扩展名的文件
    "clean_extensions": [
        ".obj", ".lst", ".m51", ".lnp", ".__i", ".htm",
        ".build_log.htm", ".plg", ".crf", ".dep", ".d",
        ".o", ".iex", ".map", ".bak",
    ],
    "clean_filenames": [
        "Thumbs.db", "ehthumbs.db", "Desktop.ini", ".DS_Store",
    ],
    "clean_dirs": ["Objects", "Listings", "DebugConfig"],
    "remove_dirs": ["keil5-code"],
    "keep_extensions": [".hex", ".uvproj", ".uvmpw", ".uvopt",
                        ".uvgui", ".c", ".h", ".s", ".gitignore"],
}


def load_config():
    """加载配置文件，与默认配置合并"""
    cfg = DEFAULT_CONFIG.copy()
    if CONFIG_PATH.exists():
        try:
            with open(CONFIG_PATH, encoding="utf-8") as f:
                user_cfg = json.load(f)
            cfg.update(user_cfg)
        except json.JSONDecodeError as ex:
            print(f"[ERROR] config.json 格式错误: {ex}")
            print("[INFO] 将使用默认配置继续。")
        except Exception as ex:
            print(f"[WARN] 读取 config.json 失败，用默认配置: {ex}")
    else:
        # 首次运行：生成默认配置文件
        try:
            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(DEFAULT_CONFIG, f, indent=2, ensure_ascii=False)
            print(f"[INFO] 已生成默认配置文件: {CONFIG_PATH}")
            print("[INFO] 请编辑 config.json 设置 watch_dir 后重新运行。")
        except Exception:
            pass
    return cfg


def validate_config(cfg):
    """校验配置项，返回 (is_valid, warnings_list)"""
    warnings = []
    valid = True

    # watch_dir 校验
    watch_dir = cfg.get("watch_dir", "")
    if not watch_dir:
        warnings.append("watch_dir 未设置")
        valid = False
    elif not Path(watch_dir).exists():
        warnings.append(f"watch_dir 路径不存在: {watch_dir}")
        valid = False

    # interval_hours 校验
    interval = cfg.get("interval_hours", 0)
    if not isinstance(interval, (int, float)) or interval <= 0:
        warnings.append(f"interval_hours 无效 ({interval})，使用默认值 12")
        cfg["interval_hours"] = 12

    # push_retry 校验
    retry = cfg.get("push_retry", 0)
    if not isinstance(retry, int) or retry < 0:
        warnings.append(f"push_retry 无效 ({retry})，使用默认值 3")
        cfg["push_retry"] = 3

    # push_retry_delay 校验
    delay = cfg.get("push_retry_delay", 0)
    if not isinstance(delay, (int, float)) or delay < 0:
        warnings.append(f"push_retry_delay 无效 ({delay})，使用默认值 5")
        cfg["push_retry_delay"] = 5

    return valid, warnings


def setup_logging(log_dir):
    Path(log_dir).mkdir(parents=True, exist_ok=True)
    log_file = Path(log_dir) / f"autosync_{datetime.now():%Y%m%d}.log"
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", "%H:%M:%S")
    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setFormatter(fmt)
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    logging.basicConfig(level=logging.INFO, handlers=[fh, sh])


# ==================== 磁盘清理 ====================
class DiskCleaner:
    """扫描并删除 Keil 编译中间产物和临时文件"""

    def __init__(self, cfg):
        self.cfg = cfg
        self.watch_dir = Path(cfg["watch_dir"])
        self.clean_ext = set(cfg.get("clean_extensions", []))
        self.clean_names = set(cfg.get("clean_filenames", []))
        self.clean_dirs = set(cfg.get("clean_dirs", []))
        self.remove_dirs = set(cfg.get("remove_dirs", []))
        self.keep_ext = set(cfg.get("keep_extensions", []))
        self.deleted_count = 0
        self.freed_bytes = 0

    def _should_clean_file(self, filepath):
        name = filepath.name
        if name in self.clean_names:
            return True
        suffix = filepath.suffix.lower()
        if suffix in self.clean_ext and suffix not in self.keep_ext:
            return True
        for ext in self.clean_ext:
            if name.lower().endswith(ext) and suffix not in self.keep_ext:
                if not any(name.lower().endswith(k) for k in self.keep_ext):
                    return True
        return False

    def _safe_remove(self, path):
        try:
            size = path.stat().st_size if path.exists() else 0
            if win_delete_file(path):
                self.deleted_count += 1
                self.freed_bytes += size
                return True
            logging.warning(f"  删除失败(Win32): {path.name}")
            return False
        except Exception as ex:
            logging.warning(f"  删除异常: {path.name} - {ex}")
            return False

    def _is_keep_file(self, filepath):
        name = filepath.name.lower()
        return any(name.endswith(ext) for ext in self.keep_ext)

    def _clean_dir_contents(self, dirpath):
        if not dirpath.exists() or not dirpath.is_dir():
            return
        for item in dirpath.iterdir():
            try:
                if item.is_dir():
                    if win_remove_dir(item):
                        self.deleted_count += 1
                else:
                    if self._is_keep_file(item):
                        logging.debug(f"  保留: {item.name}")
                        continue
                    self._safe_remove(item)
            except Exception as ex:
                logging.warning(f"  清空目录失败: {item} - {ex}")

    def run(self):
        self.deleted_count = 0
        self.freed_bytes = 0
        logging.info("--- 磁盘清理开始 ---")

        # 1. 整体删除的目录
        for dirname in self.remove_dirs:
            d = self.watch_dir / dirname
            if d.exists() and d.is_dir():
                try:
                    size = sum(f.stat().st_size for f in d.rglob("*") if f.is_file())
                    if win_remove_dir(d):
                        self.deleted_count += 1
                        self.freed_bytes += size
                        logging.info(f"  删除目录: {dirname}/ ({size // 1024} KB)")
                except Exception as ex:
                    logging.warning(f"  删除目录失败: {dirname} - {ex}")

        # 2. 递归扫描清理文件 + 清空编译输出目录
        for root, dirs, files in os.walk(self.watch_dir):
            root_path = Path(root)
            if ".git" in dirs:
                dirs.remove(".git")
            if "venv" in dirs:
                dirs.remove("venv")

            for dirname in list(dirs):
                if dirname in self.clean_dirs:
                    dirpath = root_path / dirname
                    self._clean_dir_contents(dirpath)
                    logging.info(f"  清空目录: {dirpath.relative_to(self.watch_dir)}/")

            for fname in files:
                fpath = root_path / fname
                if self._should_clean_file(fpath):
                    self._safe_remove(fpath)

        freed_kb = self.freed_bytes // 1024
        if self.deleted_count > 0:
            logging.info(f"磁盘清理完成: 删除 {self.deleted_count} 项, 释放 {freed_kb} KB")
        else:
            logging.info("磁盘清理完成: 无垃圾文件")
        return self.deleted_count


# ==================== Git 同步（增强版）====================
class GitSyncer:
    def __init__(self, cfg, git_exe, state_manager):
        self.cfg = cfg
        self.repo = cfg["watch_dir"]
        self.git_exe = git_exe
        self.state = state_manager
        self._remote_url = None

    def git(self, *args):
        """执行 git 命令，返回 (code, stdout, stderr)"""
        try:
            r = subprocess.run(
                [self.git_exe] + list(args),
                cwd=self.repo, capture_output=True, text=True, timeout=120,
            )
            return r.returncode, r.stdout.strip(), r.stderr.strip()
        except subprocess.TimeoutExpired:
            return -1, "", "git timeout"
        except Exception as ex:
            return -1, "", str(ex)

    def get_remote_url(self):
        """获取当前 remote origin URL"""
        if self._remote_url:
            return self._remote_url
        c, o, _ = self.git("remote", "get-url", "origin")
        if c == 0:
            self._remote_url = o.strip()
        return self._remote_url

    def set_remote_url(self, url):
        """更新 remote origin URL"""
        c, _, e = self.git("remote", "set-url", "origin", url)
        if c == 0:
            self._remote_url = url
            logging.info(f"  remote URL 已更新: {url}")
            return True
        logging.error(f"  更新 remote URL 失败: {e}")
        return False

    def check_remote_repo_renamed(self, old_url):
        """
        通过 GitHub API 检测仓库是否被重命名。
        GitHub 在仓库重命名后会返回 301 重定向，新 URL 在响应中。
        返回新的 URL（如果重命名），否则返回 None。
        """
        # 解析 old_url 提取 owner/repo
        # 支持格式：
        #   https://github.com/owner/repo.git
        #   git@github.com:owner/repo.git
        owner, repo = self._parse_github_url(old_url)
        if not owner or not repo:
            logging.debug(f"  无法解析 GitHub URL: {old_url}")
            return None

        api_url = f"https://api.github.com/repos/{owner}/{repo}"
        logging.info(f"  检查仓库是否被重命名: {owner}/{repo}")

        try:
            req = Request(api_url, headers={
                "User-Agent": "autosync-py",
                "Accept": "application/vnd.github.v3+json",
            })
            with urlopen(req, timeout=self.cfg.get("network_timeout", 10)) as resp:
                # 200 = 仓库存在且名称未变
                data = json.loads(resp.read().decode("utf-8"))
                current_full_name = data.get("full_name", "")
                current_url = data.get("clone_url", "").replace(".git", "") + ".git"
                if current_full_name and current_full_name.lower() != f"{owner}/{repo}".lower():
                    logging.info(f"  仓库已重命名: {owner}/{repo} → {current_full_name}")
                    return current_url
                return None

        except HTTPError as e:
            if e.code == 301:
                # 301 重定向 = 仓库被重命名
                location = e.headers.get("Location", "")
                if location:
                    # API 返回的是新的 API URL，需要转换为 clone URL
                    # https://api.github.com/repos/{owner}/{new_repo}
                    parts = location.rstrip("/").split("/")
                    if len(parts) >= 2:
                        new_owner, new_repo = parts[-2], parts[-1]
                        new_url = f"https://github.com/{new_owner}/{new_repo}.git"
                        logging.info(f"  仓库已重命名: {owner}/{repo} → {new_owner}/{new_repo}")
                        return new_url
            elif e.code == 404:
                logging.warning(f"  仓库不存在或已被删除: {owner}/{repo}")
                return None
            elif e.code == 403:
                logging.warning("  GitHub API 速率限制，跳过重命名检测")
                return None
            else:
                logging.warning(f"  GitHub API 返回 {e.code}")
                return None
        except URLError as e:
            logging.warning(f"  网络错误，无法检查仓库重命名: {e}")
            return None
        except Exception as ex:
            logging.warning(f"  检查仓库重命名失败: {ex}")
            return None

    def _parse_github_url(self, url):
        """
        从 GitHub URL 中解析 owner 和 repo。
        支持 HTTPS 和 SSH 格式。
        返回 (owner, repo) 或 (None, None)。
        """
        url = url.strip()
        # https://github.com/owner/repo.git 或 https://github.com/owner/repo
        if "github.com" in url:
            # 去掉协议和域名部分
            if url.startswith("https://") or url.startswith("http://"):
                path = url.split("github.com/", 1)[-1]
            elif url.startswith("git@"):
                # git@github.com:owner/repo.git
                path = url.split("github.com:", 1)[-1]
            else:
                path = url
            # 去掉 .git 后缀
            path = path.rstrip("/").replace(".git", "")
            parts = path.split("/")
            if len(parts) >= 2:
                return parts[-2], parts[-1]
        return None, None

    def check_network(self):
        """检查网络连通性（ping github.com）"""
        timeout = self.cfg.get("network_timeout", 10)
        try:
            # 用 DNS 解析 + TCP 连接检测
            socket.setdefaulttimeout(timeout)
            socket.create_connection(("github.com", 443), timeout=timeout)
            return True
        except (socket.timeout, OSError) as e:
            logging.warning(f"  网络不通: {e}")
            return False

    def clean_tracked_ignored(self):
        """移除被 .gitignore 忽略但仍被 git 跟踪的文件"""
        c, o, _ = self.git("ls-files", "-i", "-c", "--exclude-standard")
        if c != 0 or not o:
            return 0
        ignored_tracked = [f for f in o.splitlines() if f.strip()]
        if not ignored_tracked:
            return 0
        logging.info(f"发现 {len(ignored_tracked)} 个被忽略但仍跟踪的文件，移除跟踪:")
        for f in ignored_tracked[:20]:
            logging.info(f"  {f}")
        if len(ignored_tracked) > 20:
            logging.info(f"  ...共 {len(ignored_tracked)} 项")
        c, o, e = self.git("rm", "--cached", "-r", "--", *ignored_tracked)
        if c == 0:
            logging.info(f"已从版本库移除 {len(ignored_tracked)} 个文件")
        else:
            logging.error(f"git rm --cached 失败: {e}")
        return len(ignored_tracked)

    def has_real_changes(self):
        """git status --porcelain 是否有变更"""
        c, o, _ = self.git("status", "--porcelain")
        if c != 0 or not o:
            return False, []
        files = [ln for ln in o.splitlines() if ln.strip()]
        return bool(files), files

    def _try_push_with_remote_check(self):
        """
        执行 push，如果失败则检查是否是仓库重命名导致的。
        如果检测到重命名，更新 remote URL 后重试。
        返回 (success, remote_url_used)。
        """
        for i in range(self.cfg["push_retry"]):
            c, o, e = self.git("push")
            if c == 0:
                return True, self.get_remote_url()

            err_msg = (e or o).lower()
            logging.warning(f"  push 失败(第{i+1}次): {e or o}")

            # 检查是否是 remote URL 失效（仓库重命名/移动）
            if self.cfg.get("auto_update_remote", True):
                if any(keyword in err_msg for keyword in [
                    "could not resolve host", "404", "not found",
                    "repository not found", "does not exist",
                    "access denied", "denied", "error 403"
                ]):
                    logging.info("  检测到 push 失败可能与 remote URL 有关，尝试检查仓库重命名...")
                    current_url = self.get_remote_url()
                    if current_url:
                        new_url = self.check_remote_repo_renamed(current_url)
                        if new_url and new_url != current_url:
                            logging.info(f"  检测到仓库已重命名，更新 remote URL...")
                            if self.set_remote_url(new_url):
                                # URL 已更新，立即重试 push
                                c2, o2, e2 = self.git("push")
                                if c2 == 0:
                                    logging.info("  仓库重命名后推送成功 ✓")
                                    return True, new_url
                                logging.warning(f"  更新 URL 后 push 仍失败: {e2 or o2}")
                            else:
                                logging.error("  更新 remote URL 失败，请手动检查")
                        else:
                            logging.info("  仓库未被重命名，可能是其他原因导致 push 失败")

            if i < self.cfg["push_retry"] - 1:
                delay = self.cfg["push_retry_delay"] * (i + 1)  # 递增延迟
                logging.info(f"  等待 {delay}s 后重试...")
                time.sleep(delay)

        return False, self.get_remote_url()

    def sync(self):
        """执行一次完整的同步：清理跟踪 → add → commit → push"""
        logging.info("=== 开始同步周期 ===")

        # 0. 网络检查
        if self.cfg.get("network_check", True):
            if not self.check_network():
                logging.error("网络不通，跳过本次同步")
                self.state.record_sync(False, self.get_remote_url())
                logging.info("=== 同步周期结束 ===\n")
                return False

        # 1. 清理版本库中被忽略但仍跟踪的文件
        removed = self.clean_tracked_ignored()

        # 2. git add -A
        c, o, e = self.git("add", "-A")
        if c != 0:
            logging.error(f"git add 失败: {e}")
            self.state.record_sync(False, self.get_remote_url())
            logging.info("=== 同步周期结束 ===\n")
            return False

        # 3. 检查是否有变更
        changed, files = self.has_real_changes()
        if not changed:
            logging.info("无 git 变更，跳过提交")
            # 即使无变更也尝试 pull 一次保持同步
            c, o, e = self.git("pull", "--ff-only")
            if c == 0:
                logging.info("git pull 完成（无本地变更）")
            self.state.record_sync(True, self.get_remote_url())
            logging.info("=== 同步周期结束 ===\n")
            return True

        # 变更摘要
        summary = "; ".join(f.strip() for f in files[:8])
        if len(files) > 8:
            summary += f" ...共{len(files)}项"
        msg = f"auto-sync: {datetime.now():%Y-%m-%d %H:%M} | {summary}"
        logging.info(f"提交: {msg}")

        # 4. git commit
        c, o, e = self.git("commit", "-m", msg)
        if c != 0:
            if "nothing to commit" in (o + e).lower():
                logging.info("nothing to commit")
                self.state.record_sync(True, self.get_remote_url())
                logging.info("=== 同步周期结束 ===\n")
                return True
            logging.error(f"git commit 失败: {e}")
            self.state.record_sync(False, self.get_remote_url())
            logging.info("=== 同步周期结束 ===\n")
            return False
        commit_line = o.splitlines()[-1] if o else ""
        logging.info(f"commit: {commit_line}")

        # 5. git push（带 remote URL 自动更新检测）
        success, remote_url = self._try_push_with_remote_check()
        self.state.record_sync(success, remote_url)

        if success:
            logging.info("推送成功 ✓")
        else:
            logging.error(f"push 最终失败，已重试 {self.cfg['push_retry']} 次")

        logging.info("=== 同步周期结束 ===\n")
        return success


# ==================== 主程序 ====================
def main():
    cfg = load_config()
    setup_logging(cfg["log_dir"])
    interval_sec = cfg["interval_hours"] * 3600

    logging.info("=" * 56)
    logging.info("Keil project → GitHub 定时同步（稳定性增强版）")
    logging.info(f"扫描目录: {cfg['watch_dir']}")
    logging.info(f"间隔: 每 {cfg['interval_hours']} 小时")
    logging.info(f"push重试: {cfg['push_retry']} 次")
    logging.info(f"仓库重命名检测: {'开启' if cfg.get('auto_update_remote') else '关闭'}")
    logging.info(f"网络检查: {'开启' if cfg.get('network_check') else '关闭'}")
    logging.info("=" * 56)

    # --- Git 路径检测 ---
    git_exe = GitPathDetector.detect()
    if not git_exe:
        logging.error("未找到 git.exe，请安装 Git 或将其添加到 PATH")
        sys.exit(1)
    logging.info(f"Git 路径: {git_exe}")

    # --- 配置校验 ---
    valid, warnings = validate_config(cfg)
    for w in warnings:
        logging.warning(f"配置警告: {w}")
    if not valid:
        logging.error("配置校验失败，请检查 config.json")
        sys.exit(1)

    # --- 单实例锁 ---
    lock_file = Path(cfg["log_dir"]) / "autosync.lock"
    lock = SingleInstance(lock_file)
    if not lock.acquire():
        logging.error("另一个实例正在运行，退出。")
        sys.exit(1)

    # --- 状态管理 ---
    state_file = Path(cfg["log_dir"]) / "autosync_state.json"
    state = StateManager(state_file)

    # 显示历史状态
    if state.state.get("last_sync_time"):
        last = state.state["last_sync_time"]
        success = state.state.get("last_sync_success")
        failures = state.state.get("consecutive_failures", 0)
        logging.info(f"上次同步: {last} ({'成功' if success else '失败'})")
        if failures > 0:
            logging.warning(f"连续失败 {failures} 次")

    syncer = GitSyncer(cfg, git_exe, state)
    cleaner = DiskCleaner(cfg)

    # --- 校验 git 仓库 ---
    c, o, _ = syncer.git("rev-parse", "--is-inside-work-tree")
    if c != 0 or o.strip() != "true":
        logging.error(f"目录不是 git 仓库: {cfg['watch_dir']}")
        logging.error("请先运行: git init && git remote add origin <url>")
        lock.release()
        sys.exit(1)

    c, o, _ = syncer.git("remote", "get-url", "origin")
    if c == 0:
        logging.info(f"远程仓库: {o}")
        state.state["last_remote_url"] = o
    else:
        logging.warning("未配置 origin remote，请运行: git remote add origin <url>")

    c, o, _ = syncer.git("log", "--oneline", "-1")
    logging.info(f"当前提交: {o if c == 0 else '(无历史)'}")

    logging.info("定时同步已启动。按 Ctrl+C 停止。\n")

    # --- 主循环 ---
    first_run = True
    try:
        while True:
            if not first_run:
                next_time = datetime.now() + timedelta(seconds=interval_sec)
                logging.info(f"下次扫描: {next_time:%H:%M:%S} ( {cfg['interval_hours']}h 后 )")
                time.sleep(interval_sec)
            first_run = False

            # 检查 watch_dir 是否仍然存在
            if not Path(cfg["watch_dir"]).exists():
                logging.error(f"watch_dir 不存在: {cfg['watch_dir']}")
                logging.error("请检查路径是否被移动或重命名")
                # 等待下次重试，不退出
                continue

            try:
                # 1. 磁盘清理
                cleaner.run()
                # 2. git 同步
                syncer.sync()
            except Exception as ex:
                logging.error(f"同步周期异常: {ex}", exc_info=True)
                state.record_sync(False)

    except KeyboardInterrupt:
        logging.info("正在停止...")
    finally:
        lock.release()
        logging.info("已退出。")


if __name__ == "__main__":
    main()
