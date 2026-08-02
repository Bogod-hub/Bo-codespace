#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
镜像同步模块 - 解决 TRAE 虚拟文件系统下 Git 无法工作的问题

TRAE SOLO CN 的虚拟文件系统禁止目录枚举（os.listdir / FindFirstFile 均失效），
导致 Git 无法自动发现仓库、无法枚举工作区文件。

本模块通过以下方式绕过限制：
  1. 用 git ls-files 从索引获取已跟踪文件列表
  2. 用 os.path.exists + shutil.copy2 逐个复制文件（直接文件访问不受影响）
  3. 在非虚拟化目录（G: 盘）执行 Git 操作

使用方式：
  在 autosync.py 的配置中设置 source_dir 和 watch_dir
  - source_dir: 虚拟化路径（如 C:\\Users\\Bogod\\Desktop\\Keil project）
  - watch_dir: 非虚拟化镜像路径（如 G:\\Bogod\\Documents\\trae-list\\Keil-project-mirror）
"""

import os
import shutil
import logging
from pathlib import Path


class MirrorSync:
    """将虚拟化路径的文件镜像到非虚拟化路径"""

    def __init__(self, source_dir, work_dir, git_exe):
        self.source_dir = Path(source_dir)
        self.work_dir = Path(work_dir)
        self.git_exe = git_exe
        self.copied_count = 0
        self.skipped_count = 0
        self.missing_count = 0

    def _git(self, *args):
        """执行 git 命令（使用 --git-dir 指定源仓库）"""
        import subprocess
        git_dir = str(self.source_dir / ".git")
        cmd = [self.git_exe, f"--git-dir={git_dir}"] + list(args)
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            return r.returncode, r.stdout.strip(), r.stderr.strip()
        except Exception as ex:
            return -1, "", str(ex)

    def get_tracked_files(self):
        """从 git 索引获取已跟踪文件列表"""
        c, o, e = self._git("ls-files")
        if c != 0:
            logging.error(f"获取 git ls-files 失败: {e}")
            return []
        return [f.strip() for f in o.splitlines() if f.strip()]

    def copy_file(self, rel_path):
        """复制单个文件从源目录到镜像目录"""
        src = self.source_dir / rel_path
        dst = self.work_dir / rel_path

        if not src.exists():
            logging.debug(f"  源文件不存在，跳过: {rel_path}")
            self.missing_count += 1
            return False

        try:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(src), str(dst))
            self.copied_count += 1
            return True
        except Exception as ex:
            logging.warning(f"  复制失败: {rel_path} - {ex}")
            self.skipped_count += 1
            return False

    def mirror(self):
        """执行一次完整的镜像同步"""
        self.copied_count = 0
        self.skipped_count = 0
        self.missing_count = 0

        logging.info("--- 镜像同步开始 ---")
        logging.info(f"  源目录: {self.source_dir}")
        logging.info(f"  镜像目录: {self.work_dir}")

        # 确保镜像目录存在
        self.work_dir.mkdir(parents=True, exist_ok=True)

        # 获取已跟踪文件列表
        tracked = self.get_tracked_files()
        if not tracked:
            logging.warning("  未获取到跟踪文件列表")
            return False

        logging.info(f"  跟踪文件: {len(tracked)} 个")

        # 复制所有跟踪文件
        for rel_path in tracked:
            self.copy_file(rel_path)

        # 同时复制 .gitignore（如果存在且未被跟踪）
        gitignore = self.source_dir / ".gitignore"
        if gitignore.exists():
            dst_gitignore = self.work_dir / ".gitignore"
            if not dst_gitignore.exists():
                try:
                    shutil.copy2(str(gitignore), str(dst_gitignore))
                except Exception:
                    pass

        logging.info(
            f"镜像同步完成: 复制 {self.copied_count}, "
            f"跳过 {self.skipped_count}, 缺失 {self.missing_count}"
        )
        return self.copied_count > 0

    def scan_new_files(self):
        """
        扫描源目录中的新文件（未被 git 跟踪的文件）。
        由于目录枚举不工作，这里只能检查已知的子目录结构。
        """
        # 从 git ls-files 获取已跟踪文件的目录
        tracked = self.get_tracked_files()
        tracked_dirs = set()
        for f in tracked:
            parent = str(Path(f).parent)
            if parent and parent != ".":
                tracked_dirs.add(parent)

        new_files = []
        # 检查每个已知目录中的文件
        for dir_rel in tracked_dirs:
            src_dir = self.source_dir / dir_rel
            if not src_dir.exists():
                continue
            # 尝试读取目录（可能失败，但试试）
            try:
                for item in src_dir.iterdir():
                    if item.is_file():
                        rel = str(item.relative_to(self.source_dir)).replace("\\", "/")
                        if rel not in tracked:
                            new_files.append(rel)
            except Exception:
                pass

        return new_files
