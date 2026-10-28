"""Dashboard operacional ABC/XYZ para o gestor de compras.

Executar:  streamlit run app.py
(o banco precisa existir: rode `python main.py --setup` uma vez)
"""

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
from sqlalchemy import inspect

import inventory_solver as solver
from db import get_engine, run_sql_file

CLASSES_ABC = ["A", "B", "C"]
CLASSES_XYZ = ["X", "Y", "Z"]
CORES_ABC = {"A": "#2563eb", "B": "#f59e0b", "C": "#94a3b8"}
CORES_STATUS = {
    solver.STATUS_CRITICO: "#dc2626",
    solver.STATUS_ABAIXO_PR: "#f59e0b",
    solver.STATUS_SAUDAVEL: "#16a34a",
}
DESCRICAO_XYZ = {"X": "previsível", "Y": "variável", "Z": "imprevisível"}

st.set_page_config(page_title="Inventory Solver ABC/XYZ", page_icon="📦", layout="wide")


def recalcular(engine) -> None:
    run_sql_file(engine, "03_curva_abc_xyz.sql")
    solver.salvar_resultado(solver.calcular_niveis(solver.carregar_dados(engine)), engine)


@st.cache_data(show_spinner="Carregando dados...")
def carregar() -> pd.DataFrame:
    engine = get_engine()
    tabelas = inspect(engine).get_table_names()
    if "produtos" not in tabelas:
        return pd.DataFrame()
    if solver.TABELA_RESULTADO not in tabelas:
        recalcular(engine)
    df = pd.read_sql(f"SELECT * FROM {solver.TABELA_RESULTADO}", engine)
    df["status_estoque"] = pd.Categorical(df["status_estoque"], solver.ORDEM_STATUS, ordered=True)
    df["capital_estoque_seguranca"] = df["estoque_seguranca"] * df["custo_unitario"]
    return df


def brl(valor: float) -> str:
    return f"R$ {valor:,.0f}".replace(",", ".")


def brl_compacto(valor: float) -> str:
    if abs(valor) >= 1e6:
        return f"R$ {valor / 1e6:,.1f} mi".replace(".", ",")
    if abs(valor) >= 1e3:
        return f"R$ {valor / 1e3:,.0f} mil"
    return brl(valor)


dados = carregar()
if dados.empty:
    st.error("Banco não encontrado. Rode `python main.py --setup` e recarregue a página.")
    st.stop()

# ------------------------------------------------------------------- Filtros
with st.sidebar:
    st.header("Filtros")
    classes = st.multiselect("Classe ABC", CLASSES_ABC, default=CLASSES_ABC,
                             help="A = 80% do faturamento, B = 15%, C = 5%")
    xyz = st.multiselect("Classe XYZ", CLASSES_XYZ, default=CLASSES_XYZ,
                         format_func=lambda c: f"{c} ({DESCRICAO_XYZ[c]})")
    categorias = sorted(dados["categoria"].dropna().unique())
    cats = st.multiselect("Categoria", categorias, placeholder="Todas")

    st.divider()
    st.caption("Nível de serviço por classe: "
               + " · ".join(f"{c} {v:.0%}" for c, v in solver.NIVEL_SERVICO.items()))
    st.caption(f"Posição de estoque em {dados['data_posicao'].iloc[0]}")
    if st.button("Recalcular a partir do banco", width="stretch"):
        recalcular(get_engine())
        carregar.clear()
        st.rerun()

df = dados[dados["classe_abc"].isin(classes) & dados["classe_xyz"].isin(xyz)]
if cats:
    df = df[df["categoria"].isin(cats)]
alertas = df[df["status_estoque"] != solver.STATUS_SAUDAVEL]

st.title("📦 Inventory Solver — Matriz ABC/XYZ")
if df.empty:
    st.warning("Nenhum produto com os filtros selecionados.")
    st.stop()

# ---------------------------------------------------------------------- KPIs
k1, k2, k3, k4, k5 = st.columns(5)
k1.metric("Produtos", len(df))
k2.metric("Faturamento", brl_compacto(df["faturamento"].sum()))
k3.metric("Abaixo do PR", len(alertas), help="Inclui os críticos (abaixo do estoque de segurança)")
k4.metric("Estoque crítico", int((df["status_estoque"] == solver.STATUS_CRITICO).sum()))
k5.metric("Compra sugerida", brl_compacto(alertas["valor_sugerido_compra"].sum()))

