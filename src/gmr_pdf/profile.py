"""Perfis de família de tabelas (domínio 100% externo ao código).

Cada família de tabela de frete vive em ``config/families/<nome>.yaml``
com seu vocabulário, mapas de campos, padrão de dinheiro, marcadores de
seção e políticas. A detecção automática usa score de marcadores; a
seleção explícita ocorre via parâmetro (ex.: ``--familia`` no script).
"""

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from gmr_pdf.logger import get_logger
from gmr_pdf.settings import SETTINGS_FILE

logger = get_logger(__name__)

FAMILIES_DIR = SETTINGS_FILE.parent / "families"

_DEFAULT_FAMILY = "magalu_escalonada"
_DEFAULT_TIER_PATTERN = (
    r"(?:De\s+\d+(?:[.,]\d+)?\s+até\s+\d+(?:[.,]\d+)?"
    r"|Acima\s+de\s+\d+(?:[.,]\d+)?)"
)
_DEFAULT_MONEY_PATTERN = r"^(?:R\$\s*)?[\d.]+,\d{2}$"
_DEFAULT_LABELS: tuple[str, ...] = (
    "TIPO DE VEÍCULO",
    "CIDADE",
    "SIGLA",
    "CEP INICIAL",
    "CEP FINAL",
    "INTERIORIZAÇÃO",
    "PRAZO",
    "DIÁRIA",
)
_DEFAULT_FIELD_MAP = {
    "TIPO DE VEÍCULO": "tipo_veiculo",
    "CIDADE": "cidade",
    "SIGLA": "sigla",
    "CEP INICIAL": "cep_inicial",
    "CEP FINAL": "cep_final",
    "INTERIORIZAÇÃO": "interiorizacao",
    "PRAZO": "prazo",
    "DIÁRIA": "diaria",
}
_DEFAULT_TRANSPORTADOR_MAP = {
    "MODALIDADE DA OPERAÇÃO": "modalidade_operacao",
    "OPERAÇÃO": "operacao",
    "SIGLA": "sigla",
    "RAZÃO SOCIAL": "razao_social",
    "CNPJ": "cnpj",
    "TELEFONE DO PARCEIRO": "telefone",
    "E-MAIL DO PARCEIRO": "email",
    "INICIO DA VIGÊNCIA": "inicio_vigencia",
    "EMAIL DE QUEM CONFECCIONOU": "responsavel_confeccao",
    "ID TABELA": "id_tabela",
}
_DEFAULT_ALTERACOES_MARK = "ALTERAÇÕES DA TABELA"
_DEFAULT_ALTERACOES_MAP = {
    "CD": "cd",
    "NOME DO ANALISTA": "nome_analista",
    "DATA ALTERAÇÃO": "data_alteracao",
    "TIPO ALTERAÇÃO": "tipo_alteracao",
    "TIPO OPERÇÃO": "tipo_operacao",
}
_DEFAULT_GENERALIDADES_SECTIONS = {
    "pagamento": {"startswith": ["QUINZENAL", "PAGAMENTO VÁLIDO"]},
    "comprovante_entrega": {"marker": "COMPROVANTE DE ENTREGA"},
    "perda_idenizacao_restricao": {"marker": "PERDAS, INDENIZAÇÕES e RESTRIÇÕES"},
    "acareacoes": {"marker": "ACAREAÇÕES"},
}
_DEFAULT_STOP_PREFIXES = ("CONTRATADA", "EM CONFORMIDADE COM A LEGISLAÇÃO")
_DEFAULT_JUNK_PREFIXES = ("DOCUSIGN",)
_DEFAULT_JOIN_TOKENS = ("R$",)


def norm_key(text: str) -> str:
    """Normaliza chave visual (upper, espaços colapsados, sem ':' final)."""
    return re.sub(r"\s+", " ", text).strip().rstrip(":").strip().upper()


def _norm_map(raw: object) -> dict[str, str]:
    if not isinstance(raw, dict):
        return {}
    return {norm_key(str(k)): str(v) for k, v in raw.items()}


@dataclass(frozen=True)
class SectionSpec:
    """Especificação de seção de cláusulas (marcador de coluna ou startswith)."""

    field: str
    marker: str | None
    startswiths: tuple[str, ...]


@dataclass(frozen=True)
class FamilyProfile:
    """Perfil completo de uma família de tabela de frete."""

    name: str
    detection_markers: tuple[str, ...]
    labels: tuple[str, ...]
    tier_pattern: re.Pattern[str]
    join_tokens: tuple[str, ...]
    field_map: dict[str, str]
    transportador_map: dict[str, str]
    alteracoes_marker: str
    alteracoes_map: dict[str, str]
    sections: tuple[SectionSpec, ...]
    stop_prefixes: tuple[str, ...]
    junk_prefixes: tuple[str, ...]
    money_pattern: re.Pattern[str]
    tier_single_value_policy: str  # "fanout" | "first"
    data_row_mode: str  # "auto" | "cep_pairs" | "money"
    data_row_min: int


