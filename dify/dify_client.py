import json
import logging
import asyncio
import httpx
import os
import traceback
import ast
import re
from typing import Optional, Dict, List, Any, Callable


class DifyChatflowClient:
    def __init__(self, base_url: str, api_key: str, timeout: int = 300):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        self.endpoint = f"{self.base_url}/v1/chat-messages"

    def _clean_and_parse_response(self, raw_text: str) -> Dict:
        """
        清洗大模型输出并尝试解析
        策略：提取最外层的 {} 内容，忽略前后的废话
        """
        logging.info(f"Original Text to parse: {raw_text[:200]}...")  # 仅打印前200字符防止日志爆炸

        # 1. 定位 JSON/Dict 的起止位置
        # 无论 Dify 输出 "Here is the JSON: ```json { ... } ```", 还是直接 "{...}"
        # 我们只关心第一个 '{' 和最后一个 '}' 之间的内容
        start_index = raw_text.find('{')
        end_index = raw_text.rfind('}')

        if start_index == -1 or end_index == -1:
            # 没找到括号，说明格式完全不对
            error_str = f"Output does not contain curly braces: {raw_text[:50]}..."
            raise ValueError(error_str)

        # 提取核心内容
        content = raw_text[start_index: end_index + 1]

        # 2. 尝试标准 JSON 解析 (最优先，处理标准 {"key": true, "a": null})
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            pass  # 继续尝试

        # 3. 预处理：混合格式修正
        # 场景：大模型输出了 {'key': true} -> 单引号(Python) + true(JSON)
        # 或者 {'key': false}
        # 使用正则 \b 确保匹配的是完整的单词
        py_compatible_text = content
        py_compatible_text = re.sub(r'\bfalse\b', 'False', py_compatible_text, flags=re.IGNORECASE)
        py_compatible_text = re.sub(r'\btrue\b', 'True', py_compatible_text, flags=re.IGNORECASE)
        py_compatible_text = re.sub(r'\bnull\b', 'None', py_compatible_text, flags=re.IGNORECASE)

        # 4. 尝试 Python 字面量解析 (处理 {'key': False, 'key2': None})
        try:
            return ast.literal_eval(py_compatible_text)
        except (ValueError, SyntaxError) as e:
            # 记录具体的解析错误，方便调试
            logging.warning(f"Parse failed. JSON error passed. AST error: {e}")
            pass

        # 5. 如果都失败了
        raise ValueError(f"无法解析为字典, 提取内容为: {content}...")

    async def chat(
            self,
            query: str,
            user_id: str,
            inputs: Optional[Dict[str, Any]] = None,
            files: Optional[List[Dict[str, Any]]] = None,
            conversation_id: str = "",
            on_event: Optional[Callable[[Dict[str, Any]], None]] = None,
            max_retries: int = 2
    ) -> Dict:

        payload = {
            "inputs": inputs or {},
            "query": query,
            "response_mode": "streaming",
            "conversation_id": conversation_id,
            "user": user_id,
            "files": files or []
        }

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }

        logging.info(f"Requesting Dify chatflow API: {payload}")
        # 修正1：range 必须是 max_retries + 1，否则最后一次失败后会直接退出循环返回 None
        for attempt in range(max_retries + 1):
            try:
                answer_parts = []
                logging.info(f"Start Chat Attempt {attempt + 1}/{max_retries + 1}")

                async with httpx.AsyncClient(timeout=self.timeout) as client:
                    async with client.stream("POST", self.endpoint, headers=headers,
                                             data=json.dumps(payload)) as response:

                        if response.status_code != 200:
                            # 读取错误信息以便调试
                            error_content = await response.aread()
                            logging.error(
                                f"HTTP {response.status_code}: {error_content.decode('utf-8', errors='ignore')}")
                            raise httpx.HTTPStatusError(f"Status {response.status_code}", request=response.request,
                                                        response=response)

                        async for line in response.aiter_lines():
                            if not line.strip() or not line.startswith("data:"):
                                continue
                            try:
                                event_json = json.loads(line[len("data:"):].strip())
                                if on_event: on_event(event_json)

                                if event_json.get("event") == "message":
                                    answer_parts.append(event_json.get("answer", ""))

                                if event_json.get("event") in ["message_end", "workflow_finished"]:
                                    break
                            except json.JSONDecodeError:
                                continue

                full_text = "".join(answer_parts)
                logging.info(f"Raw answer from dify is: {full_text}")

                # 解析逻辑：如果失败会抛出 ValueError
                result_dict = self._clean_and_parse_response(full_text)
                logging.info(f"the parsed dify result is: {result_dict}")


                return result_dict

            except (httpx.RequestError, httpx.HTTPStatusError, ValueError) as e:
                logging.warning(f"Error encountered (Attempt {attempt + 1}): {str(e)}")

                # 修正2：判断逻辑保持正确
                if attempt < max_retries:
                    if isinstance(e, ValueError):
                        logging.warning("Parsing failed. Retrying...")

                    # 指数退避策略
                    sleep_time = 2 * (2 ** attempt)
                    logging.info(f"Sleeping for {sleep_time}s before retry...")
                    await asyncio.sleep(sleep_time)
                else:
                    logging.error("All retries exhausted.")
                    logging.error(traceback.format_exc())
                    raise e  # 必须抛出异常，否则外部调用者会拿到 None

            except Exception as e:
                logging.error("Critical error (Non-retryable):")
                logging.error(traceback.format_exc())
                raise e


# Dify integration is optional. Configure DIFY_BASE_URL / DIFY_COLLECTION_API_KEY /
# DIFY_MARKETING_API_KEY in the environment; clients stay None when unconfigured and
# callers must fall back to the non-Dify analysis engine.
DIFY_BASE_URL = os.getenv("DIFY_BASE_URL", "").strip()


def _build_dify_client(api_key: str) -> Optional["DifyChatflowClient"]:
    if not DIFY_BASE_URL or not api_key:
        return None
    return DifyChatflowClient(base_url=DIFY_BASE_URL, api_key=api_key)


difyCollectionNoteClient = _build_dify_client(os.getenv("DIFY_COLLECTION_API_KEY", "").strip())
difyMarketingNoteClient = _build_dify_client(os.getenv("DIFY_MARKETING_API_KEY", "").strip())


if __name__ == "__main__":
    async def main():
        client = DifyChatflowClient(
            base_url=os.environ["DIFY_BASE_URL"],
            api_key=os.environ["DIFY_COLLECTION_API_KEY"],
        )

        # 模拟包含 False 的场景
        try:
            final_answer = await client.chat(
                query=str([{'speaker': 'user', 'content': 'test'}]),
                user_id="abc-123",
                inputs={"language": "spanish"},
                on_event=lambda e: print(f"Event type: {e.get('event')}")
            )
            print("\nFinal parsed answer:", final_answer)
            print(f"Type of result: {type(final_answer)}")  # 验证是否为 dict
        except Exception as e:
            print(f"\nFinal Failure: {e}")


    asyncio.run(main())