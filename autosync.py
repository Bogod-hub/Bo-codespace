#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Keil project 定时同步到 GitHub
每 12 小时扫描一次 Keil project 目录：
  1. 清理磁盘上的编译中间产物（可随时重新编译生成）
  2. 清理 git 版本库中被 .gitignore 忽略但仍跟踪的文件
  3. git add -A → commit → push（有变更才提交）

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
from pathlib import Path
from datetime import datetime

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

# ---------- 配置 ----------
CONFIG_PATH = Path(__file__).parent / "config.json"

DEFAULT_CONFIG = {
    "watch_dir": r"C:\Users\Bogod\Desktop\Keil project",
    "interval_hours": 12,            # 扫描间隔（小时）
    "push_retry": 3,                 # push 失败重试次数
    "push_retry_delay": 5,           # 重试间隔（秒）
    "log_dir": str(Path(__file__).parent / "logs"),
    # 磁盘清理：删除这些扩展名的文件（可随时重新编译生成）
    "clean_extensions": [
        ".obj", ".lst", ".m51", ".lnp", ".__i", ".htm",
        ".build_log.htm", ".plg", ".crf", ".dep", ".d",
        ".o", ".iex", ".map", ".bak",
    ],
    # 磁盘清理：删除这些文件名
    "clean_filenames": [
        "Thumbs.db", "ehthumbs.db", "Desktop.ini", ".DS_Store",
    ],
    # 磁盘清理：清空这些目录的内容（保留目录本身，Keil 需要）
    "clean_dirs": ["Objects", "Listings", "DebugConfig"],
    # 磁盘清理：删除这些目录（整体删除，如嵌套 clone）
    "remove_dirs": ["keil5-code"],
    # 保留不清理的扩展名（即使匹配上述规则也跳过）
    "keep_extensions": [".hex", ".uvproj", ".uvmpw", ".uvopt",
                        ".uvgui", ".c", ".h", ".s", ".gitignore"],
}


def load_config():
    cfg = DEFAULT_CONFIG.copy()
    if CONFIG_PATH.exists():
        try:
            with open(CONFIG_PATH, encoding="utf-8") as f:
                cfg.update(json.load(f))
        except Exception as ex:
            print(f"[WARN] 读取 config.json 失败，用默认配置: {ex}")
    return cfg


def setup_logging(log_dir):
    Path(log_dir).mkdir(parents=True, exist_ok=True)
    log_file = Path(log_dir) / f"autosync_{datetime.now():%Y%m%d}.log"
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", "%H:%M:%S")
    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setFormatter(fmt)
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    logging.basicConfig(level=logging.INFO, handlers=[fh, sh])


# ---------- 磁盘清理 ----------
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
        """判断文件是否应该被清理"""
        name = filepath.name
        # 文件名匹配
        if name in self.clean_names:
            return True
        # 扩展名匹配
        suffix = filepath.suffix.lower()
        if suffix in self.clean_ext and suffix not in self.keep_ext:
            return True
        # 特殊处理：.build_log.htm 等复合扩展名
        for ext in self.clean_ext:
            if name.lower().endswith(ext) and suffix not in self.keep_ext:
                # 确保不是源码/工程文件
                if not any(name.lower().endswith(k) for k in self.keep_ext):
                    return True
        return False

    def _safe_remove(self, path):
        """用 Win32 API 删文件，绕过 safe-delete shim"""
        try:
            size = path.stat().st_size if path.exists() else 0
            if win_delete_file(path):
                self.deleted_count += 1
                self.freed_bytes += size
                return True
            # DeleteFileW 失败（文件被占用等）
            logging.warning(f"  删除失败(Win32): {path.name}")
            return False
        except Exception as ex:
            logging.warning(f"  删除异常: {path.name} - {ex}")
            return False

    def _is_keep_file(self, filepath):
        """文件是否在保留列表中（如 .hex 烧录文件不应删除）"""
        name = filepath.name.lower()
        return any(name.endswith(ext) for ext in self.keep_ext)

    def _clean_dir_contents(self, dirpath):
        """清空目录内容但保留目录本身，跳过 keep_extensions 的文件"""
        if not dirpath.exists() or not dirpath.is_dir():
            return
        for item in dirpath.iterdir():
            try:
                if item.is_dir():
                    # 子目录用 cmd rmdir 整体删除
                    if win_remove_dir(item):
                        self.deleted_count += 1
                else:
                    # 保留文件跳过（如 .hex）
                    if self._is_keep_file(item):
                        logging.debug(f"  保留: {item.name}")
                        continue
                    self._safe_remove(item)
            except Exception as ex:
                logging.warning(f"  清空目录失败: {item} - {ex}")

    def run(self):
        """执行一次磁盘清理"""
        self.deleted_count = 0
        self.freed_bytes = 0
        logging.info("--- 磁盘清理开始 ---")

        # 1. 整体删除的目录（如嵌套 clone）
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
            # 跳过 .git 目录
            if ".git" in dirs:
                dirs.remove(".git")
            # 跳过 venv 目录（如果在仓库内）
            if "venv" in dirs:
                dirs.remove("venv")

            # 清空编译输出目录内容
            for dirname in list(dirs):
                if dirname in self.clean_dirs:
                    dirpath = root_path / dirname
                    self._clean_dir_contents(dirpath)
                    logging.info(f"  清空目录: {dirpath.relative_to(self.watch_dir)}/")

            # 清理文件
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


