-- 01_schema.sql
-- Estrutura do banco de dados (SQLite).
-- Executar: sqlite3 data/inventory.db < sql/01_schema.sql

PRAGMA foreign_keys = ON;

DROP TABLE IF EXISTS estoque;
DROP TABLE IF EXISTS vendas;
DROP TABLE IF EXISTS produtos;

-- Cadastro de produtos
CREATE TABLE produtos (
    id_produto      INTEGER PRIMARY KEY,
    nome_produto    TEXT    NOT NULL,
    categoria       TEXT,
    custo_unitario  REAL    NOT NULL CHECK (custo_unitario >= 0),
    preco_venda     REAL    NOT NULL CHECK (preco_venda >= 0),
    codigo_origem   TEXT    -- id do produto no dataset de origem (ex: product_id da Olist)
);

-- Itens vendidos (uma linha por produto em cada pedido)
CREATE TABLE vendas (
    id_venda       INTEGER PRIMARY KEY,
    id_produto     INTEGER NOT NULL REFERENCES produtos (id_produto),
    data_venda     DATE    NOT NULL,
    quantidade     INTEGER NOT NULL CHECK (quantidade > 0),
    status_pedido  TEXT    NOT NULL
        CHECK (status_pedido IN ('criado', 'aprovado', 'processando', 'faturado',
                                 'enviado', 'entregue', 'cancelado', 'indisponivel'))
);

CREATE INDEX idx_vendas_produto_data ON vendas (id_produto, data_venda);
CREATE INDEX idx_vendas_data ON vendas (data_venda);

-- Posição de estoque e parâmetros de reposição por produto.
-- Não faz parte do dataset de vendas: é necessária para o motor de ressuprimento (Issue 3)
-- e para os alertas do dashboard (Issue 4).
CREATE TABLE estoque (
    id_produto            INTEGER PRIMARY KEY REFERENCES produtos (id_produto),
    estoque_atual         INTEGER NOT NULL CHECK (estoque_atual >= 0),
    lead_time_dias        REAL    NOT NULL CHECK (lead_time_dias > 0),
    lead_time_desvio_dias REAL    NOT NULL DEFAULT 0 CHECK (lead_time_desvio_dias >= 0),
    data_posicao          DATE    NOT NULL
);
