"""Limpeza de texto em português do Brasil para fala e para transcrição.

- `preparar_para_fala`: tira markdown, links, emojis e expande símbolos e
  abreviações que o sintetizador leria errado ("R$ 10,50", "25%", "Dr.").
- `dividir_frases`: quebra o texto em frases curtas (o Kokoro com G2P do
  espeak trunca trechos longos).
- `eh_alucinacao`: reconhece frases que o Whisper inventa em silêncio ou
  ruído ("Legendas pela comunidade Amara.org", "Obrigado por assistir").
"""
from __future__ import annotations

import re
import unicodedata

try:
    from num2words import num2words
except ImportError:  # pragma: no cover - dependência opcional
    num2words = None

ABREVIACOES = {
    r"\bSr\.": "senhor",
    r"\bSra\.": "senhora",
    r"\bSrta\.": "senhorita",
    r"\bDr\.": "doutor",
    r"\bDra\.": "doutora",
    r"\bProf\.": "professor",
    r"\bProfa\.": "professora",
    r"\bEng\.": "engenheiro",
    r"\betc\.": "etcétera",
    r"\bp\. ?ex\.": "por exemplo",
    r"\bex\.:": "por exemplo:",
    r"\bvs\.?": "versus",
    r"\bn[º°]\s?": "número ",
    r"\bkm/h\b": "quilômetros por hora",
    r"\bkm\b": "quilômetros",
    r"\bkg\b": "quilos",
    r"\bmin\b": "minutos",
    r"\bseg\b": "segundos",
    r"\baprox\.": "aproximadamente",
    r"\bobs\.": "observação",
    r"\btel\.": "telefone",
    r"\bav\.": "avenida",
}

ALUCINACOES = [
    "legendas pela comunidade amara.org",
    "legenda adriana zanotto",
    "legendas adriana zanotto",
    "transcricao e legendas",
    "obrigado por assistir",
    "obrigada por assistir",
    "obrigado por assistirem",
    "inscreva-se no canal",
    "se inscreva no canal",
    "deixe seu like",
    "ate a proxima",
    "tchau tchau",
    "sous-titres realises para la communaute d'amara.org",
    "subtitles by the amara.org community",
    "thank you for watching",
    "e ai",
    "musica",
    "aplausos",
    "risos",
]


def _sem_acentos(t: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", t) if unicodedata.category(c) != "Mn")


def _numero_por_extenso(n: int) -> str:
    if num2words is None:
        return str(n)
    try:
        return num2words(n, lang="pt_BR")
    except Exception:  # noqa: BLE001
        return str(n)


def _para_inteiro(txt: str) -> int:
    return int(txt.replace(".", "").replace(" ", "") or 0)


def _moeda(m: re.Match) -> str:
    reais = _para_inteiro(m.group(1))
    centavos = int((m.group(2) or "0").ljust(2, "0")[:2])
    partes = []
    if reais or not centavos:
        partes.append(f"{_numero_por_extenso(reais)} {'real' if reais == 1 else 'reais'}")
    if centavos:
        partes.append(f"{_numero_por_extenso(centavos)} {'centavo' if centavos == 1 else 'centavos'}")
    return " e ".join(partes)


def _remover_emojis(t: str) -> str:
    return "".join(
        c for c in t if not (unicodedata.category(c) in ("So", "Cs") or 0x1F000 <= ord(c) <= 0x1FAFF)
    )


def preparar_para_fala(texto: str) -> str:
    t = texto or ""
    # Blocos de código e fórmulas não são lidos em voz alta
    t = re.sub(r"```.*?```", " ", t, flags=re.S)
    t = re.sub(r"\$\$.*?\$\$", " ", t, flags=re.S)
    t = re.sub(r"<think>.*?</think>", " ", t, flags=re.S)
    t = re.sub(r"<[^>]{1,40}>", " ", t)  # tags HTML simples
    t = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", t)  # imagens
    t = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", t)  # links: fica o texto
    t = re.sub(r"https?://\S+", " o link ", t)
    t = re.sub(r"`([^`]*)`", r"\1", t)
    t = re.sub(r"^\s{0,3}#{1,6}\s*", "", t, flags=re.M)  # títulos
    t = re.sub(r"^\s*[-*+•]\s+", "", t, flags=re.M)  # marcadores
    t = re.sub(r"^\s*(\d+)[.)]\s+", r"\1. ", t, flags=re.M)
    t = re.sub(r"^\s*\|?[-:| ]{3,}\|?\s*$", "", t, flags=re.M)  # separador de tabela
    t = t.replace("|", ", ")
    t = re.sub(r"(\*\*|__|\*|_|~~)(.+?)\1", r"\2", t)
    t = re.sub(r"[*_~#>]+", " ", t)
    # Dinheiro, porcentagem, temperatura e horas
    t = re.sub(r"R\$\s?(\d{1,3}(?:\.\d{3})*|\d+)(?:,(\d{1,2}))?", _moeda, t)
    t = re.sub(r"US\$\s?(\d[\d.]*)(?:,(\d+))?", lambda m: f"{m.group(1)} dólares", t)
    t = re.sub(r"(\d)\s?%", r"\1 por cento", t)
    t = re.sub(r"(\d)\s?°C", r"\1 graus Celsius", t)
    t = re.sub(r"(\d)\s?°", r"\1 graus", t)
    t = re.sub(r"\b(\d{1,2})h(\d{2})\b", r"\1 horas e \2", t)
    t = re.sub(r"\b(\d{1,2})h\b", r"\1 horas", t)
    t = re.sub(r"(\d)º", r"\1º", t)
    for padrao, troca in ABREVIACOES.items():
        t = re.sub(padrao, troca, t, flags=re.I)
    t = t.replace("&", " e ").replace("+", " mais ").replace("=", " igual a ")
    t = _remover_emojis(t)
    t = re.sub(r"[ \t]+", " ", t)
    t = re.sub(r" +([,.;:!?])", r"\1", t)
    return "\n".join(linha.strip() for linha in t.split("\n") if linha.strip())


_FIM_FRASE = re.compile(r"(?<=[.!?…;:])\s+|\n+")


def dividir_frases(texto: str, maximo: int = 220) -> list[str]:
    """Divide em frases; frases muito longas são quebradas em vírgulas ou espaços."""
    frases: list[str] = []
    for bruta in _FIM_FRASE.split(texto):
        frase = bruta.strip()
        if not frase:
            continue
        while len(frase) > maximo:
            corte = frase.rfind(", ", 0, maximo)
            if corte < maximo // 3:
                corte = frase.rfind(" ", 0, maximo)
            if corte <= 0:
                corte = maximo
            frases.append(frase[: corte + 1].strip())
            frase = frase[corte + 1 :].strip()
        if frase:
            frases.append(frase)
    return frases


def eh_alucinacao(texto: str) -> bool:
    limpo = _sem_acentos(texto.lower())
    limpo = re.sub(r"[^a-z0-9.' -]", " ", limpo)
    limpo = re.sub(r"\s+", " ", limpo).strip(" .")
    if not limpo:
        return True
    if re.fullmatch(r"[. ]+", limpo):
        return True
    for frase in ALUCINACOES:
        if limpo == frase:
            return True
        # Só frases longas e características valem por "contém"; "música" ou "e aí"
        # aparecem em pedidos legítimos ("toca uma música").
        if len(frase) >= 15 and frase in limpo and len(limpo) <= len(frase) + 15:
            return True
    return False
