-- 03_curva_abc_xyz.sql
-- Classificação ABC (faturamento) x XYZ (variabilidade da demanda semanal).
-- Executar depois do schema e da carga: sqlite3 data/inventory.db < sql/03_curva_abc_xyz.sql
--
-- Regras:
--   ABC: produtos ordenados por faturamento decrescente.
--        A = produtos que formam os primeiros 80% do faturamento acumulado,
--        B = os 15% seguintes (até 95%), C = os 5% finais.
--   XYZ: coeficiente de variação (CV = desvio padrão / média) da demanda semanal.
--        X: CV <= 0,5 | Y: 0,5 < CV <= 1,0 | Z: CV > 1,0 (ou sem demanda)

DROP VIEW IF EXISTS vw_matriz_abc_xyz;
DROP VIEW IF EXISTS vw_curva_xyz;
DROP VIEW IF EXISTS vw_demanda_semanal;
DROP VIEW IF EXISTS vw_curva_abc;
DROP VIEW IF EXISTS vw_vendas_validas;

-- Vendas que geraram receita (exclui pedidos cancelados/indisponíveis).
CREATE VIEW vw_vendas_validas AS
SELECT v.id_venda,
       v.id_produto,
       DATE(v.data_venda) AS data_venda,
       v.quantidade,
       v.quantidade * p.preco_venda AS receita
FROM vendas v
JOIN produtos p ON p.id_produto = v.id_produto
WHERE v.status_pedido NOT IN ('cancelado', 'indisponivel');


-- ----------------------------------------------------------------- Curva ABC
CREATE VIEW vw_curva_abc AS
WITH faturamento AS (
    SELECT p.id_produto,
           COALESCE(SUM(v.receita), 0)    AS faturamento,
           COALESCE(SUM(v.quantidade), 0) AS unidades_vendidas
    FROM produtos p
    LEFT JOIN vw_vendas_validas v ON v.id_produto = p.id_produto
    GROUP BY p.id_produto
),
acumulado AS (
    SELECT id_produto,
           faturamento,
           unidades_vendidas,
           faturamento / SUM(faturamento) OVER ()                         AS pct_faturamento,
           SUM(faturamento) OVER (ORDER BY faturamento DESC, id_produto
                                  ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
               / SUM(faturamento) OVER ()                                  AS pct_acumulado,
           RANK() OVER (ORDER BY faturamento DESC)                         AS ranking
    FROM faturamento
)
SELECT id_produto,
       faturamento,
       unidades_vendidas,
       pct_faturamento,
       pct_acumulado,
       ranking,
       -- Classifica pelo acumulado ANTES do produto: o item que cruza a fronteira
       -- dos 80% ainda é A (garante que o 1º produto seja sempre A).
       CASE
           WHEN pct_acumulado - pct_faturamento < 0.80 THEN 'A'
           WHEN pct_acumulado - pct_faturamento < 0.95 THEN 'B'
           ELSE 'C'
       END AS classe_abc
FROM acumulado;


-- ------------------------------------------------------ Demanda semanal (com zeros)
-- Semanas começam na segunda-feira. Considera apenas semanas completas do período e,
-- para cada produto, a partir da semana da sua primeira venda (evita penalizar
-- lançamentos com semanas "zeradas" antes de existirem).
CREATE VIEW vw_demanda_semanal AS
WITH RECURSIVE
limites AS (
    SELECT DATE(MIN(data_venda), 'weekday 1')            AS primeira_semana, -- 1ª segunda-feira
           DATE(MAX(data_venda), '-6 days', 'weekday 0', '-6 days') AS ultima_semana -- seg. da última semana completa
    FROM vw_vendas_validas
),
semanas (semana) AS (
    SELECT primeira_semana FROM limites
    UNION ALL
    SELECT DATE(semana, '+7 days') FROM semanas, limites
    WHERE semana < ultima_semana
),
inicio_produto AS (
    SELECT id_produto,
           MAX(DATE(MIN(data_venda), '-6 days', 'weekday 1'), (SELECT primeira_semana FROM limites)) AS semana_inicio
    FROM vw_vendas_validas
    GROUP BY id_produto
),
vendas_semana AS (
    SELECT id_produto,
           DATE(data_venda, '-6 days', 'weekday 1') AS semana,
           SUM(quantidade) AS quantidade
    FROM vw_vendas_validas
    GROUP BY id_produto, semana
)
SELECT ip.id_produto,
       s.semana,
       COALESCE(vs.quantidade, 0) AS quantidade
FROM inicio_produto ip
JOIN semanas s ON s.semana >= ip.semana_inicio
LEFT JOIN vendas_semana vs ON vs.id_produto = ip.id_produto AND vs.semana = s.semana;


-- ----------------------------------------------------------------- Curva XYZ
CREATE VIEW vw_curva_xyz AS
WITH estatisticas AS (
    SELECT id_produto,
           COUNT(*)                     AS n_semanas,
           SUM(quantidade > 0)          AS semanas_com_venda,
           AVG(quantidade)              AS demanda_media_semanal,
           AVG(quantidade * quantidade) AS media_quadrados
    FROM vw_demanda_semanal
    GROUP BY id_produto
),
desvio AS (
    SELECT *,
           -- Desvio padrão amostral: sqrt( (Σx² - n·média²) / (n - 1) )
           CASE WHEN n_semanas > 1
                THEN SQRT(MAX(n_semanas * (media_quadrados - demanda_media_semanal * demanda_media_semanal)
                              / (n_semanas - 1), 0))
                ELSE 0
           END AS desvio_padrao_semanal
    FROM estatisticas
)
SELECT id_produto,
       n_semanas,
       semanas_com_venda,
       demanda_media_semanal,
       desvio_padrao_semanal,
       CASE WHEN demanda_media_semanal > 0
            THEN desvio_padrao_semanal / demanda_media_semanal
       END AS coef_variacao,
       CASE
           WHEN demanda_media_semanal IS NULL OR demanda_media_semanal = 0 THEN 'Z'
           WHEN desvio_padrao_semanal / demanda_media_semanal <= 0.5 THEN 'X'
           WHEN desvio_padrao_semanal / demanda_media_semanal <= 1.0 THEN 'Y'
           ELSE 'Z'
       END AS classe_xyz
FROM desvio;


-- ------------------------------------------------------------- Matriz ABC/XYZ
CREATE VIEW vw_matriz_abc_xyz AS
SELECT p.id_produto,
       p.nome_produto,
       p.categoria,
       p.custo_unitario,
       p.preco_venda,
       abc.faturamento,
       abc.unidades_vendidas,
       abc.pct_faturamento,
       abc.pct_acumulado,
       abc.ranking,
       abc.classe_abc,
       COALESCE(xyz.n_semanas, 0)                AS n_semanas,
       COALESCE(xyz.semanas_com_venda, 0)        AS semanas_com_venda,
       COALESCE(xyz.demanda_media_semanal, 0)    AS demanda_media_semanal,
       COALESCE(xyz.desvio_padrao_semanal, 0)    AS desvio_padrao_semanal,
       xyz.coef_variacao,
       COALESCE(xyz.classe_xyz, 'Z')             AS classe_xyz,
       abc.classe_abc || COALESCE(xyz.classe_xyz, 'Z') AS classe_abc_xyz
FROM produtos p
JOIN vw_curva_abc abc ON abc.id_produto = p.id_produto
LEFT JOIN vw_curva_xyz xyz ON xyz.id_produto = p.id_produto;