aba_matriz, aba_alertas, aba_insights = st.tabs(["Matriz ABC/XYZ", "Alertas de compra", "Insights"])

# ------------------------------------------------------------------- Matriz
with aba_matriz:
    metrica = st.radio("Colorir matriz por", ["Nº de produtos", "Faturamento"], horizontal=True)
    grade = (
        df.groupby(["classe_abc", "classe_xyz"])
        .agg(produtos=("id_produto", "count"), faturamento=("faturamento", "sum"))
        .reindex(pd.MultiIndex.from_product([CLASSES_ABC, CLASSES_XYZ]), fill_value=0)
    )
    grade["pct_fat"] = grade["faturamento"] / grade["faturamento"].sum()
    valores = grade["produtos" if metrica == "Nº de produtos" else "faturamento"].unstack()
    texto = (
        grade["produtos"].astype(str) + " produtos<br>"
        + (grade["pct_fat"] * 100).round(1).astype(str) + "% do fat."
    ).unstack()

    col_heat, col_scatter = st.columns([2, 3])
    with col_heat:
        fig = go.Figure(go.Heatmap(
            z=valores.values,
            x=[f"{c} · {DESCRICAO_XYZ[c]}" for c in CLASSES_XYZ],
            y=[f"Classe {c}" for c in CLASSES_ABC],
            text=texto.values, texttemplate="%{text}", textfont={"size": 13},
            colorscale="Blues", showscale=False,
            hovertemplate="%{y} / %{x}<br>%{text}<extra></extra>",
        ))
        fig.update_yaxes(autorange="reversed")
        fig.update_layout(title="Distribuição dos produtos", height=420, margin=dict(t=50, b=10, l=10, r=10))
        st.plotly_chart(fig, width="stretch")

    with col_scatter:
        plot = df.dropna(subset=["coef_variacao"]).assign(
            faturamento_plot=lambda d: d["faturamento"].clip(lower=1)
        )
        fig = px.scatter(
            plot, x="coef_variacao", y="faturamento_plot", color="classe_abc",
            color_discrete_map=CORES_ABC, category_orders={"classe_abc": CLASSES_ABC},
            log_y=True,
            hover_name="nome_produto",
            hover_data={"classe_abc_xyz": True, "faturamento_plot": False,
                        "faturamento": ":,.0f", "coef_variacao": ":.2f",
                        "demanda_media_semanal": ":.1f", "status_estoque": True, "classe_abc": False},
            labels={"coef_variacao": "Coeficiente de variação (demanda semanal)",
                    "faturamento_plot": "Faturamento (R$, escala log)",
                    "classe_abc": "Classe", "status_estoque": "Status"},
        )
        for limite in (0.5, 1.0):
            fig.add_vline(x=limite, line_dash="dash", line_color="gray", opacity=0.6)
        fig.update_layout(title="Faturamento x variabilidade", height=420, margin=dict(t=50, b=10, l=10, r=10),
                          legend=dict(orientation="h", yanchor="bottom", y=1.0, xanchor="right", x=1))
        st.plotly_chart(fig, width="stretch")
    st.caption("Linhas tracejadas: fronteiras X | Y (CV = 0,5) e Y | Z (CV = 1,0).")

