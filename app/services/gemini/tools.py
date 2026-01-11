"""Gemini Tools/Function Calling 支持 - 通过提示词模拟实现"""

import re
import json
import uuid
from typing import List, Dict, Any, Tuple, Optional


def build_tools_prompt(tools: List[Dict]) -> str:
    """
    将 OpenAI tools 定义转换为提示词
    
    Args:
        tools: OpenAI 格式的 tools 定义
        
    Returns:
        str: 用于引导模型输出 tool_call 的系统提示词
    """
    if not tools:
        return ""
    
    # 提取 function 定义
    tools_schema = json.dumps([{
        "name": t["function"]["name"],
        "description": t["function"].get("description", ""),
        "parameters": t["function"].get("parameters", {})
    } for t in tools if t.get("type") == "function"], ensure_ascii=False, indent=2)
    
    prompt = f"""[系统指令] 你必须作为函数调用代理。不要自己回答问题，必须调用函数。

可用函数:
{tools_schema}

严格规则:
1. 你不能直接回答用户问题
2. 你必须选择一个函数并调用它
3. 只输出以下格式，不要有任何其他文字:
```tool_call
{{"name": "函数名", "arguments": {{"参数": "值"}}}}
```

用户请求: """
    return prompt


def parse_tool_calls(content: str) -> Tuple[List[Dict[str, Any]], str]:
    """
    解析响应中的工具调用
    
    Args:
        content: 模型响应内容
        
    Returns:
        Tuple[List[Dict], str]: (tool_calls列表, 剩余文本内容)
    """
    tool_calls = []
    
    # 多种匹配模式
    patterns = [
        r'```tool_call\s*\n?(.*?)\n?```',       # ```tool_call ... ```
        r'```json\s*\n?(.*?)\n?```',             # ```json ... ``` (有时模型会用这个)
        r'```\s*\n?(\{[^`]*"name"[^`]*\})\n?```', # ``` {...} ```
    ]
    
    matches = []
    for pattern in patterns:
        found = re.findall(pattern, content, re.DOTALL)
        matches.extend(found)
    
    # 也尝试直接匹配 JSON 对象（没有代码块包裹的情况）
    if not matches:
        json_pattern = r'\{[^{}]*"name"\s*:\s*"[^"]+"\s*,\s*"arguments"\s*:\s*\{[^{}]*\}[^{}]*\}'
        matches = re.findall(json_pattern, content, re.DOTALL)
    
    for match in matches:
        try:
            match = match.strip()
            # 尝试解析 JSON
            call_data = json.loads(match)
            if call_data.get("name"):
                tool_calls.append({
                    "id": f"call_{uuid.uuid4().hex[:8]}",
                    "type": "function",
                    "function": {
                        "name": call_data.get("name", ""),
                        "arguments": json.dumps(call_data.get("arguments", {}), ensure_ascii=False)
                    }
                })
        except json.JSONDecodeError:
            continue
    
    # 移除工具调用部分，保留剩余文本
    remaining = content
    for pattern in patterns:
        remaining = re.sub(pattern, '', remaining, flags=re.DOTALL)
    remaining = remaining.strip()
    
    return tool_calls, remaining


def format_tool_calls_response(tool_calls: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """
    格式化 tool_calls 为 OpenAI 响应格式
    
    Args:
        tool_calls: 解析出的 tool_calls 列表
        
    Returns:
        Dict: OpenAI 格式的 message 对象，包含 tool_calls
    """
    if not tool_calls:
        return None
    
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": tool_calls
    }