def _parse_sections(raw: object) -> tuple[SectionSpec, ...]:
    source = raw if isinstance(raw, dict) else _DEFAULT_GENERALIDADES_SECTIONS
    specs: list[SectionSpec] = []
    for field, cfg in source.items():
        marker = cfg.get("marker") if isinstance(cfg, dict) else None
        startswiths = cfg.get("startswith", []) if isinstance(cfg, dict) else []
        specs.append(
            SectionSpec(
                field=str(field),
                marker=norm_key(str(marker)) if marker else None,
                startswiths=tuple(norm_key(s) for s in startswiths),
            )
        )
    return tuple(specs)


def _profile_from_dict(name: str, data: dict[str, Any]) -> FamilyProfile:
    alter = data.get("alteracoes") or {}
    gen = data.get("generalidades") or {}
    data_row = data.get("data_row") or {}
    return FamilyProfile(
        name=name,
        detection_markers=tuple(
            norm_key(str(m)) for m in data.get("detection_markers", [])
        ),
        labels=tuple(str(s) for s in data.get("labels", _DEFAULT_LABELS)),
        tier_pattern=re.compile(
            str(data.get("tier_label_pattern", _DEFAULT_TIER_PATTERN))
        ),
        join_tokens=tuple(
            str(t) for t in data.get("join_tokens", _DEFAULT_JOIN_TOKENS)
        ),
        field_map=_norm_map(data.get("field_map")) or _norm_map(_DEFAULT_FIELD_MAP),
        transportador_map=_norm_map(data.get("transportador_map"))
        or _norm_map(_DEFAULT_TRANSPORTADOR_MAP),
        alteracoes_marker=norm_key(str(alter.get("marker", _DEFAULT_ALTERACOES_MARK))),
        alteracoes_map=_norm_map(alter.get("map"))
        or _norm_map(_DEFAULT_ALTERACOES_MAP),
        sections=_parse_sections(gen.get("secoes")),
        stop_prefixes=tuple(
            norm_key(s) for s in gen.get("stop_prefixes", _DEFAULT_STOP_PREFIXES)
        ),
        junk_prefixes=tuple(
            norm_key(s) for s in gen.get("junk_prefixes", _DEFAULT_JUNK_PREFIXES)
        ),
        money_pattern=re.compile(
            str(data.get("money_pattern", _DEFAULT_MONEY_PATTERN))
        ),
        tier_single_value_policy=str(data.get("tier_single_value_policy", "fanout")),
        data_row_mode=str(data_row.get("mode", "auto")),
        data_row_min=int(data_row.get("min_count", 2)),
    )


def load_family_profile(name: str | None = None) -> FamilyProfile:
    """Carrega um perfil de família (default: ``freight.default_family``)."""
    if name is None:
        name = _DEFAULT_FAMILY
        try:
            data = yaml.safe_load(SETTINGS_FILE.read_text(encoding="utf-8")) or {}
            name = str(data.get("freight", {}).get("default_family", name))
        except FileNotFoundError:
            pass
    path = FAMILIES_DIR / f"{name}.yaml"
    if not path.exists():
        logger.warning(
            "⚠️  Perfil '%s' não encontrado em %s — usando defaults", name, path.name
        )
        return _profile_from_dict(name, {})
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    profile = _profile_from_dict(name, data)
    logger.info("🧬 Perfil de família carregado: %s", profile.name)
    return profile


def available_profiles(families_dir: Path = FAMILIES_DIR) -> list[str]:
    """Nomes dos perfis disponíveis (arquivos ``*.yaml`` em config/families)."""
    if not families_dir.exists():
        return [_DEFAULT_FAMILY]
    return sorted(p.stem for p in families_dir.glob("*.yaml")) or [_DEFAULT_FAMILY]


def detect_family(texts: list[str]) -> FamilyProfile:
    """Detecta a família do documento por score de marcadores.

    Score = número de marcadores distintos presentes nos textos
    (normalizados). Empate/ausência → perfil default com warning.
    """
    norms = [norm_key(t) for t in texts]
    best = load_family_profile()
    best_score = _score(best, norms)
    for name in available_profiles():
        if name == best.name:
            continue
        candidate = load_family_profile(name)
        score = _score(candidate, norms)
        if score > best_score:
            best, best_score = candidate, score
    label = "auto-detectado" if best_score >= 2 else "default (nenhum marcador casou)"
    logger.info("🧬 Família %s: %s (score=%d)", label, best.name, best_score)
    return best


def _score(profile: FamilyProfile, norms: list[str]) -> int:
    return sum(
        any(t.startswith(marker) for t in norms) for marker in profile.detection_markers
    )
