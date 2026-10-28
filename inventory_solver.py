"""Motor de cálculo de estoque de segurança e ponto de ressuprimento.

Consome a matriz ABC/XYZ (view vw_matriz_abc_xyz) e a posição de estoque, e calcula:

    Estoque de Segurança  ES = Z * sqrt(LT * σD² + D² * σLT²)
    Ponto de Ressuprimento PR = D * LT + ES

com D e σD em unidades por semana e LT e σLT em semanas. Se o desvio do lead time
for zero, a fórmula se reduz a Z * σD * sqrt(LT).

O nível de serviço (e, portanto, Z) depende da classe ABC.
"""

from statistics import NormalDist

import numpy as np
import pandas as pd
from sqlalchemy.engine import Engine

NIVEL_SERVICO = {"A": 0.99, "B": 0.95, "C": 0.90}

STATUS_CRITICO = "Estoque Crítico"
STATUS_ABAIXO_PR = "Abaixo do PR"
STATUS_SAUDAVEL = "Estoque Saudável"
ORDEM_STATUS = [STATUS_CRITICO, STATUS_ABAIXO_PR, STATUS_SAUDAVEL]

TABELA_RESULTADO = "resultado_estoque"

QUERY_ENTRADA = """
SELECT m.*,
       e.estoque_atual,
       e.lead_time_dias,
       e.lead_time_desvio_dias,
       e.data_posicao
FROM vw_matriz_abc_xyz m
JOIN estoque e ON e.id_produto = m.id_produto
"""


def fator_z(nivel_servico: float) -> float:
    """Quantil da normal padrão para o nível de serviço (ex: 0,95 -> 1,645)."""
    return NormalDist().inv_cdf(nivel_servico)


def carregar_dados(engine: Engine) -> pd.DataFrame:
    return pd.read_sql(QUERY_ENTRADA, engine)


def calcular_niveis(df: pd.DataFrame, nivel_servico: dict[str, float] | None = None,
                    cobertura_semanas: float = 4.0) -> pd.DataFrame:
    """Calcula ES, PR, status de alerta e sugestão de compra para cada produto.

    cobertura_semanas: semanas de demanda acima do PR usadas para definir o estoque
    máximo (PR + D * cobertura) e, a partir dele, a quantidade sugerida de compra.
    """
    nivel_servico = nivel_servico or NIVEL_SERVICO
    out = df.copy()

    out["nivel_servico"] = out["classe_abc"].map(nivel_servico)
    out["z"] = out["nivel_servico"].map(fator_z)

    d = out["demanda_media_semanal"]
    sigma_d = out["desvio_padrao_semanal"]
    lt = out["lead_time_dias"] / 7
    sigma_lt = out["lead_time_desvio_dias"] / 7

    out["estoque_seguranca"] = np.ceil(out["z"] * np.sqrt(lt * sigma_d**2 + d**2 * sigma_lt**2))
    out["demanda_lead_time"] = d * lt
    out["ponto_ressuprimento"] = np.ceil(out["demanda_lead_time"] + out["estoque_seguranca"])
    out["estoque_maximo"] = np.ceil(out["ponto_ressuprimento"] + d * cobertura_semanas)

    estoque = out["estoque_atual"]
    out["status_estoque"] = np.select(
        [estoque <= out["estoque_seguranca"], estoque <= out["ponto_ressuprimento"]],
        [STATUS_CRITICO, STATUS_ABAIXO_PR],
        default=STATUS_SAUDAVEL,
    )
    out["qtd_sugerida_compra"] = np.where(
        out["status_estoque"] != STATUS_SAUDAVEL,
        np.maximum(out["estoque_maximo"] - estoque, 0),
        0,
    ).astype(int)
    out["valor_sugerido_compra"] = out["qtd_sugerida_compra"] * out["custo_unitario"]
    out["cobertura_atual_semanas"] = np.where(d > 0, estoque / d.where(d > 0, 1), np.inf)

    out["status_estoque"] = pd.Categorical(out["status_estoque"], ORDEM_STATUS, ordered=True)
    return out.sort_values(["status_estoque", "faturamento"], ascending=[True, False]).reset_index(drop=True)


def salvar_resultado(df: pd.DataFrame, engine: Engine, tabela: str = TABELA_RESULTADO) -> None:
    saida = df.copy()
    saida["status_estoque"] = saida["status_estoque"].astype(str)
    saida["cobertura_atual_semanas"] = saida["cobertura_atual_semanas"].replace(np.inf, np.nan)
    saida.to_sql(tabela, engine, if_exists="replace", index=False)


def resumo(df: pd.DataFrame) -> pd.DataFrame:
    """Contagem de produtos e valor sugerido de compra por classe ABC e status."""
    return (
        df.groupby(["classe_abc", "status_estoque"], observed=False)
        .agg(produtos=("id_produto", "count"), valor_compra=("valor_sugerido_compra", "sum"))
        .unstack("status_estoque", fill_value=0)
    )
