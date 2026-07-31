# -*- coding: utf-8 -*-
"""Autenticação de verdade, sobre o Supabase Auth.

Por que o Supabase Auth e não uma tabela de usuários nossa: ele já cuida de hash
de senha (bcrypt), expiração de token e revogação. Escrever isso à mão é onde se
erra feio e em silêncio.

Cadastro é INTERNO: não existe auto-registro. Só quem já tem conta cria outra
conta — a checagem está em `exigir_usuario` no endpoint de criação.

O perfil (nome e cargo) fica em `public.profiles`, com o mesmo id do usuário do
Auth. O Auth guarda a credencial; o profile, quem é a pessoa aqui dentro.
"""
import os

from fastapi import HTTPException, Request
from supabase import create_client

# cliente com a chave pública: é ele que valida token e faz login.
# A service_role NUNCA vai para o front — ela ignora RLS e é chave de servidor.
_pub = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_ANON_KEY"])

CARGOS = ("admin", "curador", "leitor")


def token_do(pedido: Request) -> str | None:
    cab = pedido.headers.get("Authorization") or ""
    if cab.lower().startswith("bearer "):
        t = cab[7:].strip()
        return t or None
    return None


def usuario_do(pedido: Request, sb) -> dict | None:
    """Quem está pedindo, ou None se não houver token válido.

    Valida contra o Supabase a cada chamada — uma ida de rede por requisição.
    Com volume maior, vale verificar a assinatura do JWT localmente.
    """
    tok = token_do(pedido)
    if not tok:
        return None
    try:
        resp = _pub.auth.get_user(tok)
        u = getattr(resp, "user", None)
        if not u:
            return None
    except Exception:
        return None
    perfil = {}
    try:
        r = sb.table("profiles").select("*").eq("id", u.id).execute().data
        perfil = r[0] if r else {}
    except Exception:
        pass
    return {"id": u.id, "email": u.email,
            "nome": perfil.get("name") or (u.email or "").split("@")[0],
            "cargo": perfil.get("role") or "leitor"}


def exigir_usuario(pedido: Request, sb) -> dict:
    """Barra quem não está autenticado."""
    u = usuario_do(pedido, sb)
    if not u:
        raise HTTPException(401, "faça login para continuar")
    return u


def exigir_cargo(pedido: Request, sb, *cargos: str) -> dict:
    u = exigir_usuario(pedido, sb)
    if cargos and u["cargo"] not in cargos:
        raise HTTPException(403, f"esta ação exige cargo: {', '.join(cargos)}")
    return u


def entrar(email: str, senha: str) -> dict:
    """Login. Devolve o token de acesso e o usuário."""
    try:
        r = _pub.auth.sign_in_with_password({"email": email, "password": senha})
    except Exception:
        raise HTTPException(401, "e-mail ou senha incorretos")
    if not getattr(r, "session", None):
        raise HTTPException(401, "e-mail ou senha incorretos")
    return {"token": r.session.access_token,
            "expira_em": r.session.expires_in,
            "usuario": {"id": r.user.id, "email": r.user.email}}
