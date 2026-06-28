#!/usr/bin/env python3
"""
Xiaotao WebDAV 同步脚本
功能：
1. 从 WebDAV 获取指定日期的 flashnote 和 journal
2. 使用 AI 服务合并或转换内容
3. 保存到本地并更新回 WebDAV

目录结构：
- Flashnote: /flashnotes/{year}/{year}{month}/{year}{month}{day}/{date}.jsonl
- Journal:   /All/Daily/{year}/{year}{month}/{year}{month}{day}/{date} 星期{x}.md
"""

import os
import sys
import json
import base64
import argparse
import logging
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, List, Dict, Any
from dataclasses import dataclass

import requests
from dotenv import load_dotenv


# =============================================================================
# 日志配置
# =============================================================================

def setup_logging(log_file: str = "xiaotao_sync.log") -> logging.Logger:
    """
    配置日志：同时输出到控制台和文件（每次覆盖）
    """
    logger = logging.getLogger("xiaotao_sync")
    logger.setLevel(logging.INFO)

    # 清除已有处理器（防止重复添加）
    logger.handlers.clear()

    # 格式化
    formatter = logging.Formatter(
        '[%(asctime)s] %(levelname)s: %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )

    # 文件处理器（覆盖模式）
    file_handler = logging.FileHandler(log_file, mode='w', encoding='utf-8')
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    # 控制台处理器
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    return logger


# =============================================================================
# 配置类
# =============================================================================

@dataclass
class WebDAVConfig:
    """WebDAV 配置"""
    server_url: str
    username: str
    password: str
    flashnote_dir: str = "/flashnotes/"
    finalnote_dir: str = "/All/Daily/"

    @property
    def is_configured(self) -> bool:
        return all([self.server_url, self.username, self.password])

    def normalize_dir(self, dir_path: str) -> str:
        """规范化目录路径"""
        if not dir_path.startswith('/'):
            dir_path = '/' + dir_path
        if not dir_path.endswith('/'):
            dir_path = dir_path + '/'
        return dir_path

    @property
    def flashnote_path(self) -> str:
        return self.normalize_dir(self.flashnote_dir).rstrip('/')

    @property
    def finalnote_path(self) -> str:
        return self.normalize_dir(self.finalnote_dir).rstrip('/')


@dataclass
class AIConfig:
    """AI 服务配置（DeepSeek API，OpenAI 兼容）"""
    api_key: str
    base_url: str = "https://api.deepseek.com/v1"
    model: str = "deepseek-v4-flash"

    @property
    def is_configured(self) -> bool:
        return bool(self.api_key)


# =============================================================================
# 工具函数
# =============================================================================

def get_weekday_name(date_str: str) -> str:
    """获取日期对应的星期几中文名"""
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    weekdays = ['一', '二', '三', '四', '五', '六', '日']
    return weekdays[dt.weekday()]


def format_date_dir(date_str: str) -> str:
    """将 YYYY-MM-DD 格式化为 YYYYMMDD"""
    return date_str.replace('-', '')


def get_flashnote_date_dir(config: WebDAVConfig, date_str: str) -> str:
    """获取闪记日期目录路径"""
    parts = date_str.split('-')
    year, month = parts[0], parts[1]
    date_dir = format_date_dir(date_str)
    return f"{config.flashnote_path}/{year}/{year}{month}/{date_dir}"


def get_flashnote_file_path(config: WebDAVConfig, date_str: str) -> str:
    """获取闪记 JSONL 文件路径"""
    return f"{get_flashnote_date_dir(config, date_str)}/{date_str}.jsonl"


def get_finalnote_date_dir(config: WebDAVConfig, date_str: str) -> str:
    """获取日志日期目录路径"""
    parts = date_str.split('-')
    year, month = parts[0], parts[1]
    date_dir = format_date_dir(date_str)
    return f"{config.finalnote_path}/{year}/{year}{month}/{date_dir}"


def get_finalnote_file_path(config: WebDAVConfig, date_str: str) -> str:
    """获取日志 Markdown 文件路径"""
    weekday = get_weekday_name(date_str)
    return f"{get_finalnote_date_dir(config, date_str)}/{date_str} 星期{weekday}.md"


def get_flashnote_backup_date_dir(config: WebDAVConfig, date_str: str) -> str:
    """获取闪记备份日期目录路径"""
    parts = date_str.split('-')
    year, month = parts[0], parts[1]
    date_dir = format_date_dir(date_str)
    return f"{config.flashnote_path}/backup/{year}/{year}{month}/{date_dir}"


def get_flashnote_backup_file_path(config: WebDAVConfig, date_str: str) -> str:
    """获取闪记备份文件路径"""
    weekday = get_weekday_name(date_str)
    return f"{get_flashnote_backup_date_dir(config, date_str)}/{date_str} 星期{weekday}.md"


# =============================================================================
# WebDAV 客户端
# =============================================================================

class WebDAVClient:
    """WebDAV 客户端"""

    def __init__(self, config: WebDAVConfig):
        self.config = config
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'XiaotaoSync/1.0',
            'Accept': '*/*',
        })

    def _get_auth(self) -> tuple:
        """获取认证信息"""
        return (self.config.username, self.config.password)

    def _get_full_url(self, path: str) -> str:
        """获取完整 URL"""
        base_url = self.config.server_url.rstrip('/')
        path = path.lstrip('/')
        return f"{base_url}/{path}"

    def test_connection(self) -> bool:
        """测试 WebDAV 连接"""
        try:
            url = self._get_full_url(self.config.flashnote_dir)
            response = self.session.request(
                'PROPFIND',
                url,
                auth=self._get_auth(),
                headers={'Depth': '0'},
                data='<?xml version="1.0" encoding="utf-8"?><propfind xmlns="DAV:"><prop><displayname/></prop></propfind>'
            )
            return response.status_code in [200, 207]
        except Exception as e:
            print(f"连接测试失败: {e}")
            return False

    def download_file(self, path: str) -> Optional[bytes]:
        """下载文件"""
        try:
            url = self._get_full_url(path)
            response = self.session.get(url, auth=self._get_auth())
            if response.status_code == 200:
                return response.content
            elif response.status_code == 404:
                return None
            else:
                print(f"下载失败: {response.status_code}")
                return None
        except Exception as e:
            print(f"下载错误: {e}")
            return None

    def upload_file(self, path: str, content: bytes) -> bool:
        """上传文件"""
        try:
            # 先确保目录存在
            dir_path = '/'.join(path.split('/')[:-1])
            self._ensure_directory(dir_path)

            url = self._get_full_url(path)
            response = self.session.put(
                url,
                auth=self._get_auth(),
                data=content,
                headers={'Content-Type': 'application/octet-stream'}
            )
            return response.status_code in [201, 204]
        except Exception as e:
            print(f"上传错误: {e}")
            return False

    def backup_file(self, source_path: str, backup_path: str) -> bool:
        """
        备份文件到指定路径

        Args:
            source_path: 源文件路径
            backup_path: 备份文件路径
        """
        try:
            # 1. 先下载源文件
            content = self.download_file(source_path)
            if content is None:
                print(f"  源文件不存在，无需备份: {source_path}")
                return True  # 文件不存在也算备份成功（无需备份）

            # 2. 确保备份目录存在
            dir_path = '/'.join(backup_path.split('/')[:-1])
            self._ensure_directory(dir_path)

            # 3. 上传备份文件
            url = self._get_full_url(backup_path)
            response = self.session.put(
                url,
                auth=self._get_auth(),
                data=content,
                headers={'Content-Type': 'application/octet-stream'}
            )
            return response.status_code in [201, 204]
        except Exception as e:
            print(f"  备份错误: {e}")
            return False

    def _ensure_directory(self, path: str):
        """确保目录存在（递归创建）"""
        parts = [p for p in path.split('/') if p]
        current_path = ''
        for part in parts:
            current_path += '/' + part
            self._create_directory(current_path)

    def _create_directory(self, path: str) -> bool:
        """创建目录"""
        try:
            url = self._get_full_url(path)
            response = self.session.request('MKCOL', url, auth=self._get_auth())
            return response.status_code in [201, 405]  # 405 表示已存在
        except Exception as e:
            return False

    def list_directory(self, path: str) -> List[Dict[str, Any]]:
        """列出目录内容"""
        try:
            url = self._get_full_url(path)
            response = self.session.request(
                'PROPFIND',
                url,
                auth=self._get_auth(),
                headers={'Depth': '1'},
                data='''<?xml version="1.0" encoding="utf-8"?>
<propfind xmlns="DAV:">
  <prop>
    <displayname/>
    <getcontentlength/>
    <getlastmodified/>
    <resourcetype/>
  </prop>
</propfind>'''
            )

            if response.status_code != 207:
                return []

            return self._parse_propfind(response.text)
        except Exception as e:
            print(f"列出目录错误: {e}")
            return []

    def _parse_propfind(self, xml_content: str) -> List[Dict[str, Any]]:
        """解析 PROPFIND 响应"""
        import xml.etree.ElementTree as ET

        files = []
        try:
            root = ET.fromstring(xml_content)
            for response in root.findall('.//{DAV:}response'):
                href = response.find('.//{DAV:}href')
                if href is None:
                    continue

                path = href.text
                name = path.rstrip('/').split('/')[-1] if path else ''

                # 跳过目录本身
                if path.endswith('/'):
                    continue

                propstat = response.find('.//{DAV:}propstat')
                if propstat is None:
                    continue

                prop = propstat.find('.//{DAV:}prop')
                if prop is None:
                    continue

                resourcetype = prop.find('.//{DAV:}resourcetype')
                is_directory = resourcetype is not None and resourcetype.find('.//{DAV:}collection') is not None

                size_elem = prop.find('.//{DAV:}getcontentlength')
                size = int(size_elem.text) if size_elem is not None and size_elem.text else None

                files.append({
                    'path': path,
                    'name': name,
                    'is_directory': is_directory,
                    'size': size
                })
        except Exception as e:
            print(f"解析 PROPFIND 响应失败: {e}")

        return files

    def file_exists(self, path: str) -> bool:
        """检查文件是否存在"""
        try:
            url = self._get_full_url(path)
            response = self.session.head(url, auth=self._get_auth())
            return response.status_code == 200
        except Exception:
            return False

    def download_images_from_dir(self, remote_dir: str, local_dir: Path, logger: logging.Logger) -> List[str]:
        """从远端目录下载所有图片到本地目录

        Args:
            remote_dir: 远端目录路径
            local_dir: 本地目录路径
            logger: 日志记录器

        Returns:
            下载的图片文件名列表
        """
        downloaded = []
        image_extensions = {'.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp', '.svg'}

        try:
            # 列出远端目录内容
            files = self.list_directory(remote_dir)

            for file_info in files:
                if file_info.get('is_directory'):
                    continue

                name = file_info.get('name', '')
                ext = Path(name).suffix.lower()

                if ext in image_extensions:
                    # 下载图片
                    remote_path = f"{remote_dir}/{name}" if not remote_dir.endswith('/') else f"{remote_dir}{name}"
                    content = self.download_file(remote_path)

                    if content:
                        local_path = local_dir / name
                        local_path.write_bytes(content)
                        downloaded.append(name)
                        logger.info(f"  下载图片: {name} ({len(content)} 字节)")

        except Exception as e:
            logger.error(f"下载图片失败: {e}")

        return downloaded


