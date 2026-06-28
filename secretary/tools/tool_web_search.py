"""Tool: web_search — Permite ao Hermes buscar na web ou ler URLs."""
from __future__ import annotations

import urllib.request
import urllib.parse
import re
from typing import Any

def execute_web_search(action: str, query: str) -> dict[str, Any]:
    """Executa a busca na web ou leitura de URL."""
    if not query:
        return {"success": False, "error": "Query ou URL vazia."}
        
    if action == "read_url":
        return _read_url(query)
    elif action == "search":
        return _search_ddg(query)
    else:
        return {"success": False, "error": f"Ação desconhecida: {action}"}

def _read_url(url: str) -> dict[str, Any]:
    try:
        if not url.startswith("http"):
            url = "https://" + url
            
        req = urllib.request.Request(
            url, 
            headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
        )
        with urllib.request.urlopen(req, timeout=15) as response:
            html = response.read().decode('utf-8', errors='ignore')
            
        # Basic tag stripping
        text = re.sub(r'<script.*?</script>', '', html, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r'<style.*?</style>', '', text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r'<[^>]+>', ' ', text)
        text = re.sub(r'\s+', ' ', text).strip()
        
        # Limit to 5000 chars to avoid blowing up context
        return {"success": True, "output": f"Conteúdo de {url}:\n\n{text[:5000]}"}
    except Exception as e:
        return {"success": False, "error": f"Erro ao ler URL: {str(e)}"}

def _search_ddg(query: str) -> dict[str, Any]:
    try:
        url = "https://lite.duckduckgo.com/lite/"
        data = urllib.parse.urlencode({'q': query}).encode('utf-8')
        req = urllib.request.Request(
            url, 
            data=data,
            headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
        )
        
        with urllib.request.urlopen(req, timeout=15) as response:
            html = response.read().decode('utf-8', errors='ignore')
            
        # Extract snippets from lite.duckduckgo.com
        snippets = []
        matches = re.finditer(r'<td class=\'result-snippet\'>(.*?)</td>', html, re.IGNORECASE | re.DOTALL)
        for m in matches:
            text = re.sub(r'<[^>]+>', '', m.group(1)).strip()
            if text:
                snippets.append(text)
                if len(snippets) >= 5:
                    break
                    
        if not snippets:
            return {"success": True, "output": "Nenhum resultado encontrado."}
            
        result_text = "\n\n".join([f"- {s}" for s in snippets])
        return {"success": True, "output": f"Resultados para '{query}':\n\n{result_text}"}
        
    except Exception as e:
        return {"success": False, "error": f"Erro na busca: {str(e)}"}
