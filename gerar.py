#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Fagulha — gerador do site.

Lê a aba "Site" da planilha (CSV publicado) e as imagens de 4_finais/,
converte as imagens para WebP e monta o site estático na pasta _site/.

Uso:  python3 gerar.py --acervo "/caminho/para/Acervo_Fagulha"
"""
import argparse
import csv
import io
import json
import os
import re
import shutil
import sys
import urllib.request
from concurrent.futures import ProcessPoolExecutor
from datetime import date

# ---------------------------------------------------------------- configuração

CSV_URL = ("https://docs.google.com/spreadsheets/d/e/2PACX-1vRbjAu3tP5VZ6YOGFkQrt65T6aupTYfNgyGRp-"
           "1Pt12r7UESvGqA7nN3cNaU_P0NEfFvpJ003d_x3Du/pub?gid=345018291&single=true&output=csv")

SITE_URL = "https://yaslamoreira.github.io/fagulha/"

# Rótulo de procedência para cada conjunto (coluna "conjunto").
# Para um conjunto novo, acrescente uma linha aqui.
CONJUNTOS = {
    "c01": "Coleção de Ivan Pinheiro Bezerra",
    "c03": "Material avulso cedido por colaborador da pesquisa",
}

AVISO_PARCIAL = "Nem todas as páginas do impresso foram fotografadas."

# Tamanhos das imagens (em pixels)
PAGINA_LADO_MAIOR = 1800      # páginas e faces no visualizador
MINIATURA_LARGURA = 480       # capas na grade e na abertura (1x)
MINIATURA_2X = 960            # versão para telas de alta densidade (2x)
ABERTURA_LADO_MAIOR = 1000    # peças da abertura

RECURSOS = [  # (coluna, rótulo)
    ("letreiramento_manual", "Letreiramento manual"),
    ("datilografia", "Datilografia"),
    ("tipos_fontes", "Tipos ou fontes digitais"),
    ("ilustracao_mao", "Ilustração à mão"),
    ("ilustracao_digital", "Ilustração digital"),
    ("fotografia", "Fotografia"),
    ("marca", "Marca"),
]
VERBAIS = {"Letreiramento manual", "Datilografia", "Tipos ou fontes digitais"}
ASSUNTOS = [
    ("simbolos_juninos", "Símbolos juninos"),
    ("religioso", "Religioso"),
    ("pessoas", "Pessoas"),
    ("cidade", "Cidade"),
    ("musica_danca", "Música e dança"),
    ("vaquejada", "Vaquejada"),
]
INTERVENCOES = [("carimbo", "Carimbo"), ("anotacao", "Anotação"), ("recorte", "Recorte")]

COLUNAS_OBRIGATORIAS = (
    ["codigo", "titulo", "tipo_documental", "conjunto", "data", "numero", "paginas_qtd",
     "paginas_unidade", "autoria_responsaveis", "impressao_responsaveis", "realizacao_apoio",
     "cores_qtd", "cores_descricao", "integridade", "nota_acervo"]
    + [c for c, _ in RECURSOS + ASSUNTOS + INTERVENCOES])

AQUI = os.path.dirname(os.path.abspath(__file__))
SAIDA = os.path.join(AQUI, "_site")
CACHE = os.path.join(AQUI, "_cache")


class Problema(Exception):
    """Erro com mensagem em português para a autora."""


def aviso(msg):
    print("   ! " + msg)


# ---------------------------------------------------------------- planilha

def baixar_planilha():
    try:
        with urllib.request.urlopen(CSV_URL, timeout=60) as r:
            texto = r.read().decode("utf-8-sig")
    except Exception as e:
        raise Problema("Não consegui baixar a planilha. Confira a conexão com a internet.\n"
                       f"   (detalhe técnico: {e})")
    linhas = list(csv.DictReader(io.StringIO(texto)))
    if not linhas:
        raise Problema("A planilha veio vazia.")
    faltando = [c for c in COLUNAS_OBRIGATORIAS if c not in linhas[0]]
    if faltando:
        raise Problema("Estas colunas sumiram da aba Site da planilha: " + ", ".join(faltando))
    return [l for l in linhas if l["codigo"].strip()]


def limpar(v):
    v = (v or "").strip()
    return "" if v in ("N/A",) else v


def sim(v):
    return (v or "").strip().lower() == "sim"


def lista_sim(linha, campos, vazio, todos_na):
    valores = [(linha.get(c) or "").strip() for c, _ in campos]
    if todos_na and all(v == "N/A" for v in valores):
        return todos_na
    marcados = [rot for (c, rot) in campos if sim(linha.get(c))]
    return ", ".join(marcados) if marcados else vazio


def decada(ano):
    if ano == 2000:
        return "Ano de 2000"
    return str(ano // 10 * 10)


# ---------------------------------------------------------------- imagens

def ident(nome):
    """MUT_1974_n70_c01_p02 -> mut-1974-n70-c01-p02"""
    return nome.lower().replace("_", "-")


def proporcao(caminho):
    from PIL import Image
    with Image.open(caminho) as im:
        return round(im.width / im.height, 4)


def _converter(tarefa):
    """Roda em paralelo: (origem, destino, modo, tamanho)."""
    from PIL import Image
    origem, destino, modo, tamanho = tarefa
    with Image.open(origem) as im:
        im.load()
        if im.mode not in ("RGBA", "RGB"):
            im = im.convert("RGBA")
        if modo == "lado":
            im.thumbnail((tamanho, tamanho), Image.LANCZOS, reducing_gap=3.0)
        else:  # largura fixa
            if im.width > tamanho:
                alt = round(im.height * tamanho / im.width)
                im = im.resize((tamanho, alt), Image.LANCZOS, reducing_gap=3.0)
        tmp = destino + ".tmp"
        im.save(tmp, "WEBP", quality=82, method=5)
        os.replace(tmp, destino)
    return destino


# ---------------------------------------------------------------- montagem

def montar(acervo):
    finais = os.path.join(acervo, "4_finais")
    capas_dir = os.path.join(finais, "capas")
    if not os.path.isdir(finais):
        raise Problema(f"Não encontrei a pasta 4_finais dentro de {acervo}")

    print("1. Baixando a planilha…")
    linhas = baixar_planilha()
    print(f"   {len(linhas)} itens na planilha.")

    print("2. Conferindo as imagens de 4_finais…")
    padrao = re.compile(r"^(.+)_([pf])(\d+)\.png$", re.I)
    por_codigo = {}
    for nome in sorted(os.listdir(finais)):
        m = padrao.match(nome)
        if m:
            por_codigo.setdefault(m.group(1), []).append(nome)
        elif nome.lower().endswith(".png"):
            aviso(f"Imagem com nome fora do padrão (ignorada): {nome}")
    capas = {}
    if os.path.isdir(capas_dir):
        for nome in sorted(os.listdir(capas_dir)):
            if nome.lower().endswith("_capa.png"):
                capas[nome[:-9]] = nome

    codigos = [l["codigo"].strip() for l in linhas]
    repetidos = sorted({c for c in codigos if codigos.count(c) > 1})
    if repetidos:
        raise Problema("Códigos repetidos na planilha: " + ", ".join(repetidos))
    sem_imagem = [c for c in codigos if c not in por_codigo]
    if sem_imagem:
        raise Problema("Estes itens da planilha não têm imagens em 4_finais:\n     "
                       + "\n     ".join(sem_imagem))
    for c in sorted(set(por_codigo) - set(codigos)):
        aviso(f"Há imagens de {c} em 4_finais, mas o item não está na planilha (não será publicado).")
    for c in sorted(set(capas) - set(codigos)):
        aviso(f"Há capa de {c}, mas o item não está na planilha (não será publicada).")

    # tarefas de conversão
    img_dir = os.path.join(SAIDA, "img")
    os.makedirs(img_dir, exist_ok=True)
    os.makedirs(CACHE, exist_ok=True)
    reg_path = os.path.join(CACHE, "imagens.json")
    try:
        registro = json.load(open(reg_path, encoding="utf-8"))
    except Exception:
        registro = {}
    tarefas, esperados, novo_registro = [], set(), {}

    def pedir(origem, nome_saida, modo, tamanho):
        destino = os.path.join(img_dir, nome_saida)
        st = os.stat(origem)
        assinatura = [os.path.relpath(origem, acervo), st.st_size, int(st.st_mtime), modo, tamanho]
        esperados.add(nome_saida)
        novo_registro[nome_saida] = assinatura
        if nome_saida in [os.path.basename(t[1]) for t in tarefas]:
            return
        if registro.get(nome_saida) != assinatura or not os.path.exists(destino):
            tarefas.append((origem, destino, modo, tamanho))

    itens = []
    for l in linhas:
        cod = l["codigo"].strip()
        m = re.search(r"_(\d{4})_", "_" + cod + "_")
        if not m:
            raise Problema(f"Não achei o ano no código {cod}")
        ano = int(m.group(1))
        conj = l["conjunto"].strip()
        if conj not in CONJUNTOS:
            raise Problema(f"O conjunto '{conj}' do item {cod} não tem rótulo de procedência.\n"
                           "   Acrescente-o em CONJUNTOS, no início do arquivo gerar.py.")
        paginas = []
        for nome in por_codigo[cod]:
            origem = os.path.join(finais, nome)
            pid = ident(nome[:-4])
            pedir(origem, pid + ".webp", "lado", PAGINA_LADO_MAIOR)
            pedir(origem, pid + "-t.webp", "largura", MINIATURA_LARGURA)  # aparece antes da grande
            paginas.append([pid, proporcao(origem)])
        # capa: arquivo de capa, se houver; senão, a primeira imagem
        if cod in capas:
            origem_capa = os.path.join(capas_dir, capas[cod])
            cid = ident(cod) + "-capa"
        else:
            origem_capa = os.path.join(finais, por_codigo[cod][0])
            cid = paginas[0][0]
        if cid != paginas[0][0]:
            pedir(origem_capa, cid + "-t.webp", "largura", MINIATURA_LARGURA)
        pedir(origem_capa, cid + "-t2.webp", "largura", MINIATURA_2X)
        pedir(origem_capa, cid + "-h.webp", "lado", ABERTURA_LADO_MAIOR)

        rec = [rot for c, rot in RECURSOS if sim(l.get(c))]
        qtd_cores = l["cores_qtd"].strip()
        cores = l["cores_descricao"].strip()
        if qtd_cores and qtd_cores != "1" and cores:
            cores = f"{qtd_cores} ({cores[:1].lower() + cores[1:]})"
        notas = []
        if not l["paginas_qtd"].strip():
            notas.append(AVISO_PARCIAL)
        if l["nota_acervo"].strip():
            notas.append(l["nota_acervo"].strip())

        itens.append({
            "id": ident(cod), "code": cod, "t": l["titulo"].strip(),
            "tp": l["tipo_documental"].strip(), "d": l["data"].strip(),
            "n": limpar(l["numero"]), "q": l["paginas_qtd"].strip(),
            "u": l["paginas_unidade"].strip() or "Páginas",
            "au": limpar(l["autoria_responsaveis"]) or "Não consta",
            "im": limpar(l["impressao_responsaveis"]) or "Não consta",
            "re": limpar(l["realizacao_apoio"]) or "Não consta",
            "proc": CONJUNTOS[conj],
            "rec": rec, "verb": [r for r in rec if r in VERBAIS],
            "nverb": [r for r in rec if r not in VERBAIS],
            "cor": cores,
            "ass": lista_sim(l, ASSUNTOS, "Nenhum", "Não se aplica"),
            "ig": l["integridade"].strip(),
            "inter": lista_sim(l, INTERVENCOES, "Nenhuma", None),
            "notas": notas,
            "dec": decada(ano), "y": ano,
            "g": paginas, "c": [cid, proporcao(origem_capa)],
        })

    itens.sort(key=lambda o: (o["y"], o["code"]))
    for k, o in enumerate(itens, 1):
        o["no"] = f"{k:02d}"

    print(f"   {sum(len(o['g']) for o in itens)} imagens de páginas e {len(itens)} capas.")

    print("3. Convertendo imagens para WebP…")
    if tarefas:
        print(f"   {len(tarefas)} arquivos novos ou alterados (pode levar alguns minutos).")
        with ProcessPoolExecutor(max_workers=max(2, (os.cpu_count() or 4) - 1)) as ex:
            for k, _ in enumerate(ex.map(_converter, tarefas), 1):
                if k % 10 == 0 or k == len(tarefas):
                    print(f"   {k}/{len(tarefas)}")
    else:
        print("   Nenhuma imagem mudou desde a última vez.")
    for nome in os.listdir(img_dir):
        if nome not in esperados:
            os.remove(os.path.join(img_dir, nome))
    with open(reg_path, "w", encoding="utf-8") as f:
        json.dump(novo_registro, f, ensure_ascii=False, indent=0)

    print("4. Montando as páginas…")
    modelo = open(os.path.join(AQUI, "modelo.html"), encoding="utf-8").read()
    dados = json.dumps(itens, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    if "/*DADOS*/[]" not in modelo:
        raise Problema("O arquivo modelo.html está sem o marcador /*DADOS*/[].")
    html = modelo.replace("/*DADOS*/[]", dados).replace("{{SITE_URL}}", SITE_URL)
    # pré-carrega as capas da abertura (prioridade no carregamento)
    m = re.search(r"IDS=\{(.*?)\}", modelo)
    escolhidas = re.findall(r":'([^']+)'", m.group(1)) if m else []
    capas_ab = {o["id"]: o["c"][0] for o in itens}
    preload = "\n".join(
        f'<link rel="preload" as="image" href="img/{capas_ab[i]}-h.webp" fetchpriority="high">'
        for i in escolhidas if i in capas_ab)
    html = html.replace("<!--PRELOAD-->", preload)
    with open(os.path.join(SAIDA, "index.html"), "w", encoding="utf-8") as f:
        f.write(html)
    # fontes hospedadas no próprio site (com as licenças)
    fontes = os.path.join(SAIDA, "fonts")
    if os.path.isdir(fontes):
        shutil.rmtree(fontes)
    shutil.copytree(os.path.join(AQUI, "fontes"), fontes)
    # GitHub Pages: servir os arquivos como estão
    open(os.path.join(SAIDA, ".nojekyll"), "w").close()
    for nome in os.listdir(SAIDA):
        if nome not in ("index.html", ".nojekyll", "img", "fonts"):
            caminho = os.path.join(SAIDA, nome)
            shutil.rmtree(caminho) if os.path.isdir(caminho) else os.remove(caminho)

    total = sum(os.path.getsize(os.path.join(img_dir, n)) for n in os.listdir(img_dir))
    print(f"   Site pronto em _site/ ({len(os.listdir(img_dir))} imagens, {total/1e6:.0f} MB).")
    return itens


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--acervo", default=os.path.expanduser("~/Documents/Acervo_Fagulha"))
    args = ap.parse_args()
    try:
        montar(os.path.abspath(args.acervo))
    except Problema as e:
        print("\n✗ " + str(e))
        sys.exit(1)


if __name__ == "__main__":
    main()
