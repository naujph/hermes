#!/usr/bin/env python3
"""Skill: vision

Analisa imagens via OCR local + LLM unificado.
Interpreta prints de posição, tabelas de previdência, cartões de visita,
gráficos e documentos. Pode sugerir criar lead a partir de cartão de visita.

Uso:
    echo '{"image_path": "/tmp/foto.jpg", "caption": "cartão de visita", "task": "cartao"}' | python hermes/skills/vision.py
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Garante UTF-8 no stdout no Windows
if sys.stdout.encoding != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

from app.config import load_env
from app.llm_client import UnifiedLLMClient

load_env()


def _configure_tesseract_env() -> None:
    """Configura caminho do Tesseract e TESSDATA_PREFIX no Windows."""
    # Caminho padrão do Tesseract no Windows
    tesseract_cmd = os.getenv("TESSERACT_CMD", r"C:\Program Files\Tesseract-OCR\tesseract.exe")
    if not Path(tesseract_cmd).exists():
        # Fallback para possível instalação via chocolatey/scoop
        for candidate in [
            r"C:\Program Files\Tesseract-OCR\tesseract.exe",
            r"C:\Users\Juan\AppData\Local\Programs\Tesseract-OCR\tesseract.exe",
        ]:
            if Path(candidate).exists():
                tesseract_cmd = candidate
                break

    # TESSDATA_PREFIX: preferencia .env > diretório padrão > diretório local do usuário
    tessdata_prefix = os.getenv("TESSDATA_PREFIX")
    if not tessdata_prefix:
        default_tessdata = Path(tesseract_cmd).parent / "tessdata"
        user_tessdata = Path.home() / "AppData" / "Local" / "Tesseract-OCR" / "tessdata"
        if (user_tessdata / "por.traineddata").exists():
            tessdata_prefix = str(user_tessdata.resolve())
        elif (default_tessdata / "por.traineddata").exists():
            tessdata_prefix = str(default_tessdata.resolve())

    if tesseract_cmd:
        os.environ["TESSERACT_CMD"] = tesseract_cmd
    if tessdata_prefix:
        os.environ["TESSDATA_PREFIX"] = tessdata_prefix


def _ocr_image(image_path: str) -> str:
    """Extrai texto da imagem usando Tesseract."""
    try:
        from PIL import Image
        import pytesseract

        _configure_tesseract_env()

        tesseract_cmd = os.getenv("TESSERACT_CMD", r"C:\Program Files\Tesseract-OCR\tesseract.exe")
        if Path(tesseract_cmd).exists():
            pytesseract.pytesseract.tesseract_cmd = tesseract_cmd

        image = Image.open(image_path)
        return pytesseract.image_to_string(image, lang="por").strip()
    except Exception as exc:
        return f"[OCR indisponível: {exc}]"


def _build_prompt(ocr_text: str, caption: str, task: str) -> str:
    base = (
        "Você é Hermes, o secretário pessoal operacional de Juan, assessor de investimentos.\n"
        "Analise o texto extraído da imagem e responda de forma útil, direta e profissional.\n\n"
    )

    if task == "cartao":
        instruction = (
            "A imagem parece ser um CARTÃO DE VISITA.\n"
            "Extraia: nome da pessoa, empresa, cargo, telefone, e-mail, site, endereço.\n"
            "Se tiver dados suficientes, sugira criar um lead no CRM.\n"
            "Responda em JSON EXATAMENTE com as chaves: nome, empresa, telefone, email, site, cargo, endereco, sugestao.\n"
            "NÃO traduza as chaves. Use null para campos não encontrados."
        )
    elif task == "tabela":
        instruction = (
            "A imagem parece ser uma TABELA (ex: previdência, plano, comparativo financeiro).\n"
            "Resuma os dados principais, identifique o que é mais relevante para assessoria de investimentos\n"
            "e sugira próximos passos ou comparativos."
        )
    elif task == "grafico":
        instruction = (
            "A imagem parece ser um GRÁFICO de rentabilidade ou desempenho.\n"
            "Descreva o que o gráfico mostra, destaque pontos importantes e dê uma interpretação simples."
        )
    elif task == "print":
        instruction = (
            "A imagem parece ser um PRINT de tela (ex: posição da carteira, extrato, app financeiro).\n"
            "Resuma a informação financeira relevante e aponte alertas ou observações importantes."
        )
    else:
        instruction = (
            "Identifique o tipo de documento/imagem e extraia as informações mais relevantes.\n"
            "Se houver dados de contato comercial, sugira criar lead.\n"
            "Se houver dados financeiros, faça uma interpretação resumida."
        )

    prompt = base + instruction + "\n\n"
    if caption:
        prompt += f"Legenda do usuário: {caption}\n\n"
    prompt += f"Texto OCR extraído da imagem:\n{ocr_text[:3000]}\n\n"
    prompt += "Resposta:"
    return prompt


def analyze_image(image_path: str, caption: str = "", task: str = "auto") -> dict:
    if not Path(image_path).exists():
        return {"success": False, "error": f"Imagem não encontrada: {image_path}"}

    # Detecta task automaticamente pela legenda se não informado
    if task == "auto" and caption:
        caption_lower = caption.lower()
        if any(w in caption_lower for w in ["cartão", "cartao", "visit card", "contato"]):
            task = "cartao"
        elif any(w in caption_lower for w in ["tabela", "planilha", "previdência", "previdencia", "comparativo"]):
            task = "tabela"
        elif any(w in caption_lower for w in ["gráfico", "grafico", "chart", "rentabilidade"]):
            task = "grafico"
        elif any(w in caption_lower for w in ["print", "screenshot", "posição", "extrato", "app"]):
            task = "print"

    ocr_text = _ocr_image(image_path)
    if not ocr_text or ocr_text.startswith("[OCR indisponível"):
        return {
            "success": True,
            "ocr_text": ocr_text,
            "analysis": "Não consegui extrair texto da imagem. Tente enviar com mais qualidade ou descrever o conteúdo.",
            "suggested_action": None,
        }

    prompt = _build_prompt(ocr_text, caption, task)
    llm = UnifiedLLMClient()

    analysis_text = ""
    structured: dict[str, Any] | None = None

    if task == "cartao":
        resp = llm.extract_json(
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
            max_tokens=800,
            schema_hint={
                "nome": "string",
                "empresa": "string",
                "telefone": "string",
                "email": "string",
                "site": "string",
                "cargo": "string",
                "endereco": "string",
                "sugestao": "string",
            },
        )
        parsed = resp.get("parsed") or {}
        if parsed and isinstance(parsed, dict):
            structured = {k: parsed.get(k) for k in ["nome", "empresa", "telefone", "email", "site", "cargo", "endereco"]}
            analysis_text = (
                f"Nome: {structured.get('nome') or '—'}\n"
                f"Empresa: {structured.get('empresa') or '—'}\n"
                f"Cargo: {structured.get('cargo') or '—'}\n"
                f"Telefone: {structured.get('telefone') or '—'}\n"
                f"E-mail: {structured.get('email') or '—'}\n"
                f"Site: {structured.get('site') or '—'}\n"
                f"Endereço: {structured.get('endereco') or '—'}"
            ).strip()
        else:
            analysis_text = resp.get("content", "Não foi possível extrair dados estruturados do cartão.")
    else:
        resp = llm.complete(prompt, temperature=0.3, max_tokens=1500)
        if resp.error:
            return {
                "success": False,
                "ocr_text": ocr_text,
                "analysis": f"Erro no LLM: {resp.error}",
                "suggested_action": None,
            }
        analysis_text = resp.content

    # Sugestão de ação baseada no task
    suggested_action = None
    if task == "cartao":
        has_contact = structured and (structured.get("telefone") or structured.get("email"))
        if has_contact:
            suggested_action = "Criar lead no CRM com os dados extraídos do cartão (requer aprovação)."
        else:
            suggested_action = "Dados de contato insuficientes para criar lead automaticamente."
    elif task == "tabela":
        suggested_action = "Sugerir comparativo com produtos da XP e próximo passo de apresentação."
    elif task == "print":
        suggested_action = "Sugerir diagnóstico de carteira e oportunidades de rebalanceamento."

    result: dict[str, Any] = {
        "success": True,
        "ocr_text": ocr_text,
        "analysis": analysis_text,
        "suggested_action": suggested_action,
        "task": task,
    }
    if structured:
        result["structured"] = structured
    return result


def main():
    try:
        if sys.stdin.isatty():
            payload = {}
        else:
            raw = sys.stdin.buffer.read().decode("utf-8")
            payload = json.loads(raw)
    except json.JSONDecodeError:
        print(json.dumps({"success": False, "errors": ["JSON inválido na entrada"]}))
        sys.exit(1)

    image_path = payload.get("image_path", "")
    if not image_path:
        print(json.dumps({"success": False, "errors": ["image_path é obrigatório"]}))
        sys.exit(1)

    result = analyze_image(
        image_path=image_path,
        caption=payload.get("caption", ""),
        task=payload.get("task", "auto"),
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
