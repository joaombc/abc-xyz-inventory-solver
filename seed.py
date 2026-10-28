"""Carga de dados (etapa 02): cria o schema e popula produtos, vendas e estoque.

Duas fontes:
  * olist     - dataset público "Brazilian E-Commerce Public Dataset by Olist" (Kaggle).
                Baixe e descompacte os CSVs em data/raw/olist/.
  * sintetico - gera um histórico de 2 anos com padrões de demanda variados
                (estável, sazonal, tendência e intermitente). Não precisa de download.

Uso:
    python seed.py                       # olist se os CSVs existirem, senão sintético
    python seed.py --fonte sintetico
    python seed.py --fonte olist --olist-dir data/raw/olist --top 300
"""

import argparse
from pathlib import Path

import numpy as np
import pandas as pd
from sqlalchemy.engine import Engine

from db import ROOT, get_engine, run_sql_file

OLIST_DIR = ROOT / "data" / "raw" / "olist"
OLIST_FILES = {
    "orders": "olist_orders_dataset.csv",
    "items": "olist_order_items_dataset.csv",
    "products": "olist_products_dataset.csv",
}
OLIST_STATUS = {
    "created": "criado",
    "approved": "aprovado",
    "processing": "processando",
    "invoiced": "faturado",
    "shipped": "enviado",
    "delivered": "entregue",
    "canceled": "cancelado",
    "unavailable": "indisponivel",
}
CATEGORIAS = [
    "eletronicos", "informatica", "casa_construcao", "cama_mesa_banho", "beleza_saude",
    "esporte_lazer", "brinquedos", "papelaria", "automotivo", "utilidades_domesticas",
]


# --------------------------------------------------------------------------- Olist

def olist_disponivel(olist_dir: Path) -> bool:
    return all((olist_dir / f).exists() for f in OLIST_FILES.values())


def carregar_olist(olist_dir: Path, top: int, rng: np.random.Generator):
    """Converte os CSVs da Olist para o modelo produtos/vendas.

    A Olist tem ~32 mil produtos, a maioria com 1 ou 2 vendas; mantemos os `top`
    produtos com mais pedidos para que a variabilidade da demanda faça sentido.
    """
    orders = pd.read_csv(
        olist_dir / OLIST_FILES["orders"],
        usecols=["order_id", "order_status", "order_purchase_timestamp"],
    )
    items = pd.read_csv(
        olist_dir / OLIST_FILES["items"], usecols=["order_id", "product_id", "price"]
    )
    products = pd.read_csv(
        olist_dir / OLIST_FILES["products"], usecols=["product_id", "product_category_name"]
    )

    # Cada linha de order_items é 1 unidade: agrupa por pedido + produto.
    linhas = (
        items.groupby(["order_id", "product_id"], as_index=False)
        .agg(quantidade=("price", "size"), preco=("price", "mean"))
        .merge(orders, on="order_id", how="inner")
    )
    pedidos_por_produto = linhas.groupby("product_id")["order_id"].nunique()
    selecionados = pedidos_por_produto.nlargest(top).index
    linhas = linhas[linhas["product_id"].isin(selecionados)]

    prod = (
        linhas.groupby("product_id", as_index=False)["preco"].median()
        .merge(products, on="product_id", how="left")
        .sort_values("product_id")
        .reset_index(drop=True)
    )
    prod["id_produto"] = np.arange(1, len(prod) + 1)
    prod["categoria"] = prod["product_category_name"].fillna("sem_categoria")
    prod["nome_produto"] = prod["categoria"] + " " + prod["product_id"].str[:8]
    prod["preco_venda"] = prod["preco"].round(2)
    # A Olist não traz custo: assume margem bruta entre 25% e 55%.
    prod["custo_unitario"] = (prod["preco_venda"] * rng.uniform(0.45, 0.75, len(prod))).round(2)
    prod["codigo_origem"] = prod["product_id"]

    vendas = linhas.merge(prod[["product_id", "id_produto"]], on="product_id")
    vendas["data_venda"] = pd.to_datetime(vendas["order_purchase_timestamp"]).dt.date
    vendas["status_pedido"] = vendas["order_status"].map(OLIST_STATUS).fillna("processando")
    vendas = vendas.sort_values(["data_venda", "id_produto"])[
        ["id_produto", "data_venda", "quantidade", "status_pedido"]
    ]

    produtos = prod[
        ["id_produto", "nome_produto", "categoria", "custo_unitario", "preco_venda", "codigo_origem"]
    ]
    return produtos, vendas


# ----------------------------------------------------------------------- Sintético

