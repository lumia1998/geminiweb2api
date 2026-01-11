"""
Gemini Web Reverse Engineering Client
支持图文请求、上下文对话，OpenAI 格式输入输出
手动配置 token，无需代码登录
"""

import re
import json
import random
import string
import base64
import uuid
import httpx
from typing import Optional, List, Dict, Any, Union
from dataclasses import dataclass, field
from datetime import datetime
import time


class CookieExpiredError(Exception):
    """Cookie 过期或无效异常"""
    pass


class ImageUploadError(Exception):
    """图片上传失败异常"""
    pass


@dataclass
class Message:
    """OpenAI 格式消息"""
    role: str
    content: Union[str, List[Dict[str, Any]]]


@dataclass
class ChatCompletionChoice:
    index: int
    message: Message
    finish_reason: str = "stop"


@dataclass
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


@dataclass
class ChatCompletionResponse:
    """OpenAI 格式响应"""
    id: str
    object: str = "chat.completion"
    created: int = 0
    model: str = "gemini-web"
    choices: List[ChatCompletionChoice] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "object": self.object,
            "created": self.created,
            "model": self.model,
            "choices": [
                {
                    "index": c.index,
                    "message": {"role": c.message.role, "content": c.message.content},
                    "finish_reason": c.finish_reason
                }
                for c in self.choices
            ],
            "usage": {
                "prompt_tokens": self.usage.prompt_tokens,
                "completion_tokens": self.usage.completion_tokens,
                "total_tokens": self.usage.total_tokens
            }
        }