# ------------------------------------------------------------------ Alertas
with aba_alertas:
    st.subheader(f"{len(alertas)} produtos com estoque abaixo do ponto de ressuprimento")
    if alertas.empty:
        st.success("Nenhum produto abaixo do PR com os filtros atuais.")
    else:
        tabela = alertas.assign(
            pct_do_pr=lambda d: (d["estoque_atual"] / d["ponto_ressuprimento"].where(d["ponto_ressuprimento"] > 0)).clip(upper=1),
        ).sort_values(["status_estoque", "faturamento"], ascending=[True, False])
        colunas = {
            "status_estoque": st.column_config.TextColumn("Status"),
            "id_produto": st.column_config.NumberColumn("ID", format="%d"),
            "nome_produto": "Produto",
            "classe_abc_xyz": "Classe",
            "estoque_atual": st.column_config.NumberColumn("Estoque", format="%d"),
            "estoque_seguranca": st.column_config.NumberColumn("Est. segurança", format="%d"),
            "ponto_ressuprimento": st.column_config.NumberColumn("PR", format="%d"),
            "pct_do_pr": st.column_config.ProgressColumn("Estoque / PR", format="percent", min_value=0, max_value=1),
            "cobertura_atual_semanas": st.column_config.NumberColumn("Cobertura (sem.)", format="%.1f"),
            "lead_time_dias": st.column_config.NumberColumn("Lead time (dias)", format="%d"),
            "qtd_sugerida_compra": st.column_config.NumberColumn("Comprar (un.)", format="%d"),
            "valor_sugerido_compra": st.column_config.NumberColumn("Valor compra", format="R$ %.0f"),
            "faturamento": st.column_config.NumberColumn("Faturamento", format="R$ %.0f"),
        }
        st.dataframe(
            tabela[list(colunas)].style.map(
                lambda s: f"color: {CORES_STATUS.get(s, 'inherit')}; font-weight: 600", subset=["status_estoque"]
            ),
            column_config=colunas, hide_index=True, width="stretch", height=520,
        )
        st.download_button(
            "Baixar lista de compras (CSV)",
            tabela[list(colunas)].to_csv(index=False).encode("utf-8-sig"),
            file_name="alertas_compra.csv", mime="text/csv",
        )
        st.caption("Estoque Crítico: estoque ≤ estoque de segurança. Abaixo do PR: estoque ≤ ponto de "
                   "ressuprimento. Compra sugerida = PR + 4 semanas de demanda − estoque atual.")

# ----------------------------------------------------------------- Insights
with aba_insights:
    total_fat = df["faturamento"].sum()
    az = df[df["classe_abc_xyz"] == "AZ"]
    ax = df[df["classe_abc_xyz"] == "AX"]
    criticos_a = df[(df["classe_abc"] == "A") & (df["status_estoque"] == solver.STATUS_CRITICO)]
    capital = df.groupby("classe_xyz")["capital_estoque_seguranca"].sum().reindex(CLASSES_XYZ, fill_value=0)

    c1, c2 = st.columns(2)
    with c1:
        st.markdown("#### O que os números dizem")
        linhas = []
        if not ax.empty:
            linhas.append(f"- **{len(ax)} produtos AX** respondem por {ax['faturamento'].sum() / total_fat:.0%} "
                          "do faturamento com demanda previsível: candidatos a reposição automática e "
                          "estoque enxuto.")
        if not az.empty:
            linhas.append(f"- **{len(az)} produtos AZ** geram {az['faturamento'].sum() / total_fat:.0%} do "
                          "faturamento, mas a demanda é imprevisível: exigem o maior estoque de segurança "
                          "e acompanhamento próximo do comprador.")
        if not criticos_a.empty:
            linhas.append(f"- **{len(criticos_a)} produtos classe A estão em nível crítico** — risco direto de "
                          f"ruptura. Compra sugerida: {brl(criticos_a['valor_sugerido_compra'].sum())}.")
        if capital.sum() > 0:
            linhas.append(f"- Produtos Z concentram {capital['Z'] / capital.sum():.0%} do capital empatado em "
                          "estoque de segurança. Reduzir a variabilidade (previsão, acordos de entrega) "
                          "libera caixa.")
        st.markdown("\n".join(linhas) or "Sem insights para os filtros atuais.")
    with c2:
        fig = px.bar(
            df.groupby(["classe_xyz", "classe_abc"], as_index=False)["capital_estoque_seguranca"].sum(),
            x="classe_xyz", y="capital_estoque_seguranca", color="classe_abc",
            color_discrete_map=CORES_ABC, category_orders={"classe_abc": CLASSES_ABC, "classe_xyz": CLASSES_XYZ},
            labels={"classe_xyz": "Classe XYZ", "capital_estoque_seguranca": "R$ em estoque de segurança",
                    "classe_abc": "Classe ABC"},
            title="Capital em estoque de segurança",
        )
        fig.update_layout(height=360, margin=dict(t=50, b=10, l=10, r=10))
        st.plotly_chart(fig, width="stretch")