def gerar_sintetico(rng: np.random.Generator, n_produtos: int = 150,
                    inicio: str = "2024-10-01", fim: str = "2026-09-30"):
    dias = pd.date_range(inicio, fim, freq="D")
    t = np.arange(len(dias))
    dia_semana = dias.dayofweek.to_numpy()
    efeito_semana = np.where(dia_semana >= 5, 0.75, 1.08)  # fim de semana vende menos
    black_friday = np.where((dias.month == 11) & (dias.day >= 20), 2.2, 1.0)

    precos = np.clip(rng.lognormal(np.log(80), 1.0, n_produtos), 5, 3000).round(2)
    padroes = rng.choice(
        ["estavel", "sazonal", "tendencia", "intermitente"],
        size=n_produtos, p=[0.45, 0.25, 0.10, 0.20],
    )

    produtos, vendas = [], []
    for i in range(n_produtos):
        id_produto = i + 1
        preco = precos[i]
        categoria = CATEGORIAS[rng.integers(len(CATEGORIAS))]
        # Popularidade com cauda longa; itens caros vendem menos unidades.
        lam = rng.lognormal(0, 1.1) * 3.0 / (preco / 80) ** 0.4
        padrao = padroes[i]

        if padrao == "estavel":
            lam_t = lam * efeito_semana
        elif padrao == "sazonal":
            fase = rng.uniform(0, 2 * np.pi)
            lam_t = lam * (1 + 0.6 * np.sin(2 * np.pi * t / 365 + fase)) * efeito_semana * black_friday
        elif padrao == "tendencia":
            lam_t = lam * (0.4 + 1.2 * t / len(t)) * efeito_semana
        else:  # intermitente: pedidos raros e grandes
            lam_t = None

        if lam_t is not None:
            # Gamma-Poisson: mais dispersão que um Poisson puro.
            forma = rng.uniform(2, 8)
            qtd = rng.poisson(rng.gamma(forma, lam_t / forma))
        else:
            p = rng.uniform(0.03, 0.12)
            tamanho = max(lam / p, 1)
            ocorre = rng.random(len(t)) < p
            qtd = np.where(ocorre, rng.poisson(tamanho, len(t)) + 1, 0)

        # 10% dos produtos são lançados durante o primeiro ano.
        if rng.random() < 0.10:
            qtd[: rng.integers(30, 365)] = 0

        idx = np.flatnonzero(qtd > 0)
        if len(idx) == 0:
            continue
        status = rng.choice(
            ["entregue", "enviado", "faturado", "cancelado"], size=len(idx), p=[0.95, 0.02, 0.01, 0.02]
        )
        recentes = idx >= len(t) - 10
        status[recentes & (status == "entregue")] = "enviado"

        produtos.append({
            "id_produto": id_produto,
            "nome_produto": f"{categoria.replace('_', ' ').title()} {id_produto:03d}",
            "categoria": categoria,
            "custo_unitario": round(preco * rng.uniform(0.45, 0.75), 2),
            "preco_venda": preco,
            "codigo_origem": f"SKU-{id_produto:05d}",
        })
        vendas.append(pd.DataFrame({
            "id_produto": id_produto,
            "data_venda": dias[idx].date,
            "quantidade": qtd[idx],
            "status_pedido": status,
        }))

    vendas = pd.concat(vendas).sort_values(["data_venda", "id_produto"])
    return pd.DataFrame(produtos), vendas


# ------------------------------------------------------------------------- Estoque

def gerar_estoque(produtos: pd.DataFrame, vendas: pd.DataFrame,
                  rng: np.random.Generator) -> pd.DataFrame:
    """Simula a posição de estoque atual e os lead times de cada produto.

    O estoque é sorteado em dias de cobertura da demanda recente (média ~45), para que
    existam produtos saudáveis, abaixo do ponto de ressuprimento e em nível crítico.
    """
    validas = vendas[~vendas["status_pedido"].isin(["cancelado", "indisponivel"])]
    data_posicao = pd.to_datetime(vendas["data_venda"]).max()
    janela = pd.to_datetime(validas["data_venda"]) > data_posicao - pd.Timedelta(days=56)
    demanda_diaria = (
        validas[janela].groupby("id_produto")["quantidade"].sum().div(56)
        .reindex(produtos["id_produto"], fill_value=0)
    )

    n = len(produtos)
    lead_time = rng.choice([7, 10, 14, 21, 30, 45], size=n, p=[0.2, 0.2, 0.25, 0.15, 0.12, 0.08])
    cobertura = rng.gamma(2.5, 18.0, n)
    return pd.DataFrame({
        "id_produto": produtos["id_produto"].to_numpy(),
        "estoque_atual": np.round(demanda_diaria.to_numpy() * cobertura).astype(int),
        "lead_time_dias": lead_time.astype(float),
        "lead_time_desvio_dias": (lead_time * rng.uniform(0.0, 0.3, n)).round(1),
        "data_posicao": data_posicao.date(),
    })


# ---------------------------------------------------------------------------- Main

def popular(engine: Engine, fonte: str = "auto", olist_dir: Path = OLIST_DIR,
            top: int = 300, seed: int = 42) -> str:
    rng = np.random.default_rng(seed)
    if fonte == "auto":
        fonte = "olist" if olist_disponivel(olist_dir) else "sintetico"
    if fonte == "olist":
        if not olist_disponivel(olist_dir):
            raise FileNotFoundError(
                f"CSVs da Olist não encontrados em {olist_dir}. Esperado: {list(OLIST_FILES.values())}"
            )
        produtos, vendas = carregar_olist(olist_dir, top, rng)
    else:
        produtos, vendas = gerar_sintetico(rng)
    estoque = gerar_estoque(produtos, vendas, rng)

    run_sql_file(engine, "01_schema.sql")
    with engine.begin() as conn:
        produtos.to_sql("produtos", conn, if_exists="append", index=False)
        vendas.to_sql("vendas", conn, if_exists="append", index=False, chunksize=10_000)
        estoque.to_sql("estoque", conn, if_exists="append", index=False)

    print(f"Fonte: {fonte} | produtos: {len(produtos)} | vendas: {len(vendas)} | "
          f"período: {vendas['data_venda'].min()} a {vendas['data_venda'].max()}")
    return fonte


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--fonte", choices=["auto", "olist", "sintetico"], default="auto")
    parser.add_argument("--olist-dir", type=Path, default=OLIST_DIR)
    parser.add_argument("--top", type=int, default=300, help="nº de produtos mantidos da Olist")
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    popular(get_engine(), args.fonte, args.olist_dir, args.top, args.seed)


if __name__ == "__main__":
    main()
