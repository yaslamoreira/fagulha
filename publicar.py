#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Fagulha — publicação.

1. Gera o site (planilha + imagens de 4_finais).
2. Confere se só há arquivos públicos esperados e mostra um resumo.
3. Pede confirmação.
4. Envia o código (com histórico) e o site pronto (substituído inteiro) ao GitHub.

Uso: python3 publicar.py --acervo "/caminho/para/Acervo_Fagulha"
"""
import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime

import gerar

AQUI = os.path.dirname(os.path.abspath(__file__))
SAIDA = gerar.SAIDA
CACHE = gerar.CACHE
REPO = "https://github.com/yaslamoreira/fagulha.git"
GIT_PUB = os.path.join(CACHE, "publicacao.git")   # controle do site publicado (fora do Drive)
ULTIMA = os.path.join(CACHE, "ultima_publicacao.json")

RAIZ_PERMITIDA = {"index.html", ".nojekyll", "favicon.svg", "favicon-32.png", "apple-touch-icon.png", "img", "fonts"}
FONTES_PERMITIDAS = {"xanh-mono-400.woff2", "schibsted-grotesk.woff2", "OFL-Xanh-Mono.txt", "OFL-Schibsted-Grotesk.txt"}


def git(*args, cwd=AQUI, gitdir=None, worktree=None, capturar=True, checar=True):
    cmd = ["git"]
    if gitdir:
        cmd += ["--git-dir", gitdir]
    if worktree:
        cmd += ["--work-tree", worktree]
    cmd += list(args)
    r = subprocess.run(cmd, cwd=cwd, capture_output=capturar, text=True)
    if checar and r.returncode != 0:
        raise gerar.Problema("O git deu um erro:\n   " + (r.stderr or r.stdout).strip()[-600:])
    return (r.stdout or "").strip()


def conferir(itens):
    """Só passam arquivos esperados; qualquer coisa estranha interrompe a publicação."""
    estranhos = [n for n in os.listdir(SAIDA) if n not in RAIZ_PERMITIDA]
    estranhos += ["fonts/" + n for n in os.listdir(os.path.join(SAIDA, "fonts")) if n not in FONTES_PERMITIDAS]
    ids = {o["id"] for o in itens}
    padrao = re.compile(r"^(.+?)-(?:[pf]\d+(?:e\d+)?|capa)(?:-(?:t|t2|h))?\.webp$")
    for n in os.listdir(os.path.join(SAIDA, "img")):
        m = padrao.match(n)
        if not m or m.group(1) not in ids:
            estranhos.append("img/" + n)
    # toda imagem precisa ter vindo de 4_finais
    registro = json.load(open(os.path.join(CACHE, "imagens.json"), encoding="utf-8"))
    for saida, (origem, *_resto) in registro.items():
        if not origem.startswith("4_finais" + os.sep):
            estranhos.append(f"img/{saida} (veio de {origem})")
    if estranhos:
        raise gerar.Problema("Encontrei arquivos que não deveriam ir para o site. Nada foi publicado:\n     "
                             + "\n     ".join(estranhos))


def resumo(itens):
    total = 0
    for raiz, _d, arqs in os.walk(SAIDA):
        total += sum(os.path.getsize(os.path.join(raiz, a)) for a in arqs)
    n_img = len(os.listdir(os.path.join(SAIDA, "img")))
    print(f"\n   {len(itens)} impressos, {n_img} imagens, {total/1e6:.0f} MB no total.")
    try:
        antes = set(json.load(open(ULTIMA, encoding="utf-8")))
    except Exception:
        antes = None
    agora = {o["code"] for o in itens}
    if antes is None:
        print("   Primeira publicação.")
    else:
        novos, saem = sorted(agora - antes), sorted(antes - agora)
        print("   Itens novos: " + (", ".join(novos) if novos else "nenhum"))
        print("   Itens que saem do site: " + (", ".join(saem) if saem else "nenhum"))
    return agora


def enviar_codigo(quando):
    git("add", "-A")
    if git("status", "--porcelain"):
        git("commit", "-q", "-m", f"Publicação de {quando}")
    if not git("remote", checar=False):
        git("remote", "add", "origin", REPO)
    git("push", "-q", "origin", "main")


def enviar_site(quando):
    if not os.path.isdir(GIT_PUB):
        git("init", "-q", "--bare", GIT_PUB, cwd=CACHE)
        git("remote", "add", "origin", REPO, gitdir=GIT_PUB)
        git("config", "core.bare", "false", gitdir=GIT_PUB)
        nome = git("config", "user.name"); email = git("config", "user.email")
        git("config", "user.name", nome, gitdir=GIT_PUB)
        git("config", "user.email", email, gitdir=GIT_PUB)
    # traz a versão publicada anterior (se houver) só para não reenviar imagens que não mudaram
    git("fetch", "-q", "--depth", "1", "origin", "gh-pages", gitdir=GIT_PUB, checar=False)
    indice = os.path.join(CACHE, "publicacao.index")
    if os.path.exists(indice):
        os.remove(indice)
    env = dict(os.environ, GIT_INDEX_FILE=indice)
    for passo in (["add", "-A", "."], ["write-tree"]):
        r = subprocess.run(["git", "--git-dir", GIT_PUB, "--work-tree", SAIDA] + passo,
                           cwd=SAIDA, capture_output=True, text=True, env=env)
        if r.returncode != 0:
            raise gerar.Problema("O git deu um erro:\n   " + r.stderr.strip()[-600:])
    arvore = r.stdout.strip()
    # versão nova sem histórico: o site é substituído inteiro
    commit = git("commit-tree", arvore, "-m", f"Site publicado em {quando}", gitdir=GIT_PUB)
    git("push", "-q", "-f", "origin", f"{commit}:refs/heads/gh-pages", gitdir=GIT_PUB)
    git("gc", "-q", "--prune=now", gitdir=GIT_PUB, checar=False)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--acervo", default=os.path.expanduser("~/Documents/Acervo_Fagulha"))
    ap.add_argument("--sim", action="store_true", help="publica sem perguntar")
    args = ap.parse_args()
    print("=== Publicar Fagulha ===\n")
    try:
        itens = gerar.montar(os.path.abspath(args.acervo))
        print("5. Conferindo o que vai para o site…")
        conferir(itens)
        print("   Tudo certo: só há imagens de 4_finais e os arquivos do site.")
        codigos = resumo(itens)
        if not args.sim:
            r = input("\nPublicar agora? Digite s e aperte Enter (ou só Enter para cancelar): ").strip().lower()
            if r not in ("s", "sim"):
                print("\nCancelado. Nada foi enviado.")
                return
        quando = datetime.now().strftime("%d/%m/%Y %H:%M")
        print("\n6. Enviando o código…")
        enviar_codigo(quando)
        print("7. Enviando o site (pode levar alguns minutos na primeira vez)…")
        enviar_site(quando)
        json.dump(sorted(codigos), open(ULTIMA, "w", encoding="utf-8"), ensure_ascii=False)
        print(f"\n✓ Publicado! Em 1 ou 2 minutos o site estará atualizado em:\n  {gerar.SITE_URL}")
    except gerar.Problema as e:
        print("\n✗ " + str(e))
        sys.exit(1)
    except KeyboardInterrupt:
        print("\nCancelado.")
        sys.exit(1)


if __name__ == "__main__":
    main()
