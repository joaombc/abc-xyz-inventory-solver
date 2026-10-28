"""Pipeline completo: schema + carga -> matriz ABC/XYZ -> estoque de segurança e PR.

Uso:
    python main.py              # recalcula sobre o banco existente
    python main.py --setup      # recria o banco (schema + carga) antes de calcular
    python main.py --setup --fonte olist
"""

import argparse
from pathlib import Path

import pandas as pd

import inventory_solver as solver
from db import ROOT, get_engine, run_sql_file
from seed import popular

OUTPUT_DIR = ROOT / "data" / "output"


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--setup", action="store_true", help="recria o banco e popula os dados")
    parser.add_argument("--fonte", choices=["auto", "olist", "sintetico"], default="auto")
    parser.add_argument("--cobertura", type=float, default=4.0,
                        help="semanas de demanda acima do PR para o estoque máximo (padrão: 4)")
    parser.add_argument("--csv", type=Path, default=OUTPUT_DIR / "resultado_estoque.csv")
    args = parser.parse_args()

    engine = get_engine()
    if args.setup:
        popular(engine, args.fonte)
    run_sql_file(engine, "03_curva_abc_xyz.sql")

    resultado = solver.calcular_niveis(solver.carregar_dados(engine), cobertura_semanas=args.cobertura)
    solver.salvar_resultado(resultado, engine)
    args.csv.parent.mkdir(parents=True, exist_ok=True)
    resultado.to_csv(args.csv, index=False)

    with pd.option_context("display.width", 140, "display.float_format", "{:,.0f}".format):
        print("\nProdutos e valor sugerido de compra (R$) por classe e status:")
        print(solver.resumo(resultado).to_string())
        alertas = resultado[resultado["status_estoque"] != solver.STATUS_SAUDAVEL]
        print(f"\n{len(alertas)} produtos precisam de compra. Top 10 por faturamento:")
        print(
            alertas.sort_values("faturamento", ascending=False)
            .head(10)[["id_produto", "nome_produto", "classe_abc_xyz", "estoque_atual",
                       "estoque_seguranca", "ponto_ressuprimento", "qtd_sugerida_compra", "status_estoque"]]
            .to_string(index=False)
        )
    print(f"\nResultado salvo na tabela '{solver.TABELA_RESULTADO}' e em {args.csv}")


if __name__ == "__main__":
    main()