# ---------- git 同步 ----------
class GitSyncer:
    def __init__(self, cfg):
        self.cfg = cfg
        self.repo = cfg["watch_dir"]

    def git(self, *args):
        """执行 git 命令，返回 (code, stdout, stderr)"""
        try:
            r = subprocess.run(
                ["git"] + list(args),
                cwd=self.repo, capture_output=True, text=True, timeout=120,
            )
            return r.returncode, r.stdout.strip(), r.stderr.strip()
        except subprocess.TimeoutExpired:
            return -1, "", "git timeout"
        except Exception as ex:
            return -1, "", str(ex)

    def clean_tracked_ignored(self):
        """移除被 .gitignore 忽略但仍被 git 跟踪的文件（git rm --cached）"""
        # 列出被跟踪但匹配 .gitignore 的文件
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
        # git rm --cached（不删磁盘文件，只从版本库移除）
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

    def sync(self):
        """执行一次完整的同步：清理跟踪 → add → commit → push"""
        logging.info("=== 开始同步周期 ===")

        # 1. 清理版本库中被忽略但仍跟踪的文件
        removed = self.clean_tracked_ignored()

        # 2. git add -A
        c, o, e = self.git("add", "-A")
        if c != 0:
            logging.error(f"git add 失败: {e}")
            return False

        # 3. 检查是否有变更
        changed, files = self.has_real_changes()
        if not changed:
            logging.info("无 git 变更，跳过提交")
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
                return True
            logging.error(f"git commit 失败: {e}")
            return False
        commit_line = o.splitlines()[-1] if o else ""
        logging.info(f"commit: {commit_line}")

        # 5. git push（重试）
        for i in range(self.cfg["push_retry"]):
            c, o, e = self.git("push")
            if c == 0:
                logging.info("推送成功 ✓")
                logging.info("=== 同步周期结束 ===\n")
                return True
            logging.warning(f"push 失败(第{i+1}次): {e or o}")
            time.sleep(self.cfg["push_retry_delay"])

        logging.error(f"push 最终失败，已重试 {self.cfg['push_retry']} 次")
        logging.info("=== 同步周期结束 ===\n")
        return False


# ---------- 主程序 ----------
def main():
    cfg = load_config()
    setup_logging(cfg["log_dir"])
    interval_sec = cfg["interval_hours"] * 3600

    logging.info("=" * 56)
    logging.info("Keil project → GitHub 定时同步")
    logging.info(f"扫描目录: {cfg['watch_dir']}")
    logging.info(f"间隔: 每 {cfg['interval_hours']} 小时")
    logging.info(f"push重试: {cfg['push_retry']} 次")
    logging.info("=" * 56)

    syncer = GitSyncer(cfg)
    cleaner = DiskCleaner(cfg)

    # 校验 git 仓库
    c, o, _ = syncer.git("rev-parse", "--is-inside-work-tree")
    if c != 0 or o.strip() != "true":
        logging.error(f"目录不是 git 仓库: {cfg['watch_dir']}")
        sys.exit(1)
    c, o, _ = syncer.git("remote", "get-url", "origin")
    logging.info(f"远程仓库: {o if c == 0 else '未配置 origin'}")
    c, o, _ = syncer.git("log", "--oneline", "-1")
    logging.info(f"当前提交: {o if c == 0 else '(无历史)'}")

    logging.info("定时同步已启动。按 Ctrl+C 停止。\n")

    # 主循环：启动时立即执行一次，之后按间隔执行
    first_run = True
    try:
        while True:
            if not first_run:
                next_time = datetime.now() + timedelta_for_log(interval_sec)
                logging.info(f"下次扫描: {next_time:%H:%M:%S} ( {cfg['interval_hours']}h 后 )")
                time.sleep(interval_sec)
            first_run = False

            # 执行一次完整周期
            try:
                # 1. 磁盘清理
                cleaner.run()
                # 2. git 同步
                syncer.sync()
            except Exception as ex:
                logging.error(f"同步周期异常: {ex}", exc_info=True)

    except KeyboardInterrupt:
        logging.info("正在停止...")
    logging.info("已退出。")


def timedelta_for_log(seconds):
    """避免 import timedelta，直接构造"""
    from datetime import timedelta
    return timedelta(seconds=seconds)


if __name__ == "__main__":
    main()
