# gmr_pdf

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
freight.py        Registros tipados (pydantic) com proveniência (page/row)
   │
   ▼
Saídas            outputs/extracao_*.json (bruto) · tabelas_*.json (grid)
                  fretes_*.json · fretes_*.csv (análise/cadastro)
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

## Qualidade

```bash
uv run ruff check src scripts tests   # linter (regras ANN/RUF/UP/B/SIM/C4)
uv run pytest                         # 49 testes
```

## Princípios

- **Zero caixas-pretas**: nada de modelos pré-treinados; ML apenas white-box (scikit-learn
  interpretável) e somente se as heurísticas falharem comprovadamente.
- **Extração lossless**: o nível 1 do JSON preserva o texto bruto com geometria e tipografia.
- **Zero hardcode**: todas as configurações em `config/settings.yaml` + `.env`.
- **Auditoria first**: timestamps UTC-3, proveniência por registro, divergências geram
  alertas explícitos (⚠️), nunca silêncio.
