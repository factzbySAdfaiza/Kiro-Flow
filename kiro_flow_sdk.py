import os
import json
import httpx
from typing import List, Dict, Any, Optional, Union, Iterator, AsyncIterator

class KiroFlowError(Exception):
    """Base exception for Kiro-Flow SDK."""
    pass

class KiroFlow:
    """
    Official Python SDK for Kiro-Flow.
    
    Usage:
        client = KiroFlow(api_key="your_api_key")
        response = client.chat.completions.create(
            model="deepseek-v3.2",
            messages=[{"role": "user", "content": "Hello!"}]
        )
        print(response['choices'][0]['message']['content'])
    """
    
    def __init__(
        self, 
        api_key: Optional[str] = None, 
        base_url: str = "https://kiro-flow.vercel.app/v1",
        timeout: float = 60.0
    ):
        self.api_key = api_key or os.environ.get("KIRO_FLOW_API_KEY")
        if not self.api_key:
            raise KiroFlowError("API key is required. Pass it via constructor or KIRO_FLOW_API_KEY env var.")
        
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "User-Agent": "KiroFlow-Python-SDK/1.0.0"
        }
        
        self.chat = Chat(self)
        self.models = Models(self)

class Chat:
    def __init__(self, client: KiroFlow):
        self.client = client
        self.completions = Completions(client)

class Completions:
    def __init__(self, client: KiroFlow):
        self.client = client

    def create(
        self, 
        model: str, 
        messages: List[Dict[str, str]], 
        stream: bool = False,
        **kwargs
    ) -> Union[Dict[str, Any], Iterator[Dict[str, Any]]]:
        """Create a chat completion."""
        payload = {
            "model": model,
            "messages": messages,
            "stream": stream,
            **kwargs
        }
        
        if stream:
            return self._stream_request(payload)
        else:
            return self._sync_request(payload)

    def _sync_request(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        with httpx.Client(timeout=self.client.timeout) as client:
            try:
                resp = client.post(
                    f"{self.client.base_url}/chat/completions",
                    headers=self.client.headers,
                    json=payload
                )
                resp.raise_for_status()
                return resp.json()
            except httpx.HTTPError as e:
                raise KiroFlowError(f"HTTP Request failed: {e}")

    def _stream_request(self, payload: Dict[str, Any]) -> Iterator[Dict[str, Any]]:
        with httpx.Client(timeout=self.client.timeout) as client:
            with client.stream(
                "POST", 
                f"{self.client.base_url}/chat/completions",
                headers=self.client.headers,
                json=payload
            ) as resp:
                resp.raise_for_status()
                for line in resp.iter_lines():
                    if line.startswith("data: "):
                        data_str = line[6:].strip()
                        if data_str == "[DONE]":
                            break
                        try:
                            yield json.loads(data_str)
                        except json.JSONDecodeError:
                            continue

class Models:
    def __init__(self, client: KiroFlow):
        self.client = client

    def list(self) -> List[Dict[str, Any]]:
        """List available models."""
        with httpx.Client(timeout=self.client.timeout) as client:
            try:
                resp = client.get(
                    f"{self.client.base_url}/models",
                    headers=self.client.headers
                )
                resp.raise_for_status()
                return resp.json()["data"]
            except httpx.HTTPError as e:
                raise KiroFlowError(f"Failed to fetch models: {e}")

# Example usage if run directly
if __name__ == "__main__":
    print("Kiro-Flow SDK loaded. Set KIRO_FLOW_API_KEY environment variable to use it.")
    print("Base URL: https://kiro-flow.vercel.app/v1")