class GeminiClient:
    """
    Gemini 网页版逆向客户端
    
    使用方法:
    1. 打开 https://gemini.google.com 并登录
    2. F12 打开开发者工具 -> Application -> Cookies
    3. 复制以下 cookie 值:
       - __Secure-1PSID
       - __Secure-1PSIDTS (可选)
    4. Network 标签 -> 找任意请求 -> 复制 SNlM0e 值 (在页面源码中搜索)
    """
    
    BASE_URL = "https://gemini.google.com"
    
    def __init__(
        self,
        secure_1psid: str,
        secure_1psidts: str = None,
        secure_1psidcc: str = None,
        snlm0e: str = None,
        bl: str = None,
        cookies_str: str = None,
        push_id: str = None,
        model_ids: dict = None,
        debug: bool = False,
        media_base_url: str = None,
        image_mode: str = "url",
        proxy_url: str = None,
    ):
        """
        初始化客户端 - 手动填写 token
        
        Args:
            secure_1psid: __Secure-1PSID cookie (必填)
            secure_1psidts: __Secure-1PSIDTS cookie (推荐)
            secure_1psidcc: __Secure-1PSIDCC cookie (推荐)
            snlm0e: SNlM0e token (必填，从页面源码获取)
            bl: BL 版本号 (可选，自动获取)
            cookies_str: 完整 cookie 字符串 (可选，替代单独设置)
            push_id: Push ID for image upload (必填用于图片上传)
            model_ids: 模型 ID 映射 {"flash": "xxx", "pro": "xxx", "thinking": "xxx"}
            debug: 是否打印调试信息
            media_base_url: 媒体文件的基础 URL (如 http://localhost:8000)，用于构建完整的媒体访问 URL
            image_mode: 图片返回模式 ("url" 或 "base64")
            proxy_url: 代理 URL (如 http://127.0.0.1:7890)
        """
        self.secure_1psid = secure_1psid
        self.secure_1psidts = secure_1psidts
        self.secure_1psidcc = secure_1psidcc
        self.snlm0e = snlm0e
        self.bl = bl
        self.push_id = push_id
        self.debug = debug
        self.media_base_url = media_base_url or ""
        self.image_mode = image_mode  # "url" 或 "base64"
        # 代理URL：空字符串视为None，避免httpx解析空URL报错
        self.proxy_url = proxy_url if proxy_url else None
        
        # 模型 ID 映射 (用于请求头选择模型)
        self.model_ids = model_ids or {
            "flash": "56fdd199312815e2",
            "pro": "e6fa609c3fa255c0",
            "thinking": "e051ce1aa80aa576",
        }
        
        # 配置代理
        if self.debug and proxy_url:
            print(f"[DEBUG] 使用代理: {proxy_url}")
        
        self.session = httpx.Client(
            timeout=1220.0,
            follow_redirects=True,
            proxy=self.proxy_url,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
                "Origin": self.BASE_URL,
                "Referer": f"{self.BASE_URL}/",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            },
        )
        
        # 设置 cookies
        if cookies_str:
            self._set_cookies_from_string(cookies_str)
        else:
            self.session.cookies.set("__Secure-1PSID", secure_1psid, domain=".google.com")
            if secure_1psidts:
                self.session.cookies.set("__Secure-1PSIDTS", secure_1psidts, domain=".google.com")
            if secure_1psidcc:
                self.session.cookies.set("__Secure-1PSIDCC", secure_1psidcc, domain=".google.com")
        
        # 会话上下文
        self.conversation_id: str = ""
        self.response_id: str = ""
        self.choice_id: str = ""
        self.request_count: int = 0
        
        # 消息历史
        self.messages: List[Message] = []
        
        # 验证必填参数
        if not self.snlm0e:
            raise ValueError(
                "SNlM0e 是必填参数！\n"
                "获取方法:\n"
                "1. 打开 https://gemini.google.com 并登录\n"
                "2. F12 -> 查看页面源代码 (Ctrl+U)\n"
                "3. 搜索 'SNlM0e' 找到类似: \"SNlM0e\":\"xxxxxx\"\n"
                "4. 复制引号内的值"
            )
        
        # 自动获取 bl
        if not self.bl:
            self._fetch_bl()
    
    def _set_cookies_from_string(self, cookies_str: str):
        """从完整 cookie 字符串解析"""
        for item in cookies_str.split(";"):
            item = item.strip()
            if "=" in item:
                key, value = item.split("=", 1)
                self.session.cookies.set(key.strip(), value.strip(), domain=".google.com")
    
    def _fetch_bl(self):
        """获取 BL 版本号"""
        try:
            resp = self.session.get(self.BASE_URL)
            match = re.search(r'"cfb2h":"([^"]+)"', resp.text)
            if match:
                self.bl = match.group(1)
            else:
                # 使用默认值
                self.bl = "boq_assistant-bard-web-server_20241209.00_p0"
            if self.debug:
                print(f"[DEBUG] BL: {self.bl}")
        except Exception as e:
            self.bl = "boq_assistant-bard-web-server_20241209.00_p0"
            if self.debug:
                print(f"[DEBUG] 获取 BL 失败，使用默认值: {e}")


    
    def _parse_content(self, content: Union[str, List[Dict]]) -> tuple:
        """解析 OpenAI 格式 content，返回 (text, images)"""
        if isinstance(content, str):
            return content, []
        
        text_parts = []
        images = []
        
        for item in content:
            if item.get("type") == "text":
                text_parts.append(item.get("text", ""))
            elif item.get("type") == "image_url":
                # 支持两种格式: {"url": "..."} 或直接字符串
                image_url_data = item.get("image_url", {})
                if isinstance(image_url_data, str):
                    url = image_url_data
                else:
                    url = image_url_data.get("url", "")
                
                if not url:
                    continue
                    
                if url.startswith("data:"):
                    # base64 格式: data:image/png;base64,xxxxx
                    match = re.match(r'data:([^;]+);base64,(.+)', url)
                    if match:
                        images.append({"mime_type": match.group(1), "data": match.group(2)})
                elif url.startswith("http://") or url.startswith("https://"):
                    # URL 格式，下载图片
                    try:
                        resp = httpx.get(url, timeout=30)
                        if resp.status_code == 200:
                            mime = resp.headers.get("content-type", "image/jpeg").split(";")[0]
                            images.append({"mime_type": mime, "data": base64.b64encode(resp.content).decode()})
                    except Exception as e:
                        if self.debug:
                            print(f"[DEBUG] 下载图片失败: {e}")
                else:
                    # 可能是纯 base64 字符串 (没有 data: 前缀)
                    try:
                        # 尝试解码验证是否是有效 base64
                        base64.b64decode(url[:100])  # 只验证前100字符
                        images.append({"mime_type": "image/png", "data": url})
                    except:
                        pass
        
        return " ".join(text_parts) if text_parts else "", images
    
    def _upload_image(self, image_data: bytes, mime_type: str = "image/jpeg") -> str:
        """
        上传图片到 Gemini 服务器
        
        Args:
            image_data: 图片二进制数据
            mime_type: 图片 MIME 类型
            
        Returns:
            str: 上传后的图片路径（带 token）
        """
        if not self.push_id:
            raise CookieExpiredError(
                "图片上传需要 push_id\n"
                "获取方法: 运行 python get_push_id.py 或从浏览器 Network 中获取"
            )
        
        try:
            upload_url = "https://push.clients6.google.com/upload/"
            filename = f"image_{random.randint(100000, 999999)}.png"
            
            # 浏览器必需的头
            browser_headers = {
                "accept": "*/*",
                "accept-language": "zh-CN,zh;q=0.9,en;q=0.8",
                "origin": "https://gemini.google.com",
                "referer": "https://gemini.google.com/",
                "sec-fetch-dest": "empty",
                "sec-fetch-mode": "cors",
                "sec-fetch-site": "same-site",
                "x-browser-channel": "stable",
                "x-browser-copyright": "Copyright 2025 Google LLC. All Rights reserved.",
                "x-browser-validation": "Aj9fzfu+SaGLBY9Oqr3S7RokOtM=",
                "x-browser-year": "2025",
                "x-client-data": "CIa2yQEIpbbJAQipncoBCNvaygEIk6HLAQiFoM0BCJaMzwEIkZHPAQiSpM8BGOyFzwEYsobPAQ==",
            }
            
            # 第一步：获取 upload_id
            init_headers = {
                **browser_headers,
                "content-type": "application/x-www-form-urlencoded;charset=utf-8",
                "push-id": self.push_id,
                "x-goog-upload-command": "start",
                "x-goog-upload-header-content-length": str(len(image_data)),
                "x-goog-upload-protocol": "resumable",
                "x-tenant-id": "bard-storage",
            }
            
            init_resp = self.session.post(upload_url, data={"File name": filename}, headers=init_headers)
            
            if self.debug:
                print(f"[DEBUG] 初始化上传状态: {init_resp.status_code}")
            
            # 检查初始化响应状态
            if init_resp.status_code == 401 or init_resp.status_code == 403:
                raise CookieExpiredError(
                    f"Cookie 已过期或无效 (HTTP {init_resp.status_code})\n"
                    "请重新获取以下信息:\n"
                    "1. __Secure-1PSID\n"
                    "2. __Secure-1PSIDTS\n"
                    "3. SNlM0e\n"
                    "4. push_id"
                )
            
            upload_id = init_resp.headers.get("x-guploader-uploadid")
            if not upload_id:
                raise CookieExpiredError(
                    f"未获取到 upload_id (状态码: {init_resp.status_code})\n"
                    "可能原因: Cookie 已过期，请重新获取所有 token"
                )
            
            if self.debug:
                print(f"[DEBUG] Upload ID: {upload_id[:50]}...")
            
            # 第二步：上传图片数据
            final_upload_url = f"{upload_url}?upload_id={upload_id}&upload_protocol=resumable"
            
            upload_headers = {
                **browser_headers,
                "content-type": "application/x-www-form-urlencoded;charset=utf-8",
                "push-id": self.push_id,
                "x-goog-upload-command": "upload, finalize",
                "x-goog-upload-offset": "0",
                "x-tenant-id": "bard-storage",
                "x-client-pctx": "CgcSBWjK7pYx",
            }
            
            upload_resp = self.session.post(
                final_upload_url,
                headers=upload_headers,
                content=image_data
            )
            
            if self.debug:
                print(f"[DEBUG] 上传数据状态: {upload_resp.status_code}")
                print(f"[DEBUG] 响应头: {dict(upload_resp.headers)}")
                print(f"[DEBUG] 响应内容完整: {upload_resp.text}")
            
            # 检查上传响应状态
            if upload_resp.status_code == 401 or upload_resp.status_code == 403:
                raise CookieExpiredError(
                    f"上传图片认证失败 (HTTP {upload_resp.status_code})\n"
                    "Cookie 已过期，请重新获取"
                )
            
            if upload_resp.status_code != 200:
                raise Exception(f"上传图片数据失败: {upload_resp.status_code}, 响应: {upload_resp.text[:200] if upload_resp.text else '(empty)'}")
            
            # 从响应中提取图片路径
            response_text = upload_resp.text
            image_path = None
            
            # 尝试解析 JSON
            try:
                response_json = json.loads(response_text)
                image_path = self._extract_image_path(response_json)
            except json.JSONDecodeError:
                # 如果不是 JSON，尝试从文本中提取路径
                match = re.search(r'/contrib_service/[^\s"\']+', response_text)
                if match:
                    image_path = match.group(0)
            
            # 验证图片路径完整性
            if not image_path:
                raise CookieExpiredError(
                    f"无法从响应中提取图片路径\n"
                    f"响应内容: {response_text[:300]}\n"
                    "可能原因: Cookie 已过期，请重新获取所有 token"
                )
            
            # 检查路径是否有效（长度足够即可，新版可能不带查询参数）
            if "/contrib_service/" in image_path:
                # 路径长度至少要有一定长度才是有效的
                if len(image_path) < 40:
                    raise CookieExpiredError(
                        f"图片路径不完整\n"
                        f"返回路径: {image_path}\n"
                        "原因: Cookie 已过期或权限不足\n"
                        "解决方法:\n"
                        "1. 重新登录 https://gemini.google.com\n"
                        "2. 更新 config.py 中的所有 token:\n"
                        "   - SECURE_1PSID\n"
                        "   - SECURE_1PSIDTS\n"
                        "   - SNLM0E\n"
                        "   - PUSH_ID"
                    )
            
            if self.debug:
                print(f"[DEBUG] 图片路径: {image_path}")
            
            return image_path
            
        except CookieExpiredError:
            raise
        except Exception as e:
            if self.debug:
                print(f"[DEBUG] 上传失败: {e}")
            raise Exception(f"图片上传失败: {e}")
    
    def _extract_image_path(self, data: Any) -> str:
        """从响应数据中递归提取图片路径"""
        if isinstance(data, str):
            if data.startswith("/contrib_service/"):
                return data
        elif isinstance(data, dict):
            for value in data.values():
                result = self._extract_image_path(value)
                if result:
                    return result
        elif isinstance(data, list):
            for item in data:
                result = self._extract_image_path(item)
                if result:
                    return result
        return None
    
    def _build_request_data(self, text: str, images: List[Dict] = None, image_paths: List[str] = None, model: str = None) -> str:
        """构建请求数据 - 基于真实请求格式"""
        # 会话上下文 (空字符串表示新对话)
        conv_id = self.conversation_id or ""
        resp_id = self.response_id or ""
        choice_id = self.choice_id or ""
        
        # 处理图片数据 - 格式: [[[path, 1, null, mime_type], filename]]
        image_data = None
        if image_paths and len(image_paths) > 0:
            path = image_paths[0]
            mime_type = images[0]["mime_type"] if images else "image/png"
            filename = f"image_{random.randint(100000, 999999)}.png"
            # 构建图片数组结构
            image_data = [[[path, 1, None, mime_type], filename]]
        
        # 生成唯一会话 ID
        session_id = str(uuid.uuid4()).upper()
        timestamp = int(time.time() * 1000)
        
        # 模型映射: 将模型名称转换为 Gemini 内部模型标识
        # [[0]] = gemini-3.0-pro (Pro 版)
        # [[1]] = gemini-3.0-flash (快速版，默认)
        # [[3]] = gemini-3.0-flash-thinking (思考版)
        model_code = [[1]]  # 默认快速版
        if model:
            model_lower = model.lower()
            if "pro" in model_lower:
                model_code = [[0]]  # Pro 版
            elif "thinking" in model_lower or "think" in model_lower:
                model_code = [[3]]  # 思考版
            # flash 或其他情况保持默认 [[1]]
        
        # 构建内部 JSON 数组 (基于真实请求格式)
        # 第一个元素: [text, 0, null, image_data, null, null, 0]
        inner_data = [
            [text, 0, None, image_data, None, None, 0],
            ["zh-CN"],
            [conv_id, resp_id, choice_id, None, None, None, None, None, None, ""],
            self.snlm0e,
            None,  # 之前是 "test123"，改为 null
            None,
            [1],
            1,
            None,
            None,
            1,
            0,
            None,
            None,
            None,
            None,
            None,
            model_code,  # 模型选择字段
            0,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            1,
            None,
            None,
            [4],
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            [1],
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            0,
            None,
            None,
            None,
            None,
            None,
            session_id,
            None,
            [],
            None,
            None,
            None,
            None,
            [timestamp // 1000, (timestamp % 1000) * 1000000]
        ]
        
        # 序列化为 JSON 字符串
        inner_json = json.dumps(inner_data, ensure_ascii=False, separators=(',', ':'))
        
        # 外层包装
        outer_data = [None, inner_json]
        f_req_value = json.dumps(outer_data, ensure_ascii=False, separators=(',', ':'))
        
        return f_req_value

    
    def _parse_response(self, response_text: str) -> str:
        """解析响应文本 - 修复版"""
        try:
            # 跳过前缀并按行解析
            lines = response_text.split("\n")
            final_text = ""
            generated_images_set = set()  # 使用 set 全局去重
            last_inner_json = None  # 保存最后一个有效的 inner_json 用于调试
            
            for line in lines:
                line = line.strip()
                if not line or line.startswith(")]}'"):
                    continue
                
                # 跳过数字行（长度标记）
                if line.isdigit():
                    continue
                
                try:
                    data = json.loads(line)
                    # data 是一个嵌套数组，data[0] 才是真正的数据
                    if isinstance(data, list) and len(data) > 0 and isinstance(data[0], list):
                        actual_data = data[0]
                        # 检查是否是 wrb.fr 响应
                        if len(actual_data) >= 3 and actual_data[0] == "wrb.fr" and actual_data[2]:
                            inner_json = json.loads(actual_data[2])
                            last_inner_json = inner_json
                            
                            # 尝试提取生成的图片 URL，合并到全局 set 中去重
                            imgs = self._extract_generated_images(inner_json)
                            if imgs:
                                for img in imgs:
                                    generated_images_set.add(img)
                                if self.debug:
                                    print(f"[DEBUG] 从响应中提取到 {len(imgs)} 个图片 URL，当前总数: {len(generated_images_set)}")
                            
                            # 提取文本内容
                            if inner_json and len(inner_json) > 4 and inner_json[4]:
                                candidates = inner_json[4]
                                if candidates and len(candidates) > 0:
                                    candidate = candidates[0]
                                    if candidate and len(candidate) > 1 and candidate[1]:
                                        # candidate[1] 是一个数组，第一个元素是文本
                                        text = candidate[1][0] if isinstance(candidate[1], list) else candidate[1]
                                        if isinstance(text, str) and len(text) > len(final_text):
                                            final_text = text
                                            # 更新会话上下文
                                            if len(inner_json) > 1 and inner_json[1]:
                                                if isinstance(inner_json[1], list):
                                                    if len(inner_json[1]) > 0:
                                                        self.conversation_id = inner_json[1][0] or self.conversation_id
                                                    if len(inner_json[1]) > 1:
                                                        self.response_id = inner_json[1][1] or self.response_id
                                            if len(candidate) > 0:
                                                self.choice_id = candidate[0] or self.choice_id
                except Exception as e:
                    if self.debug:
                        print(f"[DEBUG] 解析行时出错: {e}")
                    continue
            
            # 转换为列表
            generated_images = list(generated_images_set)
            
            if self.debug:
                print(f"[DEBUG] 解析完成: final_text长度={len(final_text)}, 图片数量={len(generated_images)}")
            
            # 处理生成的图片/视频 - 下载并缓存到本地
            if generated_images:
                if self.debug:
                    print(f"[DEBUG] 提取到 {len(generated_images)} 个媒体 URL，开始下载...")
                
                # 下载图片并获取本地代理 URL
                local_media_urls = []
                for i, url in enumerate(generated_images):
                    if self.debug:
                        print(f"[DEBUG] 下载媒体 {i+1}/{len(generated_images)}: {url[:80]}...")
                    local_url = self._download_media_as_data_url(url)
                    if local_url:
                        local_media_urls.append(local_url)
                        if self.debug:
                            print(f"[DEBUG] 媒体 {i+1} 下载成功: {local_url}")
                    else:
                        # 下载失败，使用原始 URL
                        local_media_urls.append(url)
                        if self.debug:
                            print(f"[DEBUG] 媒体 {i+1} 下载失败，使用原始 URL")
                
                # 检测占位符（如果有文本的话）
                has_placeholder = False
                if final_text:
                    has_placeholder = ('image_generation_content' in final_text or 
                                       'video_gen_chip' in final_text)
                
                # 构建包含本地代理 URL 的响应
                media_parts = []
                for i, url in enumerate(local_media_urls):
                    media_parts.append(f"![生成的内容 {i+1}]({url})")
                
                media_text = "\n\n".join(media_parts)
                
                if has_placeholder:
                    # 移除占位符 URL
                    cleaned_text = re.sub(r'https?://googleusercontent\.com/(?:image_generation_content|video_gen_chip)/\d+', '', final_text)
                    cleaned_text = re.sub(r'http://googleusercontent\.com/(?:image_generation_content|video_gen_chip)/\d+', '', cleaned_text)
                    cleaned_text = re.sub(r'!\[.*?\]\(\)', '', cleaned_text)  # 移除空的图片标记
                    cleaned_text = cleaned_text.strip()
                    if cleaned_text:
                        final_text = cleaned_text + "\n\n" + media_text
                    else:
                        final_text = media_text
                elif final_text:
                    # 有文本但没有占位符，追加图片
                    final_text = final_text + "\n\n" + media_text
                else:
                    # 没有文本，只有图片
                    final_text = media_text
                
                if self.debug:
                    print(f"[DEBUG] 媒体处理完成，成功下载 {len([u for u in local_media_urls if u.startswith('/media/')])} 个")
            
            # 检测视频生成占位符，替换为提示文案
            is_video_generation = False
            if final_text and 'video_gen_chip' in final_text:
                is_video_generation = True
            
            # 清理文本中的占位符 URL 和用户上传图片的 URL
            if final_text:
                # 清理占位符 URL
                final_text = re.sub(r'https?://googleusercontent\.com/(?:image_generation_content|video_gen_chip)/\d+\s*', '', final_text)
                final_text = re.sub(r'http://googleusercontent\.com/(?:image_generation_content|video_gen_chip)/\d+\s*', '', final_text)
                # 清理用户上传图片的 URL（/gg/ 路径，非 /gg-dl/）
                final_text = re.sub(r'!\[[^\]]*\]\(https://[^)]*googleusercontent\.com/gg/[^)]+\)', '', final_text)
                final_text = re.sub(r'https://lh3\.googleusercontent\.com/gg/[^\s\)]+', '', final_text)
                final_text = final_text.strip()
            
            # 如果是视频生成，添加提示文案
            if is_video_generation:
                video_notice = "\n\n---\n📹 视频为异步生成，生成结果可在官网聊天窗口查看下载。\n\n⏱️ 使用限制：\n- 视频生成 (Veo 模型)：每天总共可以生成 3 次\n- 图片生成 (Nano Banana 模型)：每天总共可以生成 1000 次"
                if final_text:
                    final_text = final_text + video_notice
                else:
                    final_text = video_notice.strip()
            
            if final_text:
                # 优化图片 URL 为原始高清尺寸（仅对未下载的原始 URL）
                final_text = self._optimize_image_urls(final_text)
                return final_text
            
            # 如果没有文本也没有图片，尝试从 last_inner_json 中提取更多信息
            if self.debug and last_inner_json:
                print(f"[DEBUG] 无法提取内容，inner_json 结构: {str(last_inner_json)[:500]}...")
                
        except Exception as e:
            if self.debug:
                print(f"[DEBUG] 解析错误: {e}")
        
        return "无法解析响应"
    
    def _extract_generated_media(self, data: Any, depth: int = 0) -> List[str]:
        """从响应数据中递归提取生成的图片/视频 URL
        
        Gemini 会返回两个媒体（带水印和不带水印），我们只保留最后一个（不带水印）
        只提取 AI 生成的媒体 (/gg-dl/ 路径)，不提取用户上传的图片 (/gg/ 路径)
        """
        if depth > 30:  # 防止无限递归
            return []
        
        media_urls = []
        
        if isinstance(data, list):
            # 检查是否是媒体对结构: [[null, 1, "file1.png/mp4", "url1", ...], null, null, [null, 1, "file2.png/mp4", "url2", ...]]
            # 第一个是带水印的，第二个是不带水印的
            if (len(data) >= 1 and 
                isinstance(data[0], list) and len(data[0]) >= 4 and
                data[0][0] is None and 
                isinstance(data[0][1], int) and
                isinstance(data[0][2], str) and
                isinstance(data[0][3], str) and 
                data[0][3].startswith('https://') and
                'gg-dl/' in data[0][3]):  # 只匹配 AI 生成的媒体
                # 尝试找第二个媒体（不带水印）
                second_url = None
                if len(data) >= 4 and isinstance(data[3], list) and len(data[3]) >= 4:
                    if (data[3][0] is None and 
                        isinstance(data[3][3], str) and 
                        'gg-dl/' in data[3][3]):
                        second_url = data[3][3]
                
                # 优先使用第二个，否则用第一个
                url = second_url if second_url else data[0][3]
                if 'image_generation_content' not in url and 'video_gen_chip' not in url:
                    media_urls.append(url)
                    return media_urls
            
            # 检查是否是单个媒体数据结构: [null, 1, "filename.png/mp4", "https://...gg-dl/..."]
            if (len(data) >= 4 and 
                data[0] is None and 
                isinstance(data[1], int) and
                isinstance(data[2], str) and 
                isinstance(data[3], str) and 
                data[3].startswith('https://') and
                'gg-dl/' in data[3]):  # 只匹配 AI 生成的媒体
                url = data[3]
                if 'image_generation_content' not in url and 'video_gen_chip' not in url:
                    media_urls.append(url)
                    return media_urls
            
            # 递归搜索，收集所有媒体 URL
            all_found = []
            for item in data:
                found = self._extract_generated_media(item, depth + 1)
                if found:
                    all_found.extend(found)
            
            # 如果找到多个，返回最后一个（通常是不带水印的）
            if all_found:
                seen = set()
                unique = []
                for u in all_found:
                    if u not in seen:
                        seen.add(u)
                        unique.append(u)
                # 返回最后一个（不带水印）
                return [unique[-1]] if unique else []
                
        elif isinstance(data, dict):
            for value in data.values():
                found = self._extract_generated_media(value, depth + 1)
                if found:
                    return found
        
        return media_urls
    
    # 保持向后兼容
    def _extract_generated_images(self, data: Any, depth: int = 0) -> List[str]:
        """向后兼容的别名"""
        return self._extract_generated_media(data, depth)
    
    def _download_media_as_data_url(self, url: str) -> str:
        """下载媒体文件并保存到本地缓存，返回本地代理 URL
        
        Args:
            url: 媒体文件的 URL
            
        Returns:
            str: 本地代理 URL 或 base64 data URL
                 下载失败时返回空字符串
        """
        try:
            # 先优化 URL 获取高清原图（仅对图片）
            if ("googleusercontent" in url or "ggpht" in url) and not any(ext in url.lower() for ext in ['.mp4', '.webm', 'video']):
                # 移除现有尺寸参数，添加原始尺寸参数 =s0
                url = re.sub(r'=w\d+(-h\d+)?(-[a-zA-Z]+)*$', '=s0', url)
                url = re.sub(r'=s\d+(-[a-zA-Z]+)*$', '=s0', url)
                url = re.sub(r'=h\d+(-[a-zA-Z]+)*$', '=s0', url)
                # 如果 URL 没有尺寸参数，添加 =s0
                if not url.endswith('=s0') and '=' not in url.split('/')[-1]:
                    url += '=s0'
            
            if self.debug:
                print(f"[DEBUG] 正在下载媒体 (高清): {url[:100]}...")
            
            # 使用当前会话下载（带认证 cookies）
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                "Accept": "image/webp,image/apng,image/*,*/*;q=0.8",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                "Referer": "https://gemini.google.com/",
            }
            resp = self.session.get(url, timeout=60.0, headers=headers)
            
            if self.debug:
                print(f"[DEBUG] 下载状态: {resp.status_code}, 大小: {len(resp.content)} bytes")
            
            if resp.status_code != 200:
                if self.debug:
                    print(f"[DEBUG] 下载媒体失败: HTTP {resp.status_code}")
                return ""
            
            # 检查内容是否为空或太小（可能是错误页面）
            if len(resp.content) < 100:
                if self.debug:
                    print(f"[DEBUG] 下载内容太小，可能是错误: {resp.content[:100]}")
                return ""
            
            # 根据内容检测文件类型
            content = resp.content
            if content[:8] == b'\x89PNG\r\n\x1a\n':
                ext = ".png"
                mime = "image/png"
            elif content[:3] == b'\xff\xd8\xff':
                ext = ".jpg"
                mime = "image/jpeg"
            elif content[:6] in (b'GIF87a', b'GIF89a'):
                ext = ".gif"
                mime = "image/gif"
            elif content[:4] == b'RIFF' and content[8:12] == b'WEBP':
                ext = ".webp"
                mime = "image/webp"
            elif content[4:8] == b'ftyp' or content[:4] == b'\x00\x00\x00\x1c':
                ext = ".mp4"
                mime = "video/mp4"
            else:
                ext = ".png"
                mime = "image/png"
            
            # 根据 image_mode 决定返回格式
            if self.image_mode == "base64":
                # Base64 模式：直接返回 data URL
                b64_data = base64.b64encode(content).decode()
                if self.debug:
                    print(f"[DEBUG] 返回 base64 data URL, 大小: {len(b64_data)} 字符")
                return f"data:{mime};base64,{b64_data}"
            
            # URL 模式：保存到本地缓存并返回 URL
            import os
            media_id = f"gen_{uuid.uuid4().hex[:16]}"
            
            # 保存到缓存目录
            cache_dir = os.path.join(os.path.dirname(__file__), "media_cache")
            os.makedirs(cache_dir, exist_ok=True)
            file_path = os.path.join(cache_dir, media_id + ext)
            
            with open(file_path, "wb") as f:
                f.write(content)
            
            if self.debug:
                print(f"[DEBUG] 媒体已保存: {file_path}")
            
            # 返回完整的媒体访问 URL
            media_path = f"/media/{media_id}"
            if self.media_base_url:
                return f"{self.media_base_url}{media_path}"
            return media_path
            
        except Exception as e:
            if self.debug:
                print(f"[DEBUG] 下载媒体异常: {e}")
            return ""
    
    def _optimize_image_urls(self, text: str) -> str:
        """优化文本中的 Google 图片 URL 为原始高清尺寸
        
        Google 图片 URL 参数说明:
        - =w400 或 =h400: 指定宽度或高度
        - =s400: 指定最大边长
        - =s0 或 =w0-h0: 原始尺寸
        """
        import re
        
        def optimize_url(url: str) -> str:
            # 匹配 googleusercontent 或 ggpht 图片 URL
            if "googleusercontent" not in url and "ggpht" not in url:
                return url
            # 移除现有尺寸参数，添加原始尺寸参数
            url = re.sub(r'=w\d+(-h\d+)?(-[a-zA-Z]+)*$', '=s0', url)
            url = re.sub(r'=s\d+(-[a-zA-Z]+)*$', '=s0', url)
            url = re.sub(r'=h\d+(-[a-zA-Z]+)*$', '=s0', url)
            # 如果 URL 没有尺寸参数，添加 =s0
            if not url.endswith('=s0') and '=' not in url.split('/')[-1]:
                url += '=s0'
            return url
        
        # 匹配 Markdown 图片语法和纯 URL
        # Markdown: ![alt](url)
        def replace_md_img(match):
            alt = match.group(1)
            url = match.group(2)
            return f"![{alt}]({optimize_url(url)})"
        
        text = re.sub(r'!\[([^\]]*)\]\(([^)]+)\)', replace_md_img, text)
        
        # 匹配独立的 Google 图片 URL
        def replace_url(match):
            return optimize_url(match.group(0))
        
        text = re.sub(r'https?://[^\s\)]+(?:googleusercontent|ggpht)[^\s\)]*', replace_url, text)
        
        return text

    
    def _extract_text(self, parsed_data: list) -> str:
        """从解析后的数据中提取文本"""
        try:
            # 更新会话上下文
            if parsed_data and len(parsed_data) > 1:
                if parsed_data[1] and len(parsed_data[1]) > 0:
                    self.conversation_id = parsed_data[1][0] or self.conversation_id
                if parsed_data[1] and len(parsed_data[1]) > 1:
                    self.response_id = parsed_data[1][1] or self.response_id
            
            # 提取候选回复
            if parsed_data and len(parsed_data) > 4 and parsed_data[4]:
                candidates = parsed_data[4]
                if candidates and len(candidates) > 0:
                    first_candidate = candidates[0]
                    if first_candidate and len(first_candidate) > 1:
                        self.choice_id = first_candidate[0] or self.choice_id
                        content_parts = first_candidate[1]
                        if content_parts and len(content_parts) > 0:
                            return content_parts[0] if isinstance(content_parts[0], str) else str(content_parts[0])
            
            # 备用提取
            if parsed_data and len(parsed_data) > 0:
                def find_text(obj, depth=0):
                    if depth > 10:
                        return None
                    if isinstance(obj, str) and len(obj) > 50:
                        return obj
                    if isinstance(obj, list):
                        for item in obj:
                            result = find_text(item, depth + 1)
                            if result:
                                return result
                    return None
                
                text = find_text(parsed_data)
                if text:
                    return text
                    
        except Exception as e:
            pass
        
        return "无法提取回复内容"
    
    def chat(
        self,
        messages: List[Dict[str, Any]] = None,
        message: str = None,
        image: bytes = None,
        image_url: str = None,
        reset_context: bool = False,
        model: str = None
    ) -> ChatCompletionResponse:
        """
        发送聊天请求 (OpenAI 兼容格式)
        
        重要：只发送最后一条用户消息，依赖 Gemini 内部会话上下文 (conversation_id, response_id)
        来维护对话历史。前端发送的完整历史会被忽略（assistant 消息），只取最后一条 user 消息。
        
        Args:
            messages: OpenAI 格式消息列表
            message: 简单文本消息 (与 messages 二选一)
            image: 图片二进制数据
            image_url: 图片 URL
            reset_context: 是否重置上下文
            model: 模型名称 (gemini-3.0-flash/gemini-3.0-flash-thinking/gemini-3.0-pro)
        
        Returns:
            ChatCompletionResponse: OpenAI 格式响应
        """
        if reset_context:
            self.reset()
        
        # 处理输入
        text = ""
        images = []
        system_prompt = ""
        
        if messages:
            # 提取 system 消息（会作为前置指令）
            for msg in messages:
                role = msg.get("role", "user")
                content = msg.get("content", "")
                if role == "system" and isinstance(content, str) and content:
                    system_prompt = content
                    break  # 只取第一条 system 消息
            
            # 只处理最后一条 user 消息（关键修改！）
            # 依赖 Gemini 内部的 conversation_id/response_id 来维护上下文
            last_user_msg = None
            for msg in reversed(messages):
                role = msg.get("role", "user")
                if role == "user":
                    last_user_msg = msg
                    break
            
            if last_user_msg:
                content = last_user_msg.get("content", "")
                t, imgs = self._parse_content(content)
                text = t
                images = imgs
                
                # 在最后一条消息前加上 system prompt
                if system_prompt:
                    text = f"{system_prompt}\n\n{text}"
                
                self.messages.append(Message(role="user", content=content))
            
        elif message:
            text = message
            self.messages.append(Message(role="user", content=message))
            
            if image:
                images = [{"mime_type": "image/jpeg", "data": base64.b64encode(image).decode()}]
            elif image_url:
                if image_url.startswith("data:"):
                    match = re.match(r'data:([^;]+);base64,(.+)', image_url)
                    if match:
                        images = [{"mime_type": match.group(1), "data": match.group(2)}]
                else:
                    try:
                        resp = httpx.get(image_url, timeout=30)
                        mime = resp.headers.get("content-type", "image/jpeg").split(";")[0]
                        images = [{"mime_type": mime, "data": base64.b64encode(resp.content).decode()}]
                    except:
                        pass
        else:
            text = ""
        
        if not text:
            raise ValueError("消息内容不能为空")
        
        # 发送请求
        return self._send_request(text, images, model)

    
    def _log_gemini_call(self, request_data: dict, response_text: str, error: str = None):
        """记录 Gemini 内部调用日志"""
        import datetime
        log_entry = {
            "timestamp": datetime.datetime.now().isoformat(),
            "type": "gemini_internal",
            "request": request_data,
            "response_raw": response_text,
            "error": error
        }
        try:
            with open("api_logs.json", "a", encoding="utf-8") as f:
                f.write(json.dumps(log_entry, ensure_ascii=False, indent=2) + "\n---\n")
        except Exception as e:
            print(f"[LOG ERROR] 写入 Gemini 日志失败: {e}")

    def _send_request(self, text: str, images: List[Dict] = None, model: str = None) -> ChatCompletionResponse:
        """发送请求到 Gemini"""
        url = f"{self.BASE_URL}/_/BardChatUi/data/assistant.lamda.BardFrontendService/StreamGenerate"
        
        params = {
            "bl": self.bl,
            "f.sid": "",
            "hl": "zh-CN",
            "_reqid": str(self.request_count * 100000 + random.randint(10000, 99999)),
            "rt": "c",
        }
        
        # 模型标识映射 (通过请求头 x-goog-ext-525001261-jspb 选择模型)
        model_id = self.model_ids.get("flash", "56fdd199312815e2")  # 默认极速版
        if model:
            model_lower = model.lower()
            if "pro" in model_lower:
                model_id = self.model_ids.get("pro", "e6fa609c3fa255c0")
            elif "thinking" in model_lower or "think" in model_lower:
                model_id = self.model_ids.get("thinking", "e051ce1aa80aa576")
        
        # 上传图片获取路径
        image_paths = []
        if images and len(images) > 0:
            if not self.push_id:
                raise ValueError("图片上传需要 push-id，请运行 python get_push_id.py 获取并更新配置")
            else:
                try:
                    for img in images:
                        # 解码 base64 数据
                        img_data = base64.b64decode(img["data"])
                        # 上传并获取路径
                        path = self._upload_image(img_data, img["mime_type"])
                        image_paths.append(path)
                        if self.debug:
                            print(f"[DEBUG] 图片上传成功: {path[:50]}...")
                except Exception as e:
                    print(f"⚠️  图片上传失败: {e}")
                    raise Exception(f"图片上传失败: {e}")
        
        req_data = self._build_request_data(text, images, image_paths, model)
        
        form_data = {
            "f.req": req_data,
            "at": self.snlm0e,
        }
        
        # 模型选择请求头
        model_headers = {
            "x-goog-ext-525001261-jspb": json.dumps([1, None, None, None, model_id, None, None, 0, [4], None, None, 2], separators=(',', ':')),
        }
        
        # 构建日志记录
        gemini_request_log = {
            "url": url,
            "params": params,
            "text": text,
            "model": model,
            "model_id": model_id,
            "has_images": len(images) > 0 if images else False,
            "image_paths": image_paths,
            "f_req_preview": req_data[:500] + "..." if len(req_data) > 500 else req_data,
        }
        
        if self.debug:
            print(f"[DEBUG] 请求 URL: {url}")
            print(f"[DEBUG] AT Token: {self.snlm0e[:30]}...")
            print(f"[DEBUG] 模型: {model or '默认'}, ID: {model_id}")
            if image_paths:
                print(f"[DEBUG] 请求数据前300字符: {req_data[:300]}")
        
        # 重试机制
        max_retries = 3
        last_error = None
        
        for attempt in range(max_retries):
            try:
                resp = self.session.post(url, params=params, data=form_data, headers=model_headers, timeout=60.0)
            
                if self.debug:
                    print(f"[DEBUG] 响应状态: {resp.status_code}")
                    print(f"[DEBUG] 响应内容前500字符: {resp.text[:500]}")
                    # 始终保存完整响应用于调试
                    with open("debug_image_response.txt", "w", encoding="utf-8") as f:
                        f.write(resp.text)
                    print(f"[DEBUG] 完整响应已保存到 debug_image_response.txt")
                
                # 记录 Gemini 完整响应
                self._log_gemini_call(gemini_request_log, resp.text)
                
                resp.raise_for_status()
                self.request_count += 1
                
                reply_text = self._parse_response(resp.text)
                
                # 保存助手回复
                self.messages.append(Message(role="assistant", content=reply_text))
                
                # 构建 OpenAI 格式响应
                return ChatCompletionResponse(
                    id=f"chatcmpl-{self.conversation_id or 'gemini'}-{int(time.time())}",
                    created=int(time.time()),
                    model="gemini-web",
                    choices=[
                        ChatCompletionChoice(
                            index=0,
                            message=Message(role="assistant", content=reply_text),
                            finish_reason="stop"
                        )
                    ],
                    usage=Usage(
                        prompt_tokens=len(text),
                        completion_tokens=len(reply_text),
                        total_tokens=len(text) + len(reply_text)
                    )
                )
                
            except httpx.HTTPStatusError as e:
                self._log_gemini_call(gemini_request_log, e.response.text if hasattr(e, 'response') else "", error=f"HTTP {e.response.status_code}")
                raise Exception(f"HTTP 错误: {e.response.status_code}")
            except (httpx.RemoteProtocolError, httpx.ReadError, httpx.ConnectError) as e:
                # 网络连接问题，可重试
                last_error = e
                if attempt < max_retries - 1:
                    wait_time = (attempt + 1) * 2  # 2, 4 秒
                    print(f"⚠️  连接中断，{wait_time}秒后重试 ({attempt + 1}/{max_retries})...")
                    time.sleep(wait_time)
                    continue
                self._log_gemini_call(gemini_request_log, "", error=str(e))
                raise Exception(f"网络连接失败（已重试{max_retries}次）: {e}")
            except Exception as e:
                self._log_gemini_call(gemini_request_log, "", error=str(e))
                raise Exception(f"请求失败: {e}")
        
        # 所有重试都失败
        if last_error:
            raise Exception(f"请求失败（已重试{max_retries}次）: {last_error}")
    
    def reset(self):
        """重置会话上下文"""
        self.conversation_id = ""
        self.response_id = ""
        self.choice_id = ""
        self.messages = []
    
    def set_session_context(self, conversation_id: str = "", response_id: str = "", choice_id: str = ""):
        """设置会话上下文 - 用于恢复持久化的会话
        
        Args:
            conversation_id: 对话 ID
            response_id: 响应 ID
            choice_id: 选择 ID
        """
        if conversation_id:
            self.conversation_id = conversation_id
        if response_id:
            self.response_id = response_id
        if choice_id:
            self.choice_id = choice_id
    
    def get_session_context(self) -> dict:
        """获取当前会话上下文 - 用于持久化保存
        
        Returns:
            dict: 包含 conversation_id, response_id, choice_id 的字典
        """
        return {
            "conversation_id": self.conversation_id,
            "response_id": self.response_id,
            "choice_id": self.choice_id,
        }
    
    def get_history(self) -> List[Dict]:
        """获取消息历史 (OpenAI 格式)"""
        return [{"role": m.role, "content": m.content} for m in self.messages]



# OpenAI 兼容接口
class OpenAICompatible:
    """OpenAI SDK 兼容封装"""
    
    def __init__(self, client: GeminiClient):
        self.client = client
        self.chat = self.Chat(client)
    
    class Chat:
        def __init__(self, client: GeminiClient):
            self.client = client
            self.completions = self.Completions(client)
        
        class Completions:
            def __init__(self, client: GeminiClient):
                self.client = client
            
            def create(
                self,
                model: str = "gemini-web",
                messages: List[Dict] = None,
                **kwargs
            ) -> ChatCompletionResponse:
                return self.client.chat(messages=messages)
