"""Testes da camada semântica (separação de rótulos fundidos)."""

from gmr_pdf.semantic import (
    SemanticConfig,
    load_semantic_config,
    split_fused_text,
)

FUSED_CEP = "CEP INICIAL (OBRIGATA CEP FINAL (OBRIGATÓINTERIORIZAÇÃO"
FUSED_TIERS = "De 4,01 até 10,00 Acima de 10,00"


def test_config_loads_labels_and_pattern() -> None:
    cfg = load_semantic_config()
    assert "CEP INICIAL" in cfg.labels
    assert cfg.tier_pattern.search("Acima de 10,00") is not None


def test_split_fused_cep_header() -> None:
    cfg = load_semantic_config()
    assert split_fused_text(FUSED_CEP, cfg) == [
        "CEP INICIAL (OBRIGATA",
        "CEP FINAL (OBRIGATÓ",
        "INTERIORIZAÇÃO",
    ]


def test_split_fused_tier_labels() -> None:
    cfg = load_semantic_config()
    assert split_fused_text(FUSED_TIERS, cfg) == [
        "De 4,01 até 10,00",
        "Acima de 10,00",
    ]


def test_plain_text_is_untouched() -> None:
    cfg = load_semantic_config()
    assert split_fused_text("ÁGUAS MORNAS", cfg) == ["ÁGUAS MORNAS"]
    assert split_fused_text("5,20", cfg) == ["5,20"]


def test_split_is_case_insensitive_and_normalized() -> None:
    cfg = SemanticConfig(
        labels=("CEP INICIAL", "CEP FINAL"),
        tier_pattern=load_semantic_config().tier_pattern,
    )
    parts = split_fused_text("cep inicial    CEP Final", cfg)
    assert parts == ["cep inicial", "CEP Final"]
