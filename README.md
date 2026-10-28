# abc-xyz-inventory-solver
Data-Driven Inventory Solver via SQL & Python

Classifica produtos pela matriz **ABC/XYZ** (faturamento x previsibilidade da demanda), calcula
**estoque de segurança** e **ponto de ressuprimento** com nível de serviço por classe e entrega
um **dashboard** com os alertas de compra.

## Como rodar

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

python main.py --setup      # cria o banco, carrega os dados e calcula tudo
streamlit run app.py        # abre o dashboard
```

`python main.py` (sem `--setup`) recalcula sobre o banco existente.
O banco é um SQLite em `data/inventory.db`; para usar outro, defina `DATABASE_URL`.

### Dados

`seed.py` aceita duas fontes:

| Fonte | Como usar |
|---|---|
| **Olist** (Kaggle) | Baixe o [Brazilian E-Commerce Public Dataset by Olist](https://www.kaggle.com/datasets/olistbr/brazilian-ecommerce) e coloque `olist_orders_dataset.csv`, `olist_order_items_dataset.csv` e `olist_products_dataset.csv` em `data/raw/olist/`. Rode `python main.py --setup --fonte olist`. São mantidos os 300 produtos com mais pedidos (`python seed.py --top N` para mudar). |
| **Sintético** (padrão sem os CSVs) | 150 produtos, 2 anos de vendas diárias com demanda estável, sazonal (incl. Black Friday), com tendência e intermitente. |

A Olist não traz custo nem estoque, então o custo é estimado (margem de 25–55%) e a posição de
estoque e os lead times são simulados (tabela `estoque`).

## Estrutura

| Arquivo | Issue | Conteúdo |
|---|---|---|
| `sql/01_schema.sql` | #1 | Tabelas `produtos`, `vendas` e `estoque` |
| `seed.py` | #1 | Carga (etapa 02): Olist ou sintético |
| `sql/03_curva_abc_xyz.sql` | #2 | Views da curva ABC, demanda semanal, curva XYZ e matriz final |
| `inventory_solver.py`, `main.py` | #3 | Estoque de segurança, PR e alertas; grava a tabela `resultado_estoque` e `data/output/resultado_estoque.csv` |
| `app.py` | #4 | Dashboard Streamlit |
| `db.py` | — | Conexão SQLAlchemy e execução dos scripts SQL |

## Metodologia

**ABC** (`vw_curva_abc`): faturamento por produto (pedidos cancelados/indisponíveis excluídos),
ordenado de forma decrescente, com o percentual acumulado via `SUM() OVER (ORDER BY ...)`.
A = até 80% do faturamento, B = até 95%, C = restante.

**XYZ** (`vw_curva_xyz`): coeficiente de variação da demanda **semanal**, incluindo semanas sem
venda (calendário gerado por CTE recursiva), apenas semanas completas e a partir da primeira venda
do produto. X: CV ≤ 0,5 · Y: 0,5 < CV ≤ 1,0 · Z: CV > 1,0.

**Estoque de segurança e PR** (`inventory_solver.py`), com D e σD por semana e LT em semanas:

- ES = Z × √(LT × σD² + D² × σLT²) — com σLT = 0 vira Z × σD × √LT
- PR = D × LT + ES
- Nível de serviço: A 99% (Z = 2,33) · B 95% (Z = 1,64) · C 90% (Z = 1,28)

| Status | Regra |
|---|---|
| Estoque Crítico | estoque atual ≤ ES |
| Abaixo do PR | ES < estoque atual ≤ PR |
| Estoque Saudável | estoque atual > PR |

Compra sugerida = (PR + 4 semanas de demanda) − estoque atual (ajustável com `--cobertura`).

## Dashboard

- KPIs de produtos, faturamento, alertas e valor de compra sugerido
- Heatmap da matriz ABC/XYZ (nº de produtos ou faturamento) e dispersão faturamento x CV
- Tabela interativa dos produtos abaixo do PR, com exportação em CSV
- Filtros por classe ABC, classe XYZ e categoria
- Aba de insights (concentração AX/AZ, críticos classe A, capital em estoque de segurança)
