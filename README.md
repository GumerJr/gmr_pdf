# gmr_pdf

[![CI](https://github.com/GumerJr/gmr_pdf/actions/workflows/ci.yml/badge.svg)](https://github.com/GumerJr/gmr_pdf/actions/workflows/ci.yml)

Pipeline auditável para extração estruturada de **tabelas de frete** (e outros PDFs) a partir do
Google Drive. Projetado para documentos originados de **Excel → PDF** (rotacionados, sem bordas,
com spans fundidos), onde o PDF é o **artefato legal assinado** — por isso o Excel de origem
não é uma fonte válida.

> Arquitetura completa e decisões de design: [`docs/arquitetura.md`](docs/arquitetura.md)

## Pipeline

```
Drive (conta de serviço)
   │  bytes
   ▼
extractor.py      rawdict (nível mais baixo do PyMuPDF) — lossless, só texto (blocos tipo 0)
   │
   ▼
vectorize.py      Struct-of-Arrays NumPy (spans + caracteres) — hot path sem loops
   │
   ▼
spatial.py        Grid invisível: rotação canônica (landscape) → linhas por clustering
                  de baseline → células = spans por linha → join de tokens (R$ + valor)
   │
   ▼
semantic.py       Rótulos fundidos com gap zero → vocabulário do domínio (settings.yaml)
   │
   ▼
freight.py        TabelaFrete: dados_transportador + dados_tarifa (pydantic,
                  tipados, com proveniência page/row)
   │
   ▼
Saídas            outputs/extracao_*.json (bruto) · tabelas_*.json (grid)
                  fretes_*.json · fretes_*.csv (tarifas p/ análise)
                  tabela_*.json — documento completo estruturado ⬇
```

## Estrutura

```
src/gmr_pdf/   pacote (settings, logger, drive, extractor, vectorize,
               spatial, semantic, freight, models, json_export, renderer)
config/        settings.yaml (não sensível) + credentials/
data/          raw/ · processed/
scripts/       extract_freight_table.py (pipeline end-to-end)
tests/         pytest (49 testes)
outputs/       artefatos gerados (JSON/CSV/PDF reconstruído)
docs/          arquitetura.md
```

## Famílias de tabelas (resiliência por perfil)

Todo o vocabulário e regras de domínio vivem em **`config/families/<nome>.yaml`** —
nunca no código. Cada perfil define: rótulos do cabeçalho, padrão de faixas,
mapeamento label→campo, assinatura de linha de dados (`auto`/`cep_pairs`/`money`),
padrão de dinheiro, política de valor único (`fanout`/`first`), mapa do preâmbulo,
seção de alterações e seções de cláusulas (marcador, stop, junk).

```bash
# auto-detecção por marcadores (default: freight.default_family)
uv run python scripts/extract_freight_table.py

# seleção explícita de família
uv run python scripts/extract_freight_table.py <file_id> --familia magalu_escalonada
```

Nova família = **um arquivo YAML novo**, zero código. Três padrões de preâmbulo
suportados: `CHAVE | VALOR`, chave sozinha + valor na linha seguinte e
`CHAVE: valor` inline.

## Configuração

Pré-requisitos: [`uv`](https://docs.astral.sh/uv/) e Python 3.14+.

```bash
uv sync                 # instala dependências no .venv (prompt: gmr_pdf)
```

Crie o `.env` a partir do `.env.example`:

| Variável | Descrição |
|---|---|
| `GMR_GOOGLE_SERVICE_ACCOUNT_FILE` | Caminho do JSON da conta de serviço |
| `GMR_GOOGLE_DRIVE_FOLDER_ID` | ID da pasta do Drive (extraído da URL) |

> A pasta do Drive precisa estar **compartilhada com o e-mail da conta de serviço**
> (campo `client_email` do JSON), não com sua conta pessoal.

## Uso

```bash
# processa o primeiro PDF da pasta configurada
uv run python scripts/extract_freight_table.py

# ou um arquivo específico pelo ID do Drive
uv run python scripts/extract_freight_table.py <file_id>
```

Uso como biblioteca:

```python
from gmr_pdf import (
    get_drive_client, extract_document, extract_grids,
    expand_grid_labels, parse_freight_grid,
)

pdf = get_drive_client().download_pdf("<file_id>")
documento = extract_document(pdf)
grids = [expand_grid_labels(g) for g in extract_grids(documento)]
tabela = parse_freight_grid(grids[0])   # None se não houver cabeçalho
```

## Estrutura de saída (`tabela_<file_id>.json`)

```jsonc
{
  "dados_transportador": {
    "modalidade_operacao": "LAST MILE",
    "operacao": "AGENCIADOR",
    "sigla": "HPLH",
    "razao_social": "R LUCHTENBERG EXPRESS LTDA",
    "cnpj": "57804838000155",
    "telefone": "47 99249-9352",
    "email": "rluchtenbergexpress@gmail.com",
    "inicio_vigencia": "16/04/2026",
    "responsavel_confeccao": "douglas.gabriel@magazineluiza.com.br",
    "id_tabela": "ID01175"
  },
  "dados_tarifa": [
    {
      "page": 0, "row": 15,
      "tipo_veiculo": "TODOS", "cidade": "ÁGUAS MORNAS", "sigla": "HPLH",
      "cep_inicial": "88150-000", "cep_final": "88159-999",
      "interiorizacao": "Capital I", "prazo": null, "diaria": "50",
      "faixas": [
        {"rotulo": "De 0 até 2", "valor": "5.20"},
        {"rotulo": "De 2,01 até 4,00", "valor": "5.50"}
      ]
    }
  ],
  "alteracoes_tabela": {
    "cd": "HLDB", "nome_analista": "DOUGLAS GABRIEL DE OLIVEIRA",
    "data_alteracao": null,
    "tipo_alteracao": "Atualização de abrangencia global - ...",
    "tipo_operacao": "Last Mille (Entrega porta a porta)"
  },
  "generalidades": {
    "pagamento": ["Quinzenal, 30 dias para pagamento", "..."],
    "comprovante_entrega": ["Comprovante capturado no sistema eletrônico da CONTRATANTE", "..."],
    "perda_idenizacao_restricao": ["A MAGALOG incluirá nos fechamentos...", "..."],
    "acareacoes": ["Em caso de reclamação do cliente...", "..."]
  }
}
```

Regras de coleta de cláusulas (todas em `settings.yaml` → `freight.generalidades`):
seções abrem por **marcador de coluna** (x₀) ou por **padrão startswith**; um novo
marcador fecha o bloco anterior; `stop_prefixes` encerram seções (bloco de
assinaturas / cláusula de conformidade); `junk_prefixes` eliminam ruídos (DocuSign);
linhas de dados nunca entram em cláusulas; cláusulas fluem entre páginas.

## Qualidade

```bash
uv run ruff check src tests scripts   # linter (regras ANN/RUF/UP/B/SIM/C4)
uv run pytest                         # 58 testes + cobertura (gate: ≥ 85%)
```

**CI**: GitHub Actions roda ruff + pytest com gate de cobertura em cada push/PR
para `main` (`.github/workflows/ci.yml`).

## Princípios

- **Zero caixas-pretas**: nada de modelos pré-treinados; ML apenas white-box (scikit-learn
  interpretável) e somente se as heurísticas falharem comprovadamente.
- **Extração lossless**: o nível 1 do JSON preserva o texto bruto com geometria e tipografia.
- **Zero hardcode**: todas as configurações em `config/settings.yaml` + `.env`.
- **Auditoria first**: timestamps UTC-3, proveniência por registro, divergências geram
  alertas explícitos (⚠️), nunca silêncio.