# =============================================================================
# AI 服务
# =============================================================================

class AIService:
    """AI 服务 - 调用 DeepSeek API（OpenAI 兼容）"""

    # Markdown 输出模板
    MARKDOWN_TEMPLATE = '''# 随记

## 琐事


## 我想


---

# 寸进

## 读书

## 沟通

## 健身

## 浮欢

## 研习'''

    # 分类规则
    CLASSIFICATION_RULES = '''
分类规则：
- 包含比较复杂的认知过程、趋势判断，人生思考 → 我想
- 出现读书，看了什么书籍之类 → 读书
- 出现开会、会议、各种形式的和人的交流 → 沟通
- 出现健身运动类内容 → 健身
- 出现"娱乐/放松" → 浮欢
- 出现"学习/技术深入"，出现各种开发，学新知识，研发行为 → 研习
- 其他琐碎事项 → 琐事
举例：
- 公共软件BMT 这种会议显然属于沟通
- 吃饭了，食物，喝咖啡了，和人快乐聊天，娱乐活动显然属于浮欢
- "看看落日！"-->应该归入浮欢
- "人生切割术第二季第四集太厉害了"-->看电视，应该归入浮欢
- "股票继续跌，跌不停。眼看这个月又亏损了，难受。"-->如此应该归入琐事
- "所谓前端已死也是很奇怪。现在AI无法自动化，难以无监督做好的反而是界面的工作。"--> 归入我想
- 如内容里有工作上和某人沟通的，明显非娱乐非生活类的，归入沟通。
'''

    # 公共任务规则
    COMMON_TASK_RULES = '''
3. **重要：保留闪记中的所有图片引用（![[...]] 格式），不要修改图片文件名，将它们放在对应的文字内容后面。**
4. 除了原有的输入内容和前面所述的必要修改外，尽量避免添加别的文字。
5. 注意已有的三级子章节。完全不同类的内容应该避免归入同样的子章节，可以尝试另拟子章节题目。
6. 输出最终的完整 Markdown 内容，保持原有日志的格式和结构。'''

    # 转换场景的 systemPrompt
    CONVERT_SYSTEM_PROMPT = f'''你是一个"个人碎片化思考整理助手"。

你的任务是：
1. 读取 jsonl 内容，里面是流式的日志数据，同时引用了一些本目录下的图片（obsidian 语法 如 ![[1709251800000.jpg]]）；
2. 按 Markdown 模板生成日报；
{COMMON_TASK_RULES}

输出结构必须严格为 Markdown：{MARKDOWN_TEMPLATE}
不要删除模板的各级标题，哪怕内容是空的
{CLASSIFICATION_RULES}
请根据闪记内容，生成符合模板的 markdown 日志。'''

    # 合并场景的 systemPrompt
    MERGE_SYSTEM_PROMPT = f'''你是一个"个人碎片化思考整理助手"。

你的任务是：
1. 你接收的输入里，一部分是 jsonl 内容（闪记数据，可能包含图片引用如 ![[1709251800000.jpg]]），
   另一部分是 markdown 内容（已有的日志，已按模板要求整理好）。
2. 先分析闪记内容，将闪记内容按照日志的模板结构归类合并到日志中，如果有相同文字内容则以既有日志为准，不要出现重复文字。
{COMMON_TASK_RULES}

输出结构必须严格为 Markdown：{MARKDOWN_TEMPLATE}

{CLASSIFICATION_RULES}
请保留原有日志中已经整理好的内容，不要动原来已有的归类结构，而是把闪记内容合并到对应分类中。'''

    def __init__(self, config: AIConfig):
        self.config = config

    def _call_api(self, system_prompt: str, user_content: str, max_retries: int = 3) -> str:
        """调用 DeepSeek API，带指数退避重试"""
        if not self.config.is_configured:
            raise Exception("AI 服务未配置")

        headers = {
            'Authorization': f'Bearer {self.config.api_key}',
            'Content-Type': 'application/json'
        }

        data = {
            'model': self.config.model,
            'messages': [
                {'role': 'system', 'content': system_prompt},
                {'role': 'user', 'content': user_content}
            ],
            'max_tokens': 4000
        }

        last_error = None
        for attempt in range(max_retries + 1):
            try:
                response = requests.post(
                    f"{self.config.base_url}/chat/completions",
                    headers=headers,
                    json=data,
                    timeout=120
                )

                # 429 (rate limit) 或 5xx 需要重试
                if response.status_code in [429, 500, 502, 503, 504]:
                    if attempt < max_retries:
                        wait = 2 ** attempt  # 1s, 2s, 4s
                        time.sleep(wait)
                        continue
                    else:
                        raise Exception(f"API 请求失败（已重试 {max_retries} 次）: HTTP {response.status_code}, {response.text}")

                response.raise_for_status()

                result = response.json()
                if 'choices' in result and len(result['choices']) > 0:
                    content = result['choices'][0]['message']['content']
                    return self._clean_markdown_output(content)
                else:
                    raise Exception("API 响应格式错误")
            except requests.exceptions.RequestException as e:
                last_error = e
                if attempt < max_retries:
                    wait = 2 ** attempt
                    time.sleep(wait)
                else:
                    raise Exception(f"API 请求失败（已重试 {max_retries} 次）: {e}")

        raise Exception(f"API 请求失败（已重试 {max_retries} 次）: {last_error}")

    def _clean_markdown_output(self, content: str) -> str:
        """清理 markdown 输出，去除多余的代码块标记"""
        content = content.strip()
        if content.startswith('```markdown'):
            content = content[11:]
        elif content.startswith('```'):
            content = content[3:]
        if content.endswith('```'):
            content = content[:-3]
        return content.strip()

    def convert_flashnote_to_markdown(self, flashnote_jsonl: str) -> str:
        """将闪记转换为 Markdown 格式"""
        user_content = f'闪记内容：\n{flashnote_jsonl}\n\n请根据以上内容生成 markdown 日志。'
        return self._call_api(self.CONVERT_SYSTEM_PROMPT, user_content)

    def merge_flashnote_with_finalnote(self, flashnote_jsonl: str, finalnote_markdown: str) -> str:
        """合并闪记和日志内容"""
        user_content = f'''原有日志内容：
{finalnote_markdown}

闪记内容：
{flashnote_jsonl}

请将闪记内容合并到日志中，输出完整的 markdown 日志。'''
        return self._call_api(self.MERGE_SYSTEM_PROMPT, user_content)


