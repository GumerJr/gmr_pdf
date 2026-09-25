# Relatório Técnico — Motor Unificado de Reconstrução Geométrica de PDFs

**Projeto:** `gmr_pdf` | **Versão:** 0.1.0 | **Data:** 2026-09-23 | **Status:** Arquitetura aprovada para execução

---

## 1. Visão Geral & Premissa Principal

**Objetivo:** Reproduzir a **camada de texto de qualquer PDF com fidelidade geométrica absoluta**,
capturando exatamente as informações originais — **posição espacial, tamanho de fonte e cor** —
eliminando completamente:

- Elementos gráficos vetoriais (grids, caixas, linhas de tabelas, réguas);
- Imagens (logos, diagramas, carimbos, marcas d'água).

O resultado é um **documento higienizado**, onde apenas o texto flutua em suas posições
espaciais originais, preservando inclusive o **espaço negativo** deixado pelas imagens
descartadas (sem *reflow* do texto ao redor).

**Aplicabilidade universal:** o mesmo pipeline atende documentos matriciais (DANFEs, faturas,
notas fiscais) e contínuos (procedimentos operacionais, políticas, relatórios).

---

## 2. Arquitetura Técnica & Pipeline

```
┌─────────────────────────────────────────────────────────────────────────┐
│  1. EXTRAÇÃO DE BAIXO NÍVEL                                             │
│  • Motor: PyMuPDF (MuPDF C-API)                                         │
│  • Leitura de `rawdict` → filtro exclusivo de Blocos Tipo 0 (Texto)     │
│  • Captura por span: texto, baseline (origin x,y), fontsize, cor, bbox  │
│  • Ignora: drawings (linhas/vetores) e Blocos Tipo 1 (Imagens)          │
└───────────────────────────────────┬─────────────────────────────────────┘
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  2. INTELIGÊNCIA ESPACIAL (ML)                                          │
│  • Layout analysis: agrupamento semântico de blocos                     │
│  • Identificação de relações: chave-valor, colunas de tabelas           │
│  • Normalização de coordenadas (escala percentual)                      │
│  • Roteamento: Texto Contínuo vs. Estrutura Matricial                   │
└───────────────────────────────────┬─────────────────────────────────────┘
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  3. RECONSTRUÇÃO DA PÁGINA                                              │
│  • Motor: `fitz.new_page()` com as mesmas dimensões originais           │
│  • Tipografia: fonte proporcional genérica (Helvetica)                  │
│  • Ancoragem pela baseline (`origin`), fontsize e cor preservados       │
└─────────────────────────────────────────────────────────────────────────┘
```

### Mapeamento proposto de módulos (`src/gmr_pdf/`)

| Módulo | Responsabilidade | Status |
|---|---|---|
| `drive.py` | Download do PDF (bytes) via conta de serviço | ✅ Concluído |
| `extractor.py` | `rawdict` → spans tipados (texto, origin, size, cor, bbox) | ✅ Concluído |
| `vectorize.py` | Struct-of-Arrays (NumPy) para análise espacial vetorizada | ✅ Concluído |
| `renderer.py` | Reconstrução via `TextWriter` (nível mais baixo de escrita), baseline + morph para rotações; determinístico | ✅ Concluído |
| `logger.py` | Logging centralizado (UTC-3 Brasília, colorama, config no YAML) | ✅ Concluído |
| `spatial.py` | Grid invisível: linhas por clustering de baseline + células por span (sem bordas/alinhamento global); normalização de rotação; join de tokens (`R$`+valor) | ✅ Concluído (Estágio 1) |
| `semantic.py` | Separação de rótulos fundidos pelo Excel (gaps zero → indivisível por geometria) via vocabulário do domínio configurável (YAML) | ✅ Concluído |
| `freight.py` | Domínio: `TabelaFrete` completa (transportador + tarifa + alterações + cláusulas). 100% dirigido por perfil | ✅ Concluído |
| `profile.py` | **Perfis de família** (`config/families/*.yaml`): vocabulário, mapas, padrão de dinheiro, política de faixas, assinatura de linha; auto-detecção por score de marcadores | ✅ Concluído |
| `models.py` + `json_export.py` | Schemas pydantic + exportação JSON (nível 1 bruto / nível 2 grids) | ✅ Concluído |

### 2.1 Camada 2 — Estratégia em estágios: "heurística primeiro, ML quando conquistado"

**Princípio: ML é conquistado, não assumido.** O caminho determinístico é
sempre o fallback (ver R4) — o pipeline nunca quebra por causa do ML.

| Estágio | Abordagem | Casos cobertos |
|---|---|---|
| **0 — Determinístico** (Fase 1) | Reconstrução geométrica por identidade de coordenadas. **Zero ML, para sempre** | Reconstrução fiel de qualquer PDF com camada de texto |
| **1 — Heurísticas vetorizadas** (Fase 2, via `SpansVector`) | **Linhas** = clustering 1-D das baselines (`origins[:,1]`, tolerância × altura mediana); **Células** = spans por linha em ordem horizontal (o span é a fronteira de célula do export Excel: gaps internos como `R$       5,50` NÃO separam); **rotação** = normalização do frame (landscape→canônico) antes do corte; espaços internos colapsados (`R$ 5,50`). **Sem bordas, sem alinhamento global** — bordas são inexistentes/incompletas nesses documentos | Tabelas de frete (validado: 21 linhas de dados × 15 colunas na proposta real) |
| **2 — ML white-box** (SE e somente se o estágio 1 falhar comprovadamente) | **Proibido caixa-preta / modelos pré-treinados externos.** Apenas modelos simples, interpretáveis e treinados sobre os nossos próprios vetores (`SpansVector`): regressão linear (correção de escala R2), regressão logística (roteamento contínuo/matricial), clustering (agrupamento espacial) — scikit-learn auditável | Casos que heurísticas não alcançam comprovadamente |

**Uso de grafos:** fora do núcleo de reconstrução (posição = mapeamento
identidade lido do `rawdict`; o MuPDF já resolveu o grafo de layout ao
renderizar). Grafos entram em dois pontos: (a) união de tabelas cortadas
entre páginas (R5): nós=fragmentos, arestas=continuidade de coluna; (b)
representação de entrada da camada ML: nós=spans (features do
`SpansVector`), arestas=k-NN geométrico construído de forma vetorizada
(broadcasting de bboxes).

---

## 3. Comportamento por Tipo de Documento

### 3.1 Matriciais / Tabulares (DANFEs, NFs, Faturas)
- ML identifica colunas e linhas, mantendo o texto nos "**grids invisíveis**" originais.
- Valores numéricos preservam alinhamento à direita: ancoragem por `bbox[2]` (x final).
- Blocos de dados (emitente, destinatário, impostos) mantêm posição absoluta.

### 3.2 Contínuos (Procedimentos, Políticas, Relatórios)
- Fluxo topológico preservado: cima → baixo, esquerda → direita.
- Títulos, subtítulos, parágrafos e listas numeradas mantidos.
- Elementos graficos decorativos e screenshots (imagens) eliminados.

---

## 4. Decisões Estratégicas Consolidadas

| Decisão | Justificativa | Trade-off aceito |
|---|---|---|
| **Fonte genérica proporcional (Helvetica)** | Baixa complexidade, arquivo final leve, sem injeção de buffers de fontes proprietárias | Desvio de largura de 2–5px em strings longas sem espaços |
| **Abandono de fonte monoespaçada** | Evita *width drift* acumulado e deformação de kerning em colunas estreitas | — |
| **Ancoragem por baseline (`origin`)** | Textos da mesma linha ficam perfeitamente alinhados independente do glifo | — |
| **Descarte total de imagens** | Documento higienizado, foco 100% na informação textual | Logos/carimbos perdidos (decisão consciente) |
| **Preservação do espaço negativo** | Texto ao redor de imagens não sofre reflow; diagramação intacta | "Buracos" visuais esperados no resultado |
| **ML na camada espacial (não na extração)** | Extração determinística (C-API) + ML apenas para semântica/roteamento | Dependência de modelo para casos matriciais complexos |
| **Dataclasses no núcleo (não pydantic)** | Hot path com 10–50 mil spans/doc; fonte confiável (rawdict); pydantic fica nas boundaries (settings, DTOs futuros da camada ML) | Sem validação de shape no núcleo (aceito: contrato estável do MuPDF) |
| **Extração LOSSLESS, normalização na camada 2/3** | Artefatos de origem (bold sintético, spans fragmentados) são capturados fielmente e corrigidos por perfil de origem — reversível e configurável | Nenhum |
| **SoA vetorizado (NumPy) na fronteira extração→espacial** | Filtragens/máscaras/histogramas/clusters sem loop Python no hot path; arrays prontos como features de ML | Duas representações de span (objeto ↔ array) — mitigado por conversão unidirecional única em `vectorize.py` |
| **Grafos fora do núcleo de reconstrução** | Posição = identidade (geometria absoluta já resolvida pelo MuPDF); grafo só para união de tabelas entre páginas (R5) e entrada da camada ML | União cross-page de tabelas adiada para Fase 2 |
| **Zero caixas-pretas** | Nenhum modelo pré-treinado externo; ML, se necessário, será white-box (scikit-learn interpretável) treinado sobre vetores próprios | Escaneados (R1) são rejeitados, não processados via OCR |
| **Resiliência por perfis de família** | Domínio 100% em YAML (`config/families/`); assinatura de linha de dados derivada do cabeçalho (auto); 3 padrões de preâmbulo KV (mesma linha, linha seguinte, inline `CHAVE: valor`); política de valor único (`fanout`); ética de ausência explícita (`None`) | Novas famílias exigem vocabulário próprio (o gap-zero de rótulos é insolúvel por geometria — medido) |

---

## 5. Mapeamento de Riscos

| # | Risco | Impacto | Mitigação |
|---|---|---|---|
| R1 | **PDF escaneado sem camada de texto** → filtro de imagens gera página em branco | Alto | **Gate `needs_ocr`** (implementado): página sem spans é detectada, logada com ⚠️ e o documento é **rejeitado com relatório** (quarentena). OCR automático está **fora de escopo** por envolver modelo caixa-preta |
| R2 | **Desalinhamento em chaves longas** (ex.: chave NF-e de 44 dígitos) por substituição de fonte | Médio | Fator de escala horizontal por span: `scale = largura_original / largura_nova` em colunas/valores detectados pelo ML |
| R3 | **PDFs com fontes não-embedded / subset corrompido** | Médio | Garantir que a extração leia métricas do `rawdict` (independe do render) |
| R4 | **Modelo ML mal calibrado em layouts inéditos** | Médio | Fallback determinístico: se confiança baixa → reconstrução puramente geométrica (posições absolutas, sem agrupamento) |
| R5 | **PDFs originados de Excel**: bold sintético, conteúdo landscape em página portrait (dir `(0,-1)` em 100% dos spans — observado no real), spans com padding interno (`R$          5,50`), linhas densamente empacotadas sem gap de cobertura, gridlines vetoriais | Alto | Implementado: (a) normalização de frame canônico por rotação vetorizada; (b) células = spans por linha (ordem x) — nunca splitar pelo gap interno; (c) linhas por clustering de baseline (cobertura falha sem gaps); (d) colapso de espaços múltiplos no nível 2 (bruto preservado no nível 1); (e) join de tokens configurável (`cell_join_tokens`: célula `R$` funde com o valor seguinte); (f) rótulos fundidos com gap zero (`CEP INICIAL…INTERIORIZAÇÃO`, faixas escalonadas) — medido: geometricamente indivisíveis (gaps ≈ 0/negativos); solução: **camada semântica** com vocabulário configurável (`semantic.labels` + `tier_label_pattern`). Nota: grafo foi **avaliado e rejeitado** para esse caso — não há aresta cortável (informação ausente do espaço); grafos permanecem reservados à união de tabelas entre páginas |

---

## 6. Estratégia de Execução

1. **Fase 0 — Infraestrutura** ✅ — Ambiente uv/Python 3.14, módulo Drive, configs centralizadas (`.env` + `settings.yaml`), zero hardcode.
2. **Fase 1 — Núcleo geométrico determinístico** (próximo passo):
   - `extractor.py`: spans com `origin`, `size`, `color`, `bbox`;
   - `renderer.py`: reconstrução por baseline, Helvetica, fontsize idêntico;
   - Validação visual: sobreposição original vs. reconstruído em notebooks.
3. **Fase 2 — Inteligência espacial (ML)** — roteamento contínuo/matricial, alinhamento de colunas numéricas.
4. **Fase 3 — Gaiola de proteção** — gate `needs_ocr`: detecção, log e rejeição/quarentena de escaneados (sem OCR caixa-preta).

**Critério de aceite da Fase 1:** PDF reconstruído mantém 100% dos spans na mesma
coordenada `origin` e mesmo `fontsize` do original, com zero vetores e zero imagens.