# =============================================================================
# 主程序
# =============================================================================

class XiaotaoSync:
    """Xiaotao 同步主类"""

    def __init__(self, webdav_config: WebDAVConfig, ai_config: AIConfig, local_dir: str, logger: logging.Logger):
        self.webdav = WebDAVClient(webdav_config)
        self.ai = AIService(ai_config)
        self.local_dir = Path(local_dir)
        self.webdav_config = webdav_config
        self.logger = logger

        # 创建本地目录结构
        self.local_dir.mkdir(parents=True, exist_ok=True)
        (self.local_dir / "flashnotes").mkdir(exist_ok=True)
        (self.local_dir / "finalnotes").mkdir(exist_ok=True)  # 包含原始日志（备份）和处理后日志

        self.logger.info(f"初始化完成，本地目录: {self.local_dir}")

    def sync_date(self, date_str: str, dry_run: bool = False) -> bool:
        """
        同步指定日期的数据

        Args:
            date_str: 日期格式 YYYY-MM-DD
            dry_run: 是否仅预览，不实际上传
        """
        self.logger.info(f"{'='*60}")
        self.logger.info(f"开始处理日期: {date_str}")
        self.logger.info(f"模式: {'模拟运行(dry-run)' if dry_run else '实际执行'}")
        self.logger.info(f"{'='*60}")

        print(f"\n{'='*60}")
        print(f"处理日期: {date_str}")
        print(f"{'='*60}")

        # 1. 获取 WebDAV 上的数据
        flashnote_path = get_flashnote_file_path(self.webdav_config, date_str)
        finalnote_path = get_finalnote_file_path(self.webdav_config, date_str)

        self.logger.info(f"[1/5] 从 WebDAV 获取数据")
        self.logger.info(f"  闪记路径: {flashnote_path}")
        self.logger.info(f"  日志路径: {finalnote_path}")

        print(f"\n[1/5] 从 WebDAV 获取数据...")
        flashnote_content = self._download_flashnote(flashnote_path)
        finalnote_content = self._download_finalnote(finalnote_path)

        flashnote_info = f"✓ ({len(flashnote_content)} 字符)" if flashnote_content else "✗ (未找到)"
        finalnote_info = f"✓ ({len(finalnote_content)} 字符)" if finalnote_content else "✗ (0 字符)"

        self.logger.info(f"  闪记: {flashnote_info}")
        self.logger.info(f"  日志: {finalnote_info}")

        print(f"  闪记: {flashnote_info}")
        print(f"  日志: {finalnote_info}")

        # 2. 保存到本地
        self.logger.info(f"[2/5] 保存到本地")
        print(f"\n[2/5] 保存到本地...")

        if flashnote_content:
            local_flashnote_path = self._save_flashnote_local(date_str, flashnote_content)
            self.logger.info(f"  闪记保存到: {local_flashnote_path}")
            print(f"  闪记保存到: {local_flashnote_path}")

        local_finalnote_path = None
        if finalnote_content:
            local_finalnote_path = self._save_finalnote_local(date_str, finalnote_content)
            self.logger.info(f"  日志保存到: {local_finalnote_path}")
            print(f"  日志保存到: {local_finalnote_path}")

        if flashnote_content:
            # 3. AI 处理
            self.logger.info(f"[3/5] AI 处理")
            print(f"\n[3/5] AI 处理...")

            try:
                if finalnote_content:
                    self.logger.info("  模式: 合并闪记到现有日志")
                    print("  模式: 合并闪记到现有日志")
                    result = self.ai.merge_flashnote_with_finalnote(flashnote_content, finalnote_content)
                else:
                    self.logger.info("  模式: 闪记转换为日志")
                    print("  模式: 闪记转换为日志")
                    result = self.ai.convert_flashnote_to_markdown(flashnote_content)

                self.logger.info(f"  处理完成: {len(result)} 字符")
                print(f"  处理完成: {len(result)} 字符")
            except Exception as e:
                self.logger.error(f"AI 处理失败: {e}")
                print(f"  AI 处理失败: {e}")
                return False

            # 4. 保存处理结果到本地
            self.logger.info(f"[4/5] 保存处理结果到本地")
            print(f"\n[4/5] 保存处理结果到本地...")

            processed_path = self._save_processed_local(date_str, result)
            self.logger.info(f"  保存到: {processed_path}")
            print(f"  保存到: {processed_path}")

            # 5. 上传回 WebDAV（如有现有日志，先备份）
            self.logger.info(f"[5/5] 上传回 WebDAV")

            if dry_run:
                self.logger.info("模拟模式，跳过上传和备份")
                self.logger.info(f"预览内容前 500 字符:\n{result[:500]}...")
                print(f"\n[5/5] 模拟模式，跳过上传和备份")
                print(f"  预览内容前 500 字符:")
                print(f"  {result[:500]}...")
            else:
                print(f"\n[5/5] 上传回 WebDAV...")
                upload_path = get_finalnote_file_path(self.webdav_config, date_str)
                self.logger.info(f"  上传路径: {upload_path}")

                # 5.1 如云端已有日志，先备份到 WebDAV
                # 本地 finalnotes/ 下的原始文件已是备份，无需重复保存
                if finalnote_content:
                    backup_path = get_flashnote_backup_file_path(self.webdav_config, date_str)
                    self.logger.info(f"  备份现有日志到: {backup_path}")
                    print(f"  备份现有日志到: {backup_path}")

                    backup_success = self.webdav.backup_file(upload_path, backup_path)
                    if backup_success:
                        self.logger.info(f"  备份成功（本地备份: {local_finalnote_path}）")
                        print(f"  备份成功（本地备份: {local_finalnote_path}）")
                    else:
                        self.logger.error("备份失败，跳过上传")
                        print(f"  备份失败，跳过上传")
                        return False

                # 5.2 上传新内容
                upload_success = self.webdav.upload_file(upload_path, result.encode('utf-8'))
                if upload_success:
                    self.logger.info(f"  上传成功: {upload_path}")
                    print(f"  上传成功: {upload_path}")

                    # 5.3 上传成功，用处理后的日志覆盖本地原日志
                    final_path = self._overwrite_finalnote_local(date_str, result)
                    self.logger.info(f"  本地日志已更新: {final_path}")
                    print(f"  本地日志已更新: {final_path}")
                else:
                    self.logger.error(f"上传失败: {upload_path}")
                    print(f"  上传失败")
                    return False
        else:
            self.logger.info("无闪记数据，跳过 AI 处理和上传")
            print("  无闪记数据，跳过 AI 处理和上传")

        # 6. 从远端日志目录下载图片到本地
        self.logger.info(f"[6/6] 下载远端日志目录图片")
        print(f"\n[6/6] 下载远端日志目录图片...")

        remote_finalnote_dir = get_finalnote_date_dir(self.webdav_config, date_str)
        local_finalnote_dir = self.local_dir / "finalnotes" / date_str.replace('-', '')[:4] / date_str.replace('-', '')[0:6] / date_str.replace('-', '')

        downloaded_images = self.webdav.download_images_from_dir(
            remote_finalnote_dir,
            local_finalnote_dir,
            self.logger
        )

        if downloaded_images:
            self.logger.info(f"  成功下载 {len(downloaded_images)} 张图片: {', '.join(downloaded_images)}")
            print(f"  成功下载 {len(downloaded_images)} 张图片: {', '.join(downloaded_images)}")
        else:
            self.logger.info("  远端日志目录没有图片")
            print("  远端日志目录没有图片")

        self.logger.info(f"{'='*60}")
        self.logger.info(f"处理完成: {date_str}")
        self.logger.info(f"{'='*60}")

        print(f"\n{'='*60}")
        print(f"处理完成: {date_str}")
        print(f"{'='*60}\n")
        return True

    def _download_flashnote(self, path: str) -> Optional[str]:
        """下载闪记内容"""
        content = self.webdav.download_file(path)
        if content:
            return content.decode('utf-8')
        return None

    def _download_finalnote(self, path: str) -> Optional[str]:
        """下载日志内容"""
        content = self.webdav.download_file(path)
        if content:
            return content.decode('utf-8')
        return None

    def _save_flashnote_local(self, date_str: str, content: str) -> Path:
        """保存闪记到本地"""
        dir_path = self.local_dir / "flashnotes" / date_str.replace('-', '')[:4] / date_str.replace('-', '')[0:6] / date_str.replace('-', '')
        dir_path.mkdir(parents=True, exist_ok=True)
        file_path = dir_path / f"{date_str}.jsonl"
        file_path.write_text(content, encoding='utf-8')
        return file_path

    def _save_finalnote_local(self, date_str: str, content: str) -> Path:
        """保存原始日志到本地"""
        dir_path = self.local_dir / "finalnotes" / date_str.replace('-', '')[:4] / date_str.replace('-', '')[0:6] / date_str.replace('-', '')
        dir_path.mkdir(parents=True, exist_ok=True)
        weekday = get_weekday_name(date_str)
        file_path = dir_path / f"{date_str} 星期{weekday}.md"
        file_path.write_text(content, encoding='utf-8')
        return file_path

    def _save_processed_local(self, date_str: str, content: str) -> Path:
        """保存处理后的日志到本地（临时文件，上传成功后会覆盖最终文件）"""
        dir_path = self.local_dir / "finalnotes" / date_str.replace('-', '')[:4] / date_str.replace('-', '')[0:6] / date_str.replace('-', '')
        dir_path.mkdir(parents=True, exist_ok=True)
        weekday = get_weekday_name(date_str)
        file_path = dir_path / f"{date_str} 星期{weekday}_processed.md"
        file_path.write_text(content, encoding='utf-8')
        return file_path

    def _overwrite_finalnote_local(self, date_str: str, content: str) -> Path:
        """上传成功后，用处理后的内容覆盖本地最终日志文件"""
        dir_path = self.local_dir / "finalnotes" / date_str.replace('-', '')[:4] / date_str.replace('-', '')[0:6] / date_str.replace('-', '')
        dir_path.mkdir(parents=True, exist_ok=True)
        weekday = get_weekday_name(date_str)
        final_path = dir_path / f"{date_str} 星期{weekday}.md"
        final_path.write_text(content, encoding='utf-8')

        # 删除临时的 _processed 文件
        processed_path = dir_path / f"{date_str} 星期{weekday}_processed.md"
        if processed_path.exists():
            processed_path.unlink()

        return final_path


# =============================================================================
# 命令行入口
# =============================================================================

def load_config(args) -> tuple:
    """从 .env 加载配置

    本地目录优先级：环境变量 > 命令行参数 > 默认值
    """
    # 尝试加载 .env 文件
    env_paths = [
        Path('.env'),
        Path.home() / '.xiaotao' / '.env',
        Path(__file__).parent / '.env'
    ]

    for env_path in env_paths:
        if env_path.exists():
            load_dotenv(env_path)
            print(f"已加载配置: {env_path}")
            break

    # WebDAV 配置
    webdav_config = WebDAVConfig(
        server_url=os.getenv('WEBDAV_URL', ''),
        username=os.getenv('WEBDAV_USERNAME', ''),
        password=os.getenv('WEBDAV_PASSWORD', ''),
        flashnote_dir=os.getenv('WEBDAV_FLASHNOTE_DIR', '/flashnotes/'),
        finalnote_dir=os.getenv('WEBDAV_FINALNOTE_DIR', '/All/Daily/')
    )

    # AI 配置（DeepSeek API）
    ai_config = AIConfig(
        api_key=os.getenv('AI_API_KEY', ''),
        base_url=os.getenv('AI_BASE_URL', 'https://api.deepseek.com/v1'),
        model=os.getenv('AI_MODEL', 'deepseek-v4-flash')
    )

    # 本地数据目录（优先级：环境变量 > 命令行参数 > 默认值）
    local_data_dir = os.getenv('LOCAL_DATA_DIR', args.local_dir)

    return webdav_config, ai_config, local_data_dir


def validate_date(date_str: str) -> bool:
    """验证日期格式"""
    try:
        datetime.strptime(date_str, "%Y-%m-%d")
        return True
    except ValueError:
        return False


def main():
    parser = argparse.ArgumentParser(
        description='Xiaotao WebDAV 同步工具 - 将闪记转换为/合并到日志',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='''
示例:
  # 同步今天的数据
  uv run python xiaotao_sync.py

  # 同步指定日期
  uv run python xiaotao_sync.py --date 2026-05-13

  # 同步日期范围
  uv run python xiaotao_sync.py --from-date 2026-05-01 --to-date 2026-05-13

  # 仅预览，不上传
  uv run python xiaotao_sync.py --date 2026-05-13 --dry-run

  # 指定本地存储目录
  python xiaotao_sync.py --local-dir ./my_notes

  # 指定日志文件路径
  python xiaotao_sync.py --log-file ./logs/sync.log

环境变量 (或 .env 文件):
  WEBDAV_URL          WebDAV 服务器地址
  WEBDAV_USERNAME     WebDAV 用户名
  WEBDAV_PASSWORD     WebDAV 密码
  WEBDAV_FLASHNOTE_DIR 闪记目录 (默认: /flashnotes/)
  WEBDAV_FINALNOTE_DIR  日志目录 (默认: /All/Daily/)
  AI_API_KEY          DeepSeek API Key
  AI_BASE_URL         DeepSeek API 基础 URL (默认: https://api.deepseek.com/v1)
  AI_MODEL            DeepSeek 模型 (默认: deepseek-v4-flash，推理可用 deepseek-v4-pro)
        '''
    )

    parser.add_argument('--date', '-d', type=str, help='指定日期 (YYYY-MM-DD)，默认为今天')
    parser.add_argument('--from-date', type=str, help='起始日期 (YYYY-MM-DD)')
    parser.add_argument('--to-date', type=str, help='结束日期 (YYYY-MM-DD)')
    parser.add_argument('--local-dir', '-l', type=str, default='./xiaotao_data',
                        help='本地存储目录 (默认: ./xiaotao_data)')
    parser.add_argument('--dry-run', action='store_true',
                        help='仅预览，不上传到 WebDAV')
    parser.add_argument('--test-connection', action='store_true',
                        help='测试 WebDAV 连接')
    parser.add_argument('--log-file', type=str, default='xiaotao_sync.log',
                        help='日志文件路径 (默认: xiaotao_sync.log，每次执行覆盖)')

    args = parser.parse_args()

    # 配置日志
    logger = setup_logging(args.log_file)
    logger.info("="*60)
    logger.info("Xiaotao WebDAV 同步工具启动")
    logger.info(f"日志文件: {args.log_file}")
    logger.info("="*60)

    # 加载配置
    webdav_config, ai_config, local_data_dir = load_config(args)
    logger.info(f"WebDAV URL: {webdav_config.server_url}")
    logger.info(f"AI 模型: {ai_config.model}")

    # 验证配置
    if not webdav_config.is_configured:
        logger.error("WebDAV 配置不完整")
        print("错误: WebDAV 配置不完整，请设置以下环境变量:")
        print("  WEBDAV_URL, WEBDAV_USERNAME, WEBDAV_PASSWORD")
        sys.exit(1)

    if not ai_config.is_configured:
        logger.error("AI 配置不完整")
        print("错误: AI 配置不完整，请设置以下环境变量:")
        print("  AI_API_KEY")
        sys.exit(1)

    # 创建同步器（传入logger）
    sync = XiaotaoSync(webdav_config, ai_config, local_data_dir, logger)

    # 测试连接
    if args.test_connection:
        print("测试 WebDAV 连接...")
        if sync.webdav.test_connection():
            print("连接成功!")
            sys.exit(0)
        else:
            print("连接失败!")
            sys.exit(1)

    # 确定日期范围
    dates_to_process = []

    if args.date:
        if not validate_date(args.date):
            print(f"错误: 无效的日期格式: {args.date}，应为 YYYY-MM-DD")
            sys.exit(1)
        dates_to_process = [args.date]
    elif args.from_date or args.to_date:
        from_date = args.from_date or datetime.now().strftime('%Y-%m-%d')
        to_date = args.to_date or datetime.now().strftime('%Y-%m-%d')

        if not validate_date(from_date) or not validate_date(to_date):
            print("错误: 无效的日期格式，应为 YYYY-MM-DD")
            sys.exit(1)

        # 生成日期范围
        start = datetime.strptime(from_date, '%Y-%m-%d')
        end = datetime.strptime(to_date, '%Y-%m-%d')

        if start > end:
            print("错误: 起始日期不能晚于结束日期")
            sys.exit(1)

        current = start
        while current <= end:
            dates_to_process.append(current.strftime('%Y-%m-%d'))
            current += timedelta(days=1)
    else:
        # 默认处理今天
        dates_to_process = [datetime.now().strftime('%Y-%m-%d')]

    logger.info(f"准备处理 {len(dates_to_process)} 天的数据")
    logger.info(f"本地目录: {local_data_dir}")
    logger.info(f"模式: {'模拟模式' if args.dry_run else '正常模式'}")

    print(f"\n准备处理 {len(dates_to_process)} 天的数据")
    print(f"本地目录: {local_data_dir}")
    print(f"{'模拟模式' if args.dry_run else '正常模式'}\n")

    # 处理每个日期
    success_count = 0
    for date_str in dates_to_process:
        if sync.sync_date(date_str, dry_run=args.dry_run):
            success_count += 1

    logger.info(f"{'='*60}")
    logger.info(f"执行完成: {success_count}/{len(dates_to_process)} 成功")
    logger.info(f"{'='*60}")

    print(f"\n{'='*60}")
    print(f"总计: {success_count}/{len(dates_to_process)} 成功")
    print(f"{'='*60}")

    if success_count < len(dates_to_process):
        logger.error(f"部分日期处理失败")
        sys.exit(1)
    else:
        logger.info("所有日期处理成功")


if __name__ == '__main__':
    main()
