import asyncio
import os
import json
import threading
import secrets
import unicodedata
import urllib.parse
import urllib.request
import urllib.error
from copy import deepcopy
from pathlib import Path
from functools import wraps
from datetime import datetime, timezone, timedelta
from urllib.parse import urlencode, urlsplit, urlunsplit, parse_qsl

import discord
from discord.ext import commands
from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify
from werkzeug.security import generate_password_hash, check_password_hash

# =========================================================
# CONFIGURAÇÕES DO SITE
# =========================================================

PANEL_PASSWORD = os.getenv("PANEL_PASSWORD")
if not PANEL_PASSWORD:
    raise RuntimeError(
        "A variável PANEL_PASSWORD não foi configurada. "
        "Crie essa variável no Railway antes de iniciar."
    )

SITE_PUBLIC_URL = os.getenv("SITE_PUBLIC_URL", "https://resenha-maxima.up.railway.app").rstrip("/")
DATA_DIR = Path(os.getenv("DATA_DIR", "."))
DATA_DIR.mkdir(parents=True, exist_ok=True)

# Novo arquivo: vários menus, um por canal.
MENUS_FILE = DATA_DIR / "menus_config.json"

# Arquivo antigo. Se existir, o painel consegue aproveitar a configuração
# como modelo inicial sem apagar os dados antigos.
CONFIG_ANTIGO_FILE = DATA_DIR / "menu_config.json"

# Canal usado apenas para prévias/testes enviados pelo painel.
CANAL_TESTE_ID = 1537936115233722388

# Permissões do /menu deste serviço.
DONO_ID = 1455937306400653344
CARGO_DESENVOLVIMENTO_ID = 1533625836874498181
CANAL_APROVACAO_ID = 1536073451633254420
MAX_BOTOES = 25

# =========================================================
# SERVIDOR DO DEPARTAMENTO DE EVENTOS
# =========================================================

SERVIDORES_CONFIG_FILE = DATA_DIR / "servidores_config.json"
CONTA_TESTE_EVENTOS_ID = 1532838576256057557
CARGO_TESTE_EVENTOS_ID = 1536081355711062166
GOOGLE_FORMS_URL = os.getenv("GOOGLE_FORMS_URL", "https://forms.gle/h4kt2Cp7fduGG4Pc8")
CARGO_ROBLOX_ID = 1540858217301549176
CARGO_MINECRAFT_ID = 1534006899371147304
CANAL_CANDIDATURA_PRINCIPAL_ID = int(os.getenv("CANAL_CANDIDATURA_PRINCIPAL_ID", "1541035337709649990") or "1541035337709649990")
FORM_WEBHOOK_SECRET = (os.getenv("FORM_WEBHOOK_SECRET") or os.getenv("EVENTOS_SECRET") or "").strip()
EVENTOS_PREFILL_SCRIPT_URL = os.getenv("EVENTOS_PREFILL_SCRIPT_URL", "https://script.google.com/macros/s/AKfycbxkCj_GHByDB1fGiWIB5afuKSseh3akjYb2cwrFNubeaBpeL5mf2LnltEpx8zroIvn7MQ/exec").strip()
EVENTOS_PREFILL_CONFIG_FILE = DATA_DIR / "eventos_prefill.json"
EVENTOS_GUILD_ID = int(os.getenv("EVENTOS_GUILD_ID", "1541541588122079283") or "1541541588122079283")
CANDIDATURA_CODES_FILE = DATA_DIR / "candidatura_codes.json"
_CANDIDATURA_CODES_LOCK = threading.Lock()

CARGOS_EVENTOS = [
    ("Chef de Departamento", "chef"),
    ("Diretor de Eventos", "diretor"),
    ("Gerente de Eventos", "gerente"),
    ("Coordenador de Eventos", "coordenador"),
    ("Supervisor de Eventos", "supervisor"),
    ("Aprendiz de Eventos", "aprendiz"),
    ("Intruso", "intruso"),
]

CANAIS_EVENTOS = [
    ("📁 GERAL", [
        ("💬・chat", "chat"),
        ("📢・anuncios", "anuncios"),
        ("💡・sugestoes", "sugestoes"),
        ("📸・midias", "midias"),
        ("🤖・comandos", "comandos"),
        ("📜・𝑪𝒂𝒏𝒅𝒊𝒅𝒂𝒕𝒖𝒓𝒂", "candidatura"),
        ("📜・regras", "regras"),
        ("⚠️・advertencias", "advertencias"),
        ("📊・hierarquia", "hierarquia"),
    ]),
    ("🔒 INTERNO", [
        ("👔・gerentes", "gerentes"),
        ("👑・diretoria", "diretoria"),
    ]),
    ("🎙️ VOZ", [
        ("🎙️・call-eventos", "call"),
    ]),
]

def carregar_servidores_config():
    if not SERVIDORES_CONFIG_FILE.exists():
        return {"versao": 1, "principal_id": str(GUILD_ID or ""), "eventos_guild_id": None}
    try:
        dados = json.loads(SERVIDORES_CONFIG_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        dados = {}
    if not isinstance(dados, dict):
        dados = {}
    dados.setdefault("versao", 1)
    dados.setdefault("principal_id", str(GUILD_ID or ""))
    dados.setdefault("eventos_guild_id", None)
    return dados

def salvar_servidores_config(dados):
    temporario = SERVIDORES_CONFIG_FILE.with_suffix(".tmp")
    temporario.write_text(
        json.dumps(dados, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )
    temporario.replace(SERVIDORES_CONFIG_FILE)

def servidores_disponiveis_eventos():
    principal = str(GUILD_ID or "")
    configuracao = carregar_servidores_config()
    configurado = str(configuracao.get("eventos_guild_id") or "")
    resultado = []
    for guild in sorted(bot.guilds, key=lambda g: g.name.casefold()):
        if str(guild.id) == principal:
            continue
        resultado.append({
            "id": str(guild.id),
            "nome": guild.name,
            "membros": getattr(guild, "member_count", None) or 0,
            "configurado": str(guild.id) == configurado,
        })
    return resultado

async def _buscar_ou_criar_role(guild, nome, *, permissions=None, hoist=False):
    existente = discord.utils.get(guild.roles, name=nome)
    if existente:
        if permissions is not None:
            try:
                await existente.edit(permissions=permissions, hoist=hoist, reason="Configuração do Departamento de Eventos")
            except discord.HTTPException:
                pass
        return existente
    return await guild.create_role(
        name=nome,
        permissions=permissions or discord.Permissions.none(),
        hoist=hoist,
        reason="Configuração automática do Departamento de Eventos",
    )

def _permissoes_eventos(chave):
    p = discord.Permissions.none()
    if chave == "chef":
        p.administrator = True
        return p
    if chave == "diretor":
        p.manage_guild = True
        p.manage_channels = True
        p.manage_roles = True
        p.manage_messages = True
        p.moderate_members = True
        p.view_audit_log = True
        p.move_members = True
        p.mute_members = True
        p.deafen_members = True
        p.kick_members = True
        return p
    if chave == "gerente":
        p.manage_messages = True
        p.moderate_members = True
        p.view_audit_log = True
        p.move_members = True
        p.mute_members = True
        p.deafen_members = True
        return p
    if chave == "coordenador":
        p.manage_messages = True
        p.moderate_members = True
        p.move_members = True
        p.mute_members = True
        p.deafen_members = True
        return p
    if chave == "supervisor":
        p.manage_messages = True
        p.moderate_members = True
        p.move_members = True
        return p
    if chave == "aprendiz":
        p.view_channel = True
        p.send_messages = True
        p.read_message_history = True
        p.connect = True
        p.speak = True
        return p
    return discord.Permissions.none()

def _overwrite(allow=(), deny=()):
    overwrite = discord.PermissionOverwrite()
    for nome in allow:
        setattr(overwrite, nome, True)
    for nome in deny:
        setattr(overwrite, nome, False)
    return overwrite

async def _obter_categoria(guild, nome):
    categoria = discord.utils.get(guild.categories, name=nome)
    if categoria:
        return categoria
    return await guild.create_category(
        nome,
        reason="Configuração automática do Departamento de Eventos"
    )

async def _obter_canal_texto(categoria, nome):
    canal = discord.utils.get(categoria.text_channels, name=nome)
    if canal:
        return canal
    return await categoria.create_text_channel(
        nome,
        reason="Configuração automática do Departamento de Eventos"
    )

async def _obter_canal_voz(categoria, nome):
    canal = discord.utils.get(categoria.voice_channels, name=nome)
    if canal:
        return canal
    return await categoria.create_voice_channel(
        nome,
        reason="Configuração automática do Departamento de Eventos"
    )

def _carregar_codigos_candidatura():
    if not CANDIDATURA_CODES_FILE.exists():
        return {}
    try:
        dados = json.loads(CANDIDATURA_CODES_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return dados if isinstance(dados, dict) else {}


def _salvar_codigos_candidatura(dados):
    tmp = CANDIDATURA_CODES_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(dados, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(CANDIDATURA_CODES_FILE)


def _gerar_codigo_candidatura(user_id):
    # Compatível com o Apps Script antigo: RM-EVT + 6 caracteres hexadecimais.
    # O vínculo com o Discord fica salvo no JSON de códigos.
    with _CANDIDATURA_CODES_LOCK:
        dados = _carregar_codigos_candidatura()
        for _ in range(30):
            codigo = f"RM-EVT-{secrets.token_hex(3).upper()}"
            if codigo not in dados:
                dados[codigo] = {"discord_id": str(int(user_id))}
                _salvar_codigos_candidatura(dados)
                return codigo
    raise RuntimeError("Não foi possível gerar um código de candidatura único.")


def _discord_id_por_codigo(codigo):
    if not codigo:
        return None
    codigo = str(codigo).strip().upper()
    with _CANDIDATURA_CODES_LOCK:
        item = _carregar_codigos_candidatura().get(codigo)
    if isinstance(item, dict) and str(item.get("discord_id", "")).isdigit():
        return int(item["discord_id"])
    return None


def _carregar_prefill_script_url():
    if EVENTOS_PREFILL_SCRIPT_URL:
        return EVENTOS_PREFILL_SCRIPT_URL
    if EVENTOS_PREFILL_CONFIG_FILE.exists():
        try:
            dados = json.loads(EVENTOS_PREFILL_CONFIG_FILE.read_text(encoding="utf-8"))
            url = str(dados.get("prefill_script_url") or "").strip()
            if url:
                return url
        except (OSError, json.JSONDecodeError, AttributeError):
            pass
    return ""


def _salvar_prefill_script_url(url):
    url = str(url or "").strip()
    tmp = EVENTOS_PREFILL_CONFIG_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps({"prefill_script_url": url}, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(EVENTOS_PREFILL_CONFIG_FILE)


def _url_formulario_com_codigo(codigo):
    # O Apps Script existente encontra o campo "Código da candidatura" no Forms
    # e redireciona para uma URL pré-preenchida. Não precisamos de entry.xxxxx.
    script_url = _carregar_prefill_script_url()
    if script_url:
        separador = "&" if "?" in script_url else "?"
        return f"{script_url}{separador}{urlencode({'codigo': codigo})}"
    return GOOGLE_FORMS_URL


class CandidaturaEventosView(discord.ui.View):
    """Menu simples e persistente usado nos dois servidores."""

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Informações",
        style=discord.ButtonStyle.secondary,
        emoji="ℹ️",
        custom_id="eventos_candidatura_informacoes",
    )
    async def informacoes(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = discord.Embed(
            title="ℹ️ Como funciona a candidatura",
            description=(
                "A candidatura é o processo para entrar no **Departamento de Eventos** como "
                "**Aprendiz de Eventos**.\n\n"
                "**1.** Clique em **Fazer candidatura** e responda o formulário.\n"
                "**2.** Depois do envio, o bot cria um **ticket temporário no servidor principal**.\n"
                "**3.** Somente você e a **Diretoria de Eventos** terão acesso ao ticket.\n"
                "**4.** Suas respostas serão exibidas no ticket para avaliação.\n"
                "**5.** Se a prova for aprovada, o bot cria uma **call privada de entrevista** com as mesmas permissões.\n"
                "**6.** Se você também for aprovado na call, recebe o cargo do **Departamento de Eventos** "
                "e entra oficialmente como **Aprendiz de Eventos**.\n\n"
                "Se você estiver fazendo a prova pelo servidor **Departamento de Eventos**, "
                "a avaliação continua no **servidor principal**."
            ),
            color=discord.Color.blurple(),
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @discord.ui.button(
        label="Fazer candidatura",
        style=discord.ButtonStyle.success,
        emoji="📝",
        custom_id="eventos_candidatura_fazer",
    )
    async def fazer_candidatura(self, interaction: discord.Interaction, button: discord.ui.Button):
        origem_eventos = False
        cfg = carregar_servidores_config()
        if interaction.guild:
            origem_eventos = str(interaction.guild.id) == str(cfg.get("eventos_guild_id") or "")

        codigo = _gerar_codigo_candidatura(interaction.user.id)
        link = _url_formulario_com_codigo(codigo)
        complemento = (
            "\n\n📌 Quando terminar, a análise e o ticket serão feitos no **servidor principal da RESENHA MÁXIMA**."
            if origem_eventos else ""
        )
        prefill_ativo = bool(_carregar_prefill_script_url())
        aviso_codigo = (
            "\n\n✅ O campo **Código da candidatura** será preenchido automaticamente."
            if prefill_ativo
            else f"\n\n🔑 **Código da candidatura:** `{codigo}`\n"
                 "⚠️ O Web App de pré-preenchimento ainda não foi registrado no site."
        )
        await interaction.response.send_message(
            f"📝 **Formulário de candidatura para Aprendiz de Eventos**\n\n{link}{aviso_codigo}{complemento}",
            ephemeral=True,
        )


def _embed_menu_candidatura():
    embed = discord.Embed(
        title="📝 Candidatura — Departamento de Eventos",
        description=(
            "Quer se tornar **Aprendiz de Eventos**?\n\n"
            "Use **Informações** para entender todas as etapas ou "
            "**Fazer candidatura** para abrir o formulário."
        ),
        color=discord.Color.blurple(),
    )
    embed.set_footer(text="RESENHA MÁXIMA • Departamento de Eventos")
    return embed


def _normalizar_nome_discord(valor):
    # NFKD transforma letras matemáticas/estilizadas em letras comuns.
    texto = unicodedata.normalize("NFKD", str(valor or ""))
    texto = "".join(ch for ch in texto if not unicodedata.combining(ch))
    return "".join(ch for ch in texto.casefold() if ch.isalnum())


def _eh_canal_candidatura_eventos(canal):
    nome = _normalizar_nome_discord(getattr(canal, "name", ""))
    return "candidatura" in nome


def _localizar_canal_candidatura_eventos(guild):
    # 1) Usa o ID salvo quando ele ainda existe.
    try:
        cfg = carregar_servidores_config()
        canal_id = int(cfg.get("candidatura_channel_id") or 0)
    except (TypeError, ValueError):
        canal_id = 0
    if canal_id:
        canal = guild.get_channel(canal_id)
        if isinstance(canal, discord.TextChannel):
            return canal

    # 2) Aceita nomes normais ou com fontes Unicode, como
    #    📜・Candidatura e 📜・𝑪𝒂𝒏𝒅𝒊𝒅𝒂𝒕𝒖𝒓𝒂.
    candidatos = [c for c in guild.text_channels if _eh_canal_candidatura_eventos(c)]
    if not candidatos:
        return None

    # Prefere o canal fora de categorias/mais antigo quando houver duplicatas,
    # pois é o formato do canal já existente mostrado no servidor de Eventos.
    candidatos.sort(key=lambda c: (c.category is not None, c.position, c.id))
    return candidatos[0]


def _mensagem_parece_menu_candidatura(msg):
    if msg.author.id != bot.user.id:
        return False

    ids = set()
    for row in msg.components or []:
        for comp in getattr(row, "children", []):
            cid = getattr(comp, "custom_id", None)
            if cid:
                ids.add(cid)
    if {"eventos_candidatura_informacoes", "eventos_candidatura_fazer"} & ids:
        return True

    texto = " ".join(
        f"{getattr(embed, 'title', '')} {getattr(embed, 'description', '')}"
        for embed in msg.embeds
    ).casefold()
    if "candidatura" in texto:
        return True
    if "verificar roblox" in texto or "verificar minecraft" in texto:
        return True
    return False


async def _publicar_ou_atualizar_menu(canal):
    # Procura tanto o menu novo quanto o menu antigo Roblox/Minecraft e mantém
    # somente um painel de candidatura no canal.
    encontradas = []
    try:
        async for msg in canal.history(limit=100):
            if _mensagem_parece_menu_candidatura(msg):
                encontradas.append(msg)
    except (discord.Forbidden, discord.HTTPException):
        encontradas = []

    view = CandidaturaEventosView()
    embed = _embed_menu_candidatura()

    if encontradas:
        # history() retorna da mais recente para a mais antiga. Editamos a mais
        # recente e removemos menus antigos duplicados do próprio bot.
        principal = encontradas[0]
        await principal.edit(content=None, embed=embed, view=view)
        for antiga in encontradas[1:]:
            try:
                await antiga.delete()
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                pass
        return principal

    return await canal.send(embed=embed, view=view)


async def publicar_menu_candidatura_principal():
    canal = bot.get_channel(CANAL_CANDIDATURA_PRINCIPAL_ID)
    if canal is None:
        try:
            canal = await bot.fetch_channel(CANAL_CANDIDATURA_PRINCIPAL_ID)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            return False
    if not isinstance(canal, discord.TextChannel):
        return False
    await _publicar_ou_atualizar_menu(canal)
    return True


async def atualizar_menu_candidatura_eventos_existente(guild_id):
    """Atualiza o painel no canal existente sem criar canal/categoria/cargo."""
    guild = bot.get_guild(int(guild_id))
    if guild is None:
        return False
    canal = _localizar_canal_candidatura_eventos(guild)
    if canal is None:
        return False
    await _publicar_ou_atualizar_menu(canal)

    # Salva o ID encontrado para as próximas inicializações.
    try:
        dados = carregar_servidores_config()
        dados["eventos_guild_id"] = str(guild.id)
        dados["eventos_guild_nome"] = guild.name
        dados["candidatura_channel_id"] = str(canal.id)
        salvar_servidores_config(dados)
    except OSError:
        pass
    return True


async def configurar_servidor_eventos(guild_id):
    """
    Configura SOMENTE o canal de candidatura no servidor de Eventos.
    Não cria categorias, outros canais, calls ou a hierarquia inteira.
    """
    guild = bot.get_guild(int(guild_id))
    if guild is None:
        raise RuntimeError("O bot não está mais conectado a esse servidor.")

    principal = str(GUILD_ID or "")
    if str(guild.id) == principal:
        raise RuntimeError("O servidor principal não pode ser usado como Departamento de Eventos.")

    intruso = discord.utils.get(guild.roles, name="Intruso")
    if intruso is None:
        intruso = await guild.create_role(
            name="Intruso",
            permissions=discord.Permissions.none(),
            reason="Acesso inicial à candidatura do Departamento de Eventos",
        )

    candidatura = _localizar_canal_candidatura_eventos(guild)
    if candidatura is None:
        # Só cria o canal quando a configuração for solicitada explicitamente e
        # nenhum canal de candidatura (normal ou estilizado) já existir.
        candidatura = await guild.create_text_channel(
            "📜・Candidatura",
            reason="Canal de candidatura do Departamento de Eventos",
        )

    overwrites = {
        guild.default_role: _overwrite(deny=("view_channel",)),
        intruso: _overwrite(
            allow=("view_channel", "read_message_history"),
            deny=("send_messages", "create_public_threads", "create_private_threads"),
        ),
    }
    if guild.me:
        overwrites[guild.me] = _overwrite(
            allow=("view_channel", "read_message_history", "send_messages", "manage_messages")
        )
    try:
        await candidatura.edit(
            overwrites=overwrites,
            reason="Permissões do canal de candidatura do Departamento de Eventos",
        )
    except discord.HTTPException:
        pass

    await _publicar_ou_atualizar_menu(candidatura)

    dados = carregar_servidores_config()
    dados["eventos_guild_id"] = str(guild.id)
    dados["eventos_guild_nome"] = guild.name
    dados["candidatura_channel_id"] = str(candidatura.id)
    dados["intruso_role_id"] = str(intruso.id)
    dados["configurado_em"] = __import__("datetime").datetime.now().isoformat()
    salvar_servidores_config(dados)

    return {
        "guild_id": str(guild.id),
        "guild_name": guild.name,
        "roles": {"intruso": intruso.name},
        "channels": [candidatura.name],
    }


def _normalizar_respostas_formulario(payload):
    respostas = payload.get("respostas") or payload.get("answers") or payload.get("respostas_formulario") or {}
    if isinstance(respostas, list):
        convertido = {}
        for item in respostas:
            if isinstance(item, dict):
                pergunta = str(item.get("pergunta") or item.get("question") or item.get("titulo") or "Pergunta")
                convertido[pergunta] = item.get("resposta") or item.get("answer") or item.get("valor") or ""
        respostas = convertido
    if not isinstance(respostas, dict):
        respostas = {"Resposta": str(respostas)}
    return {str(k): str(v) for k, v in respostas.items()}


def _discord_id_do_payload(payload):
    codigo_direto = payload.get("codigo") or payload.get("codigo_candidatura")
    if codigo_direto:
        encontrado = _discord_id_por_codigo(codigo_direto)
        if encontrado:
            return encontrado

    respostas = _normalizar_respostas_formulario(payload)
    for pergunta, valor in respostas.items():
        titulo = pergunta.casefold().strip()
        if "código da candidatura" in titulo or "codigo da candidatura" in titulo:
            encontrado = _discord_id_por_codigo(valor)
            if encontrado:
                return encontrado

    chaves = ("discord_id", "discordId", "id_discord", "usuario_discord_id", "user_id")
    for chave in chaves:
        valor = payload.get(chave)
        if valor:
            try:
                return int(str(valor).strip().replace("<@", "").replace("!", "").replace(">", ""))
            except ValueError:
                pass
    for pergunta, valor in respostas.items():
        if "discord" in pergunta.casefold() and "id" in pergunta.casefold():
            try:
                return int(str(valor).strip().replace("<@", "").replace("!", "").replace(">", ""))
            except ValueError:
                pass
    return None


def _cargo_diretor_eventos(guild):
    nomes = {"diretor de eventos", "diretor eventos", "diretoria de eventos"}
    for role in guild.roles:
        if role.name.casefold().strip() in nomes:
            return role
    return None


def _cargo_departamento_eventos(guild):
    nomes = {"departamento de eventos", "departamento eventos", "equipe de eventos", "eventos"}
    if CARGO_EVENTOS_ID:
        role = guild.get_role(CARGO_EVENTOS_ID)
        if role:
            return role
    for role in guild.roles:
        if role.name.casefold().strip() in nomes:
            return role
    return None


async def _encerrar_canais_candidatura(channel, voice_channel=None, *, delay=8):
    await asyncio.sleep(delay)
    for alvo in (voice_channel, channel):
        if alvo is not None:
            try:
                await alvo.delete(reason="Processo de candidatura finalizado")
            except (discord.Forbidden, discord.HTTPException, discord.NotFound):
                pass


class EtapaFinalCandidaturaView(discord.ui.View):
    def __init__(self, candidato_id, voice_channel_id=None):
        super().__init__(timeout=86400)
        self.candidato_id = int(candidato_id)
        self.voice_channel_id = int(voice_channel_id) if voice_channel_id else None

    async def interaction_check(self, interaction: discord.Interaction):
        diretor = _cargo_diretor_eventos(interaction.guild) if interaction.guild else None
        permitido = interaction.user.id == DONO_ID or (diretor and diretor in interaction.user.roles)
        if not permitido:
            await interaction.response.send_message("❌ Somente a Diretoria de Eventos pode concluir esta candidatura.", ephemeral=True)
        return bool(permitido)

    @discord.ui.button(label="Aprovar após call", style=discord.ButtonStyle.success, emoji="✅")
    async def aprovar(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild = interaction.guild
        candidato = guild.get_member(self.candidato_id)
        if candidato is None:
            try:
                candidato = await guild.fetch_member(self.candidato_id)
            except Exception:
                candidato = None
        if candidato is None:
            await interaction.response.send_message("❌ O candidato não está mais no servidor principal.", ephemeral=True)
            return

        cargo = _cargo_departamento_eventos(guild)
        if cargo is None:
            await interaction.response.send_message(
                "❌ Não encontrei o cargo **Departamento de Eventos** no servidor principal. Configure `CARGO_EVENTOS_ID` ou use esse nome no cargo.",
                ephemeral=True,
            )
            return
        await candidato.add_roles(cargo, reason=f"Candidatura aprovada por {interaction.user}")

        # No servidor de Eventos o aprovado entra oficialmente como Aprendiz.
        cfg = carregar_servidores_config()
        guild_eventos = bot.get_guild(int(cfg.get("eventos_guild_id") or 0)) if cfg.get("eventos_guild_id") else None
        if guild_eventos:
            membro_eventos = guild_eventos.get_member(candidato.id)
            if membro_eventos:
                aprendiz = discord.utils.get(guild_eventos.roles, name="Aprendiz de Eventos")
                intruso = discord.utils.get(guild_eventos.roles, name="Intruso")
                if aprendiz:
                    await membro_eventos.add_roles(aprendiz, reason="Candidatura aprovada")
                if intruso and intruso in membro_eventos.roles:
                    await membro_eventos.remove_roles(intruso, reason="Candidatura aprovada")

        await interaction.response.send_message(
            f"✅ {candidato.mention} foi **aprovado na call** e recebeu o cargo {cargo.mention}.\nEste ticket será encerrado.",
        )
        voice = guild.get_channel(self.voice_channel_id) if self.voice_channel_id else None
        asyncio.create_task(_encerrar_canais_candidatura(interaction.channel, voice))
        self.stop()

    @discord.ui.button(label="Reprovar após call", style=discord.ButtonStyle.danger, emoji="❌")
    async def reprovar(self, interaction: discord.Interaction, button: discord.ui.Button):
        candidato = interaction.guild.get_member(self.candidato_id)
        mencao = candidato.mention if candidato else f"<@{self.candidato_id}>"
        await interaction.response.send_message(f"❌ {mencao} foi **reprovado após a call**. Este ticket será encerrado.")
        voice = interaction.guild.get_channel(self.voice_channel_id) if self.voice_channel_id else None
        asyncio.create_task(_encerrar_canais_candidatura(interaction.channel, voice))
        self.stop()


class AvaliacaoCandidaturaView(discord.ui.View):
    def __init__(self, candidato_id):
        super().__init__(timeout=86400)
        self.candidato_id = int(candidato_id)

    async def interaction_check(self, interaction: discord.Interaction):
        diretor = _cargo_diretor_eventos(interaction.guild) if interaction.guild else None
        permitido = interaction.user.id == DONO_ID or (diretor and diretor in interaction.user.roles)
        if not permitido:
            await interaction.response.send_message("❌ Somente a Diretoria de Eventos pode avaliar esta candidatura.", ephemeral=True)
        return bool(permitido)

    @discord.ui.button(label="Aprovar prova / criar call", style=discord.ButtonStyle.success, emoji="🎙️")
    async def aprovar_prova(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild = interaction.guild
        candidato = guild.get_member(self.candidato_id)
        if candidato is None:
            try:
                candidato = await guild.fetch_member(self.candidato_id)
            except Exception:
                candidato = None
        if candidato is None:
            await interaction.response.send_message("❌ O candidato não está no servidor principal.", ephemeral=True)
            return

        diretor = _cargo_diretor_eventos(guild)
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False, connect=False),
            candidato: discord.PermissionOverwrite(view_channel=True, connect=True, speak=True),
        }
        if diretor:
            overwrites[diretor] = discord.PermissionOverwrite(view_channel=True, connect=True, speak=True, move_members=True)
        if guild.me:
            overwrites[guild.me] = discord.PermissionOverwrite(view_channel=True, connect=True, speak=True, manage_channels=True)

        nome = f"entrevista-{candidato.display_name}"[:95].lower().replace(" ", "-")
        voice = await guild.create_voice_channel(
            nome,
            category=interaction.channel.category,
            overwrites=overwrites,
            reason=f"Entrevista da candidatura de {candidato}",
        )
        await interaction.response.send_message(
            f"✅ **Prova aprovada.**\n🎙️ Call privada criada: {voice.mention}\n\nDepois da entrevista, conclua a avaliação abaixo.",
            view=EtapaFinalCandidaturaView(candidato.id, voice.id),
        )
        self.stop()

    @discord.ui.button(label="Reprovar prova", style=discord.ButtonStyle.danger, emoji="❌")
    async def reprovar_prova(self, interaction: discord.Interaction, button: discord.ui.Button):
        candidato = interaction.guild.get_member(self.candidato_id)
        mencao = candidato.mention if candidato else f"<@{self.candidato_id}>"
        await interaction.response.send_message(f"❌ A candidatura de {mencao} foi **reprovada**. Este ticket será encerrado.")
        asyncio.create_task(_encerrar_canais_candidatura(interaction.channel))
        self.stop()


async def criar_ticket_candidatura_eventos(payload):
    if not GUILD_ID:
        raise RuntimeError("GUILD_ID do servidor principal não está configurado.")
    guild = bot.get_guild(int(GUILD_ID))
    if guild is None:
        raise RuntimeError("O bot não está conectado ao servidor principal.")

    candidato_id = _discord_id_do_payload(payload)
    if not candidato_id:
        raise RuntimeError("A resposta do formulário não contém um ID do Discord válido.")
    candidato = guild.get_member(candidato_id)
    if candidato is None:
        try:
            candidato = await guild.fetch_member(candidato_id)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            candidato = None
    if candidato is None:
        raise RuntimeError("O candidato precisa estar no servidor principal para o ticket ser criado.")

    diretor = _cargo_diretor_eventos(guild)
    if diretor is None:
        raise RuntimeError("Não encontrei o cargo Diretor de Eventos no servidor principal.")

    canal_base = guild.get_channel(CANAL_CANDIDATURA_PRINCIPAL_ID)
    categoria = canal_base.category if isinstance(canal_base, discord.TextChannel) else None
    nome = f"candidatura-{candidato.display_name}-{candidato.id % 10000}"[:95].lower().replace(" ", "-")

    # Evita ticket duplicado para a mesma pessoa.
    existente = discord.utils.get(guild.text_channels, name=nome)
    if existente:
        return existente

    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        candidato: discord.PermissionOverwrite(view_channel=True, read_message_history=True, send_messages=True),
        diretor: discord.PermissionOverwrite(view_channel=True, read_message_history=True, send_messages=True, manage_messages=True),
    }
    if guild.me:
        overwrites[guild.me] = discord.PermissionOverwrite(view_channel=True, read_message_history=True, send_messages=True, manage_channels=True)

    canal = await guild.create_text_channel(
        nome,
        category=categoria,
        overwrites=overwrites,
        topic=f"Candidatura temporária | Discord ID: {candidato.id}",
        reason="Resposta recebida do formulário de candidatura de Eventos",
    )

    respostas = _normalizar_respostas_formulario(payload)
    cabecalho = discord.Embed(
        title="📋 Respostas da candidatura",
        description=(
            f"**Candidato:** {candidato.mention} (`{candidato.id}`)\n"
            f"**Status:** Aguardando avaliação da prova\n\n"
            "Abaixo estão as respostas enviadas pelo formulário."
        ),
        color=discord.Color.gold(),
    )
    await canal.send(content=f"{candidato.mention} {diretor.mention}", embed=cabecalho)

    if not respostas:
        await canal.send("_O formulário não enviou respostas detalhadas._")
    else:
        atual = discord.Embed(title="🧾 Tabela de respostas", color=discord.Color.blurple())
        campos = 0
        for pergunta, resposta in respostas.items():
            pergunta = pergunta[:256] or "Pergunta"
            resposta = resposta[:1024] or "—"
            if campos >= 20:
                await canal.send(embed=atual)
                atual = discord.Embed(title="🧾 Tabela de respostas (continuação)", color=discord.Color.blurple())
                campos = 0
            atual.add_field(name=pergunta, value=resposta, inline=False)
            campos += 1
        if campos:
            await canal.send(embed=atual)

    await canal.send(
        "### Avaliação da prova\nA Diretoria de Eventos deve escolher uma opção:",
        view=AvaliacaoCandidaturaView(candidato.id),
    )
    return canal


DEFAULT_MENU = {
    "titulo": "📋 Menu do Servidor",
    "descricao": "Escolha uma das opções abaixo.",
    "cor": "5865F2",
    "botoes": [
        {
            "emoji": "⭐",
            "nome": "Opção 1",
            "resposta": "Configure a resposta desta opção pelo painel."
        },
        {
            "emoji": "💬",
            "nome": "Opção 2",
            "resposta": "Configure a resposta desta opção pelo painel."
        },
        {
            "emoji": "🏆",
            "nome": "Opção 3",
            "resposta": "Configure a resposta desta opção pelo painel."
        }
    ]
}

_config_lock = threading.Lock()


def estrutura_vazia():
    return {
        "versao": 2,
        "menus": {}
    }


def salvar_menus_sem_lock(dados):
    temporario = MENUS_FILE.with_suffix(".tmp")
    with temporario.open("w", encoding="utf-8") as arquivo:
        json.dump(dados, arquivo, ensure_ascii=False, indent=2)
    temporario.replace(MENUS_FILE)


def carregar_menus():
    with _config_lock:
        if not MENUS_FILE.exists():
            salvar_menus_sem_lock(estrutura_vazia())

        try:
            with MENUS_FILE.open("r", encoding="utf-8") as arquivo:
                dados = json.load(arquivo)
        except (OSError, json.JSONDecodeError):
            dados = estrutura_vazia()
            salvar_menus_sem_lock(dados)

        if not isinstance(dados, dict):
            dados = estrutura_vazia()

        if not isinstance(dados.get("menus"), dict):
            dados["menus"] = {}

        dados.setdefault("versao", 2)
        return dados


def salvar_menus(dados):
    with _config_lock:
        salvar_menus_sem_lock(dados)


def carregar_config_antiga():
    if not CONFIG_ANTIGO_FILE.exists():
        return None

    try:
        with CONFIG_ANTIGO_FILE.open("r", encoding="utf-8") as arquivo:
            config = json.load(arquivo)
    except (OSError, json.JSONDecodeError):
        return None

    if not isinstance(config, dict):
        return None

    if not isinstance(config.get("botoes"), list):
        return None

    return config


def menu_padrao():
    antigo = carregar_config_antiga()
    if antigo:
        config = deepcopy(DEFAULT_MENU)
        config.update({
            "titulo": antigo.get("titulo", config["titulo"]),
            "descricao": antigo.get("descricao", config["descricao"]),
            "cor": antigo.get("cor", config["cor"]),
            "botoes": antigo.get("botoes", config["botoes"])[:MAX_BOTOES]
        })
        if not config["botoes"]:
            config["botoes"] = deepcopy(DEFAULT_MENU["botoes"])
        return config

    return deepcopy(DEFAULT_MENU)


def normalizar_menu(config):
    base = menu_padrao()

    if not isinstance(config, dict):
        return base

    base["titulo"] = str(config.get("titulo") or base["titulo"])[:256]
    base["descricao"] = str(config.get("descricao") or base["descricao"])[:4000]
    base["cor"] = str(config.get("cor") or base["cor"]).replace("#", "").upper()[:6]

    botoes_recebidos = config.get("botoes")
    if isinstance(botoes_recebidos, list):
        botoes = []
        for atual in botoes_recebidos[:MAX_BOTOES]:
            if not isinstance(atual, dict):
                continue
            botoes.append({
                "emoji": str(atual.get("emoji") or "")[:100],
                "nome": str(atual.get("nome") or "Opção")[:80],
                "resposta": str(atual.get("resposta") or "Sem conteúdo configurado.")[:4000]
            })
        if botoes:
            base["botoes"] = botoes

    return base


def config_do_formulario(form):
    titulo = form.get("titulo", "").strip()
    descricao = form.get("descricao", "").strip()
    cor = form.get("cor", "D4AF37").replace("#", "").strip().upper()

    if len(cor) != 6:
        raise ValueError("A cor precisa ter 6 caracteres, por exemplo: D4AF37.")

    try:
        int(cor, 16)
    except ValueError as exc:
        raise ValueError("Cor inválida. Use apenas números 0-9 e letras A-F.") from exc

    try:
        quantidade = int(form.get("quantidade_botoes", "1"))
    except ValueError:
        quantidade = 1

    quantidade = max(1, min(quantidade, MAX_BOTOES))
    botoes = []
    for i in range(quantidade):
        botoes.append({
            "emoji": form.get(f"emoji_{i}", "").strip()[:100],
            "nome": (form.get(f"nome_{i}", "").strip() or f"Opção {i + 1}")[:80],
            "resposta": (form.get(f"resposta_{i}", "").strip() or "Sem conteúdo configurado.")[:4000]
        })

    return {
        "titulo": titulo[:256],
        "descricao": descricao[:4000],
        "cor": cor,
        "botoes": botoes
    }


# =========================================================
# BOT DO DISCORD
# =========================================================

TOKEN = os.getenv("TOKEN")
GUILD_ID = os.getenv("GUILD_ID")
BOT_LOOP = None

intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)


def cor_da_config(config):
    try:
        return int(config.get("cor", "5865F2"), 16)
    except (ValueError, TypeError):
        return 0x5865F2


def pode_usar_menu(membro):
    if not isinstance(membro, discord.Member):
        return False

    if membro.id == DONO_ID:
        return True

    return any(
        cargo.id == CARGO_DESENVOLVIMENTO_ID
        for cargo in membro.roles
    )


class MenuView(discord.ui.View):
    """Menu oficial persistente do Discord."""

    def __init__(
        self,
        config,
        canal_id,
        *,
        persistente=True
    ):
        super().__init__(
            timeout=None if persistente else 300
        )

        self.canal_id = str(canal_id)

        for indice, botao_config in enumerate(
            config.get("botoes", [])[:MAX_BOTOES]
        ):
            nome = botao_config.get("nome") or "Opção"
            emoji = botao_config.get("emoji") or None
            resposta = (
                botao_config.get("resposta")
                or "Sem conteúdo configurado."
            )

            argumentos = {
                "label": nome[:80],
                "emoji": emoji,
                "style": discord.ButtonStyle.primary,
                "row": indice // 5
            }

            if persistente:
                argumentos["custom_id"] = (
                    f"rm_menu:{self.canal_id}:{indice}"
                )

            botao = discord.ui.Button(**argumentos)

            async def callback(
                interaction: discord.Interaction,
                texto=resposta,
                titulo_botao=nome
            ):
                embed_resposta = discord.Embed(
                    title=titulo_botao,
                    description=texto,
                    color=discord.Color.gold()
                )
                await interaction.response.send_message(
                    embed=embed_resposta,
                    ephemeral=True
                )

            botao.callback = callback
            self.add_item(botao)


def registrar_views_persistentes():
    """Reativa os botões dos menus salvos após reinício/deploy."""
    try:
        bot.add_view(CandidaturaEventosView())
    except Exception as erro:
        print(f"Erro ao registrar view persistente de candidatura: {erro}")
    if getattr(bot, "_views_menus_registradas", False):
        return

    dados = carregar_menus()
    total = 0

    for canal_id, config in dados["menus"].items():
        try:
            bot.add_view(
                MenuView(
                    normalizar_menu(config),
                    canal_id,
                    persistente=True
                )
            )
            total += 1
        except Exception as erro:
            print(
                f"Erro ao registrar menu persistente "
                f"do canal {canal_id}: {erro}"
            )

    bot._views_menus_registradas = True
    print(f"Views persistentes registradas: {total}")


async def obter_canais_texto_discord():
    if not bot.is_ready():
        return []

    guild = None

    if GUILD_ID:
        try:
            guild = bot.get_guild(int(GUILD_ID))
        except ValueError:
            guild = None

    if guild is None and bot.guilds:
        guild = bot.guilds[0]

    if guild is None:
        return []

    canais = []

    for canal in guild.text_channels:
        canais.append({
            "id": str(canal.id),
            "nome": canal.name,
            "categoria": canal.category.name if canal.category else "Sem categoria",
            "posicao": canal.position
        })

    canais.sort(key=lambda item: (item["categoria"].lower(), item["posicao"], item["nome"].lower()))
    return canais


def obter_canais_texto_sync():
    if not TOKEN or not bot.is_ready() or BOT_LOOP is None:
        return []

    try:
        futuro = asyncio.run_coroutine_threadsafe(
            obter_canais_texto_discord(),
            BOT_LOOP
        )
        return futuro.result(timeout=10)
    except Exception as erro:
        print(f"Erro ao obter canais do Discord: {repr(erro)}")
        return []


async def localizar_canal(canal_id):
    try:
        canal_id_int = int(canal_id)
    except (TypeError, ValueError) as exc:
        raise RuntimeError("ID de canal inválido.") from exc

    canal = bot.get_channel(canal_id_int)

    if canal is None:
        try:
            canal = await bot.fetch_channel(canal_id_int)
        except discord.NotFound as exc:
            raise RuntimeError("Canal não encontrado.") from exc
        except discord.Forbidden as exc:
            raise RuntimeError("O bot não tem acesso ao canal.") from exc
        except discord.HTTPException as exc:
            raise RuntimeError(f"Erro do Discord ao localizar o canal: {exc}") from exc

    if not hasattr(canal, "send"):
        raise RuntimeError("O canal configurado não aceita mensagens.")

    return canal


async def enviar_teste_discord(config):
    canal = await localizar_canal(CANAL_TESTE_ID)

    embed = discord.Embed(
        title=config.get("titulo") or "Menu",
        description=config.get("descricao") or "Escolha uma opção.",
        color=cor_da_config(config)
    )

    await canal.send(
        content="⚠️ **PRÉVIA / TESTE — não substitui nenhum menu oficial**",
        embed=embed,
        view=MenuView(
            config,
            f"teste_{secrets.token_hex(4)}",
            persistente=False
        )
    )


@bot.tree.command(
    name="menu",
    description="Envia o menu configurado para este canal."
)
async def menu(interaction: discord.Interaction):
    if not pode_usar_menu(interaction.user):
        await interaction.response.send_message(
            "❌ Você não possui autorização para publicar menus.",
            ephemeral=True
        )
        return

    dados = carregar_menus()
    config = dados["menus"].get(str(interaction.channel_id))

    if not config:
        await interaction.response.send_message(
            "❌ Não existe um menu configurado para este canal.\n"
            "Configure este canal primeiro pelo painel web.",
            ephemeral=True
        )
        return

    config = normalizar_menu(config)

    embed = discord.Embed(
        title=config.get("titulo") or "Menu",
        description=config.get("descricao") or "Escolha uma opção.",
        color=cor_da_config(config)
    )

    await interaction.response.send_message(
        embed=embed,
        view=MenuView(
            config,
            str(interaction.channel_id),
            persistente=True
        )
    )


@bot.event
async def on_ready():
    global BOT_LOOP
    BOT_LOOP = asyncio.get_running_loop()

    registrar_views_persistentes()

    try:
        await publicar_menu_candidatura_principal()
    except Exception as erro:
        print(f"Erro ao publicar menu de candidatura no servidor principal: {erro}")

    # No startup, SOMENTE atualiza o canal de candidatura que já existe no
    # Departamento de Eventos. Nunca cria canais/categorias/cargos aqui.
    try:
        cfg_eventos = carregar_servidores_config()
        guild_eventos_id = int(cfg_eventos.get("eventos_guild_id") or EVENTOS_GUILD_ID or 0)
        if guild_eventos_id and bot.get_guild(guild_eventos_id):
            atualizado = await atualizar_menu_candidatura_eventos_existente(guild_eventos_id)
            if not atualizado:
                print("AVISO: canal de candidatura do servidor de Eventos não foi encontrado; nada foi criado.")
    except Exception as erro:
        print(f"Erro ao atualizar candidatura no servidor de Eventos: {erro}")

    if getattr(bot, "_menu_sync_feito", False):
        print(f"Bot conectado como {bot.user}")
        return

    try:
        if GUILD_ID:
            guild = discord.Object(id=int(GUILD_ID))
            bot.tree.copy_global_to(guild=guild)
            comandos = await bot.tree.sync(guild=guild)
            print(f"{len(comandos)} comando(s) sincronizado(s) no servidor {GUILD_ID}.")
        else:
            comandos = await bot.tree.sync()
            print(f"{len(comandos)} comando(s) global(is) sincronizado(s).")

        bot._menu_sync_feito = True
    except Exception as erro:
        print(f"Erro ao sincronizar comandos: {erro}")

    print(f"Bot conectado como {bot.user}")


def iniciar_bot():
    if not TOKEN:
        print("AVISO: TOKEN não configurado. O site abrirá, mas o bot não ficará online.")
        return

    bot.run(TOKEN, log_handler=None)



def _agora_utc_iso():
    return datetime.now(timezone.utc).isoformat()


def _parse_iso_utc(valor):
    try:
        dt = datetime.fromisoformat(str(valor or "").replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


# =========================================================
# IDENTIDADE GAMER — ROBLOX OAUTH 2.0 / OPENID CONNECT
# =========================================================

ROBLOX_VINCULOS_FILE = DATA_DIR / "roblox_vinculos.json"
_roblox_vinculos_lock = threading.Lock()

ROBLOX_VINCULO_SECRET = os.getenv(
    "ROBLOX_VINCULO_SECRET",
    "",
).strip()
ROBLOX_CLIENT_ID = os.getenv(
    "ROBLOX_CLIENT_ID",
    "",
).strip()
ROBLOX_CLIENT_SECRET = os.getenv(
    "ROBLOX_CLIENT_SECRET",
    "",
).strip()
ROBLOX_REDIRECT_URI = os.getenv(
    "ROBLOX_REDIRECT_URI",
    "https://resenha-maxima.up.railway.app/roblox/callback",
).strip()

ROBLOX_AUTHORIZE_URL = (
    "https://apis.roblox.com/oauth/v1/authorize"
)
ROBLOX_TOKEN_URL = (
    "https://apis.roblox.com/oauth/v1/token"
)
ROBLOX_USERINFO_URL = (
    "https://apis.roblox.com/oauth/v1/userinfo"
)
ROBLOX_PENDENCIA_MINUTOS = 15


def roblox_vinculos_vazio():
    return {
        "versao": 1,
        "vinculos": {},
        "pendentes": {},
    }


def _salvar_roblox_vinculos_sem_lock(dados):
    temporario = ROBLOX_VINCULOS_FILE.with_suffix(".tmp")
    with temporario.open(
        "w",
        encoding="utf-8",
    ) as arquivo:
        json.dump(
            dados,
            arquivo,
            ensure_ascii=False,
            indent=2,
        )
    temporario.replace(ROBLOX_VINCULOS_FILE)


def carregar_roblox_vinculos():
    with _roblox_vinculos_lock:
        if not ROBLOX_VINCULOS_FILE.exists():
            _salvar_roblox_vinculos_sem_lock(
                roblox_vinculos_vazio()
            )

        try:
            with ROBLOX_VINCULOS_FILE.open(
                "r",
                encoding="utf-8",
            ) as arquivo:
                dados = json.load(arquivo)
        except (
            OSError,
            json.JSONDecodeError,
        ):
            dados = roblox_vinculos_vazio()

        if not isinstance(dados, dict):
            dados = roblox_vinculos_vazio()
        if not isinstance(dados.get("vinculos"), dict):
            dados["vinculos"] = {}
        if not isinstance(dados.get("pendentes"), dict):
            dados["pendentes"] = {}
        dados.setdefault("versao", 1)

        agora = datetime.now(timezone.utc)
        expirados = []
        for token, item in dados["pendentes"].items():
            expira = _parse_iso_utc(
                item.get("expira_em")
                if isinstance(item, dict)
                else None
            )
            if expira is None or expira <= agora:
                expirados.append(token)
        if expirados:
            for token in expirados:
                dados["pendentes"].pop(token, None)
            _salvar_roblox_vinculos_sem_lock(dados)

        return dados


def salvar_roblox_vinculos(dados):
    with _roblox_vinculos_lock:
        _salvar_roblox_vinculos_sem_lock(dados)


def _autorizado_roblox(payload=None):
    segredo = request.headers.get(
        "X-Roblox-Link-Secret",
        "",
    ).strip()
    if not segredo and isinstance(payload, dict):
        segredo = str(
            payload.get("secret")
            or ""
        ).strip()

    return bool(
        ROBLOX_VINCULO_SECRET
        and segredo
        and secrets.compare_digest(
            segredo,
            ROBLOX_VINCULO_SECRET,
        )
    )


def _roblox_configurado():
    return bool(
        ROBLOX_CLIENT_ID
        and ROBLOX_CLIENT_SECRET
        and ROBLOX_REDIRECT_URI
    )


def _pagina_roblox(
    titulo,
    mensagem,
    sucesso=False,
):
    cor = "#57F287" if sucesso else "#ED4245"
    icone = "✅" if sucesso else "⚠️"
    titulo_seguro = str(titulo).replace(
        "<", "&lt;"
    ).replace(">", "&gt;")
    mensagem_segura = str(mensagem).replace(
        "<", "&lt;"
    ).replace(">", "&gt;")
    return f"""<!doctype html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{titulo_seguro} • Resenha Máxima</title>
<style>
body {{
  margin:0; min-height:100vh; display:grid; place-items:center;
  background:#111214; color:#fff; font-family:Arial,sans-serif;
}}
.card {{
  width:min(560px,calc(100% - 40px)); background:#1e1f22;
  border:1px solid #2b2d31; border-radius:18px; padding:30px;
  box-shadow:0 18px 60px rgba(0,0,0,.35);
}}
h1 {{ margin:0 0 14px; font-size:24px; }}
p {{ color:#dbdee1; line-height:1.55; white-space:pre-line; }}
.badge {{ color:{cor}; font-weight:700; }}
small {{ color:#949ba4; }}
</style>
</head>
<body>
  <main class="card">
    <div class="badge">{icone} RESENHA MÁXIMA</div>
    <h1>{titulo_seguro}</h1>
    <p>{mensagem_segura}</p>
    <small>Você já pode voltar para o Discord.</small>
  </main>
</body>
</html>"""


def _post_form_roblox(url, campos):
    corpo = urllib.parse.urlencode(
        campos
    ).encode("utf-8")
    requisicao = urllib.request.Request(
        url,
        data=corpo,
        headers={
            "Content-Type": (
                "application/x-www-form-urlencoded"
            ),
            "Accept": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(
        requisicao,
        timeout=12,
    ) as resposta:
        bruto = resposta.read().decode(
            "utf-8",
            errors="replace",
        )
    return json.loads(bruto)


def _userinfo_roblox(access_token):
    requisicao = urllib.request.Request(
        ROBLOX_USERINFO_URL,
        headers={
            "Authorization": (
                f"Bearer {access_token}"
            ),
            "Accept": "application/json",
        },
        method="GET",
    )
    with urllib.request.urlopen(
        requisicao,
        timeout=12,
    ) as resposta:
        bruto = resposta.read().decode(
            "utf-8",
            errors="replace",
        )
    return json.loads(bruto)

# =========================================================
# SITE / PAINEL WEB
# =========================================================

app = Flask(__name__)
app.secret_key = os.getenv("PANEL_SECRET_KEY") or secrets.token_hex(32)

def _segredo_webhook_valido(payload):
    if not FORM_WEBHOOK_SECRET:
        return True
    recebido = (
        request.headers.get("X-Webhook-Secret")
        or request.args.get("secret")
        or (payload.get("secret") if isinstance(payload, dict) else "")
        or ""
    )
    return secrets.compare_digest(str(recebido), str(FORM_WEBHOOK_SECRET))


def _processar_webhook_candidatura(payload):
    if not isinstance(payload, dict):
        return {"ok": False, "erro": "Envie os dados do formulário em JSON."}, 400
    if not _segredo_webhook_valido(payload):
        return {"ok": False, "erro": "Webhook não autorizado."}, 401
    if not TOKEN or not bot.is_ready() or BOT_LOOP is None:
        return {"ok": False, "erro": "O bot ainda não está conectado ao Discord."}, 503
    try:
        futuro = asyncio.run_coroutine_threadsafe(
            criar_ticket_candidatura_eventos(payload),
            BOT_LOOP,
        )
        canal = futuro.result(timeout=20)
        return {
            "ok": True,
            "ticket_channel_id": str(canal.id),
            "ticket_channel_name": canal.name,
        }, 201
    except Exception as erro:
        print(f"Erro no webhook de candidatura: {repr(erro)}")
        return {"ok": False, "erro": str(erro)}, 400


@app.route("/api/recrutamento/eventos/prova", methods=["POST"])
def webhook_recrutamento_eventos_prova():
    """Compatibilidade com o Apps Script antigo da candidatura de Eventos."""
    payload = request.get_json(silent=True)
    return _processar_webhook_candidatura(payload)


@app.route("/api/recrutamento/eventos/configurar-prefill", methods=["POST"])
def configurar_prefill_eventos():
    """Registra o Web App do Apps Script usado para abrir o Forms pré-preenchido."""
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return {"ok": False, "erro": "Envie JSON."}, 400
    if not _segredo_webhook_valido(payload):
        return {"ok": False, "erro": "Webhook não autorizado."}, 401
    url = str(payload.get("prefill_script_url") or "").strip()
    if not url.startswith("https://script.google.com/") or "/exec" not in url:
        return {"ok": False, "erro": "URL do Web App inválida."}, 400
    try:
        _salvar_prefill_script_url(url)
    except OSError as erro:
        return {"ok": False, "erro": f"Não foi possível salvar a configuração: {erro}"}, 500
    return {"ok": True, "prefill_script_url": url}, 200


@app.route("/api/candidatura-eventos", methods=["POST"])
def webhook_candidatura_eventos():
    """Rota nova; mantém compatibilidade com integrações já existentes."""
    return _processar_webhook_candidatura(request.get_json(silent=True))

USERS_FILE = DATA_DIR / "panel_users.json"
ADMIN_LOG_FILE = DATA_DIR / "admin_logs.json"

# Equipe de Desenvolvimento já definida acima.
#
# Para o Departamento de Eventos e canal de Eventos, você pode
# definir os IDs diretamente na Railway. Se não definir, o painel
# tenta localizar pelo nome no Discord.
CARGO_EVENTOS_ID = int(os.getenv("CARGO_EVENTOS_ID", "0") or "0")
CANAL_EVENTOS_ID = int(os.getenv("CANAL_EVENTOS_ID", "0") or "0")

_users_lock = threading.Lock()

MODELOS_MENSAGENS = [
    {
        "categoria": "Eventos",
        "titulo": "Candidatura — link da prova",
        "destino": "Canal de candidatura",
        "conteudo": "📜・𝑪𝒂𝒏𝒅𝒊𝒅𝒂𝒕𝒖𝒓𝒂\n\nFaça a prova para tentar entrar como Aprendiz de Eventos.\n\n🎓 Fazer prova: https://forms.gle/h4kt2Cp7fduGG4Pc8",
    },
    {
        "categoria": "Eventos",
        "titulo": "Intruso — acesso inicial",
        "destino": "Servidor do Departamento de Eventos",
        "conteudo": "Você entrou como Intruso. O acesso ao restante do servidor é liberado somente após aprovação da candidatura.",
    },

    {
        "categoria": "Minecraft",
        "titulo": "Solicitação de nickname",
        "destino": "DM do membro",
        "conteudo": (
            "🎮 Cadastro do Minecraft\n\n"
            "Precisamos do seu nickname do Minecraft para concluir seu cadastro.\n"
            "Responda esta mensagem com o nickname que você usa no servidor."
        ),
    },
    {
        "categoria": "Minecraft",
        "titulo": "Nickname pendente",
        "destino": "DM do membro",
        "conteudo": (
            "⚠️ Seu nickname do Minecraft ainda está pendente.\n\n"
            "Envie seu nickname para concluir o cadastro. "
            "O sistema pode enviar até 4 avisos dentro de 48 horas."
        ),
    },
    {
        "categoria": "Minecraft",
        "titulo": "DM fechada",
        "destino": "Canal do servidor",
        "conteudo": (
            "📩 Não consegui enviar uma mensagem no seu privado.\n"
            "Abra suas DMs para que o cadastro do nickname possa continuar."
        ),
    },
    {
        "categoria": "Minecraft",
        "titulo": "Nickname cadastrado",
        "destino": "DM do membro",
        "conteudo": (
            "✅ Nickname cadastrado com sucesso.\n\n"
            "Seu nickname foi adicionado à tabela do Minecraft."
        ),
    },
    {
        "categoria": "Minecraft",
        "titulo": "Status Online",
        "destino": "Canal de status",
        "conteudo": (
            "🟢 SERVIDOR MINECRAFT ONLINE\n\n"
            "O servidor da Resenha Máxima está disponível agora."
        ),
    },
    {
        "categoria": "Minecraft",
        "titulo": "Status Offline",
        "destino": "Canal de status",
        "conteudo": (
            "🔴 SERVIDOR MINECRAFT OFFLINE\n\n"
            "O servidor da Resenha Máxima está offline no momento."
        ),
    },
    {
        "categoria": "Moderação",
        "titulo": "Solicitação de Ban / Hackban",
        "destino": "Canal de aprovação",
        "conteudo": (
            "⚠️ Solicitação de Ban / Hackban\n\n"
            "Exibe usuário, ID, solicitante, motivo e status da solicitação "
            "com os controles de aprovação e negação."
        ),
    },
    {
        "categoria": "Administração",
        "titulo": "Canal limpo",
        "destino": "Canal de comandos",
        "conteudo": (
            "🧹 Este canal foi limpo.\n"
            "Essa ação foi feita para evitar acúmulo de mensagens.\n\n"
            "Este aviso desaparece automaticamente após 3 novas mensagens."
        ),
    },
    {
        "categoria": "Bot",
        "titulo": "Funções do Bot",
        "destino": "Canal de funções",
        "conteudo": (
            "🤖 Funções do Bot\n\n"
            "Apresentação oficial com funções atuais, última atualização "
            "e histórico de funções removidas."
        ),
    },
]


def usuarios_vazios():
    return {"versao": 1, "usuarios": {}}


def carregar_usuarios():
    with _users_lock:
        if not USERS_FILE.exists():
            salvar_usuarios_sem_lock(usuarios_vazios())

        try:
            with USERS_FILE.open("r", encoding="utf-8") as arquivo:
                dados = json.load(arquivo)
        except (OSError, json.JSONDecodeError):
            dados = usuarios_vazios()
            salvar_usuarios_sem_lock(dados)

        if not isinstance(dados, dict):
            dados = usuarios_vazios()

        if not isinstance(dados.get("usuarios"), dict):
            dados["usuarios"] = {}

        return dados


def salvar_usuarios_sem_lock(dados):
    temporario = USERS_FILE.with_suffix(".tmp")

    with temporario.open("w", encoding="utf-8") as arquivo:
        json.dump(
            dados,
            arquivo,
            ensure_ascii=False,
            indent=2
        )

    temporario.replace(USERS_FILE)


def salvar_usuarios(dados):
    with _users_lock:
        salvar_usuarios_sem_lock(dados)


def normalizar_nome_cargo(nome):
    return (
        str(nome or "")
        .strip()
        .casefold()
        .replace("-", " ")
        .replace("_", " ")
    )


def obter_guild_painel():
    guild = None

    if GUILD_ID:
        try:
            guild = bot.get_guild(int(GUILD_ID))
        except ValueError:
            guild = None

    if guild is None and bot.guilds:
        guild = bot.guilds[0]

    return guild


async def verificar_permissao_discord(discord_id):
    guild = obter_guild_painel()

    if guild is None:
        return {
            "ok": False,
            "nivel": None,
            "nome": None,
            "erro": "O bot do painel ainda não está conectado ao servidor."
        }

    try:
        discord_id = int(discord_id)
    except (TypeError, ValueError):
        return {
            "ok": False,
            "nivel": None,
            "nome": None,
            "erro": "ID do Discord inválido."
        }

    membro = guild.get_member(discord_id)

    if membro is None:
        try:
            membro = await guild.fetch_member(discord_id)
        except (
            discord.NotFound,
            discord.Forbidden,
            discord.HTTPException
        ):
            return {
                "ok": False,
                "nivel": None,
                "nome": None,
                "erro": "Esse usuário não foi encontrado no servidor."
            }

    if membro.id == DONO_ID:
        return {
            "ok": True,
            "nivel": "full",
            "nome": str(membro),
            "erro": None
        }

    ids_cargos = {
        cargo.id
        for cargo in membro.roles
    }

    if CARGO_DESENVOLVIMENTO_ID in ids_cargos:
        return {
            "ok": True,
            "nivel": "full",
            "nome": str(membro),
            "erro": None
        }

    cargo_eventos_encontrado = False

    if CARGO_EVENTOS_ID:
        cargo_eventos_encontrado = (
            CARGO_EVENTOS_ID in ids_cargos
        )
    else:
        for cargo in membro.roles:
            nome = normalizar_nome_cargo(cargo.name)

            if nome in {
                "departamento de eventos",
                "departamento eventos",
                "equipe de eventos",
                "eventos"
            }:
                cargo_eventos_encontrado = True
                break

    if cargo_eventos_encontrado:
        return {
            "ok": True,
            "nivel": "eventos",
            "nome": str(membro),
            "erro": None
        }

    return {
        "ok": False,
        "nivel": None,
        "nome": str(membro),
        "erro": (
            "O usuário não possui o cargo Equipe de Desenvolvimento "
            "nem Departamento de Eventos."
        )
    }


def verificar_permissao_discord_sync(discord_id):
    if not bot.is_ready() or BOT_LOOP is None:
        return {
            "ok": False,
            "nivel": None,
            "nome": None,
            "erro": "O bot do painel ainda não está conectado ao Discord."
        }

    try:
        futuro = asyncio.run_coroutine_threadsafe(
            verificar_permissao_discord(discord_id),
            BOT_LOOP
        )
        return futuro.result(timeout=12)

    except Exception as erro:
        return {
            "ok": False,
            "nivel": None,
            "nome": None,
            "erro": f"Falha ao verificar o Discord: {erro}"
        }


def canal_eventos_id(canais=None):
    if CANAL_EVENTOS_ID:
        return str(CANAL_EVENTOS_ID)

    canais = canais or obter_canais_texto_sync()

    nomes_exatos = {
        "eventos",
        "evento",
        "departamento-de-eventos",
        "departamento de eventos",
    }

    for canal in canais:
        nome = str(canal.get("nome", "")).casefold()

        if nome in nomes_exatos:
            return canal["id"]

    return None


def nivel_sessao():
    return session.get("nivel")


def acesso_total():
    return nivel_sessao() == "full"


def canal_permitido_para_sessao(canal_id, canais=None):
    if acesso_total():
        return True

    if nivel_sessao() != "eventos":
        return False

    eventos_id = canal_eventos_id(canais)

    return (
        eventos_id is not None
        and str(canal_id) == str(eventos_id)
    )


def filtrar_canais_por_permissao(canais):
    if acesso_total():
        return canais

    if nivel_sessao() == "eventos":
        eventos_id = canal_eventos_id(canais)

        if eventos_id is None:
            return []

        return [
            canal
            for canal in canais
            if canal["id"] == eventos_id
        ]

    return []


def login_obrigatorio(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        if not session.get("logado"):
            return redirect(url_for("login"))

        return func(*args, **kwargs)

    return wrapper


def somente_full(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        if not session.get("logado"):
            return redirect(url_for("login"))

        if not acesso_total():
            flash("❌ Você não possui acesso a esta área.")
            return redirect(url_for("painel"))

        return func(*args, **kwargs)

    return wrapper


@app.get("/api/site-account/<int:discord_id>")
def api_site_account(discord_id):
    dados = carregar_usuarios()
    existe = any(str(registro.get("discord_id", "")) == str(discord_id) for registro in dados.get("usuarios", {}).values())
    # A conta mestre também é uma conta válida.
    if str(discord_id) == str(DONO_ID):
        existe = True
    return {"exists": bool(existe), "site": SITE_PUBLIC_URL}

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        usuario = request.form.get("usuario", "").strip()
        senha = request.form.get("senha", "")

        # Login mestre antigo continua funcionando.
        if secrets.compare_digest(senha, PANEL_PASSWORD):
            session.clear()
            session["logado"] = True
            session["usuario"] = "Administrador principal"
            session["nivel"] = "full"
            session["discord_id"] = str(DONO_ID)
            session["login_mestre"] = True

            return redirect(url_for("painel"))

        dados = carregar_usuarios()
        registro = dados["usuarios"].get(
            usuario.casefold()
        )

        if (
            registro
            and check_password_hash(
                registro.get("senha_hash", ""),
                senha
            )
        ):
            permissao = verificar_permissao_discord_sync(
                registro.get("discord_id")
            )

            if not permissao["ok"]:
                flash(
                    "❌ Sua conta existe, mas o acesso do Discord "
                    f"não pôde ser validado: {permissao['erro']}"
                )
                return render_template("login.html")

            session.clear()
            session["logado"] = True
            session["usuario"] = usuario
            session["discord_id"] = str(
                registro.get("discord_id")
            )
            session["discord_nome"] = permissao.get("nome")
            session["nivel"] = permissao["nivel"]
            session["login_mestre"] = False

            return redirect(url_for("painel"))

        flash("❌ Usuário ou senha incorretos.")

    return render_template("login.html")


@app.route("/sair")
def sair():
    session.clear()
    return redirect(url_for("login"))


def carregar_nota_atualizacao_site():
    candidatos = (
        Path(__file__).parent / "NOTA_ATUALIZACAO.json",
        Path(__file__).parent.parent / "NOTA_ATUALIZACAO.json",
    )
    for caminho in candidatos:
        if caminho.exists():
            try:
                dados = json.loads(caminho.read_text(encoding="utf-8"))
                return dados if isinstance(dados, dict) else {}
            except (OSError, json.JSONDecodeError):
                pass
    return {}


def carregar_futuras_atualizacoes_site():
    caminho = Path(__file__).parent / "FUTURAS_ATUALIZACOES.json"
    if not caminho.exists():
        return []
    try:
        dados = json.loads(caminho.read_text(encoding="utf-8"))
        return dados if isinstance(dados, list) else []
    except (OSError, json.JSONDecodeError):
        return []


async def obter_solicitacoes_ban_discord():
    if not bot.is_ready():
        return []
    canal = bot.get_channel(CANAL_APROVACAO_ID)
    if canal is None:
        try:
            canal = await bot.fetch_channel(CANAL_APROVACAO_ID)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            return []
    if not isinstance(canal, discord.TextChannel):
        return []
    resultado = []
    try:
        async for mensagem in canal.history(limit=30):
            if not mensagem.embeds:
                continue
            embed = mensagem.embeds[0]
            titulo = str(embed.title or "")
            footer = str(embed.footer.text or "") if embed.footer else ""
            if "ban" not in titulo.casefold() and "solicitação:" not in footer.casefold():
                continue
            campos = []
            for campo in embed.fields:
                campos.append({"nome": campo.name, "valor": campo.value})
            resultado.append({
                "message_id": str(mensagem.id),
                "url": mensagem.jump_url,
                "titulo": titulo or "Solicitação de Ban/Hackban",
                "descricao": str(embed.description or ""),
                "cor": embed.color.value if embed.color else 0x5865F2,
                "campos": campos,
                "data": mensagem.created_at.strftime("%d/%m/%Y %H:%M"),
                "autor": str(mensagem.author),
            })
    except (discord.Forbidden, discord.HTTPException):
        return []
    return resultado


def obter_solicitacoes_ban_sync():
    if not TOKEN or not bot.is_ready() or BOT_LOOP is None:
        return []
    try:
        futuro = asyncio.run_coroutine_threadsafe(
            obter_solicitacoes_ban_discord(),
            BOT_LOOP
        )
        return futuro.result(timeout=12)
    except Exception as erro:
        print(f"Erro ao carregar painel de Ban: {erro}")
        return []


def carregar_logs_administrativos():
    """Carrega a central própria do painel sem consultar a DM."""
    if not ADMIN_LOG_FILE.exists():
        return []

    try:
        with ADMIN_LOG_FILE.open("r", encoding="utf-8") as arquivo:
            dados = json.load(arquivo)
    except (OSError, json.JSONDecodeError):
        return []

    if not isinstance(dados, list):
        return []

    registros = []
    for item in dados[-100:]:
        if not isinstance(item, dict):
            continue
        registros.append({
            "data": str(item.get("data", "")),
            "tipo": str(item.get("tipo", "Sistema")),
            "texto": str(item.get("texto", "")),
        })

    return list(reversed(registros))


def contexto_painel(
    canal_id=None,
    config_temporaria=None,
    aba="menus"
):
    dados = carregar_menus()
    canais_todos = obter_canais_texto_sync()
    canais = filtrar_canais_por_permissao(
        canais_todos
    )

    if nivel_sessao() == "eventos":
        aba = "menus"

    abas_validas = {
        "menus",
        "modelos",
        "central",
        "servidores",
        "atualizacoes",
    }

    if aba not in abas_validas:
        aba = "menus"

    if (
        aba in {"modelos", "central"}
        and not acesso_total()
    ):
        aba = "menus"

    ids_validos = {
        canal["id"]
        for canal in canais
    }

    if canal_id is not None:
        canal_id = str(canal_id)

    if (
        canal_id
        and canal_id not in ids_validos
    ):
        canal_id = None

    if not canal_id:
        configurados = list(
            dados["menus"].keys()
        )

        for configurado in configurados:
            if configurado in ids_validos:
                canal_id = configurado
                break

    if not canal_id and canais:
        canal_id = canais[0]["id"]

    canal_atual = next(
        (
            canal
            for canal in canais
            if canal["id"] == canal_id
        ),
        None
    )

    if config_temporaria is not None:
        config = normalizar_menu(
            config_temporaria
        )

    elif (
        canal_id
        and canal_id in dados["menus"]
    ):
        config = normalizar_menu(
            dados["menus"][canal_id]
        )

    else:
        config = menu_padrao()

    menus_configurados = []

    for id_menu, menu in dados["menus"].items():
        if id_menu not in ids_validos:
            continue

        canal = next(
            (
                item
                for item in canais
                if item["id"] == id_menu
            ),
            None
        )

        menus_configurados.append({
            "canal_id": id_menu,
            "canal_nome": (
                canal["nome"]
                if canal
                else menu.get(
                    "canal_nome",
                    f"Canal {id_menu}"
                )
            ),
            "titulo": (
                menu.get("titulo")
                or "Menu sem título"
            ),
            "ativo": (
                id_menu == canal_id
            )
        })

    menus_configurados.sort(
        key=lambda item: item[
            "canal_nome"
        ].lower()
    )

    usuarios = []

    if acesso_total():
        dados_usuarios = carregar_usuarios()

        for chave, registro in dados_usuarios[
            "usuarios"
        ].items():
            usuarios.append({
                "usuario": registro.get(
                    "usuario",
                    chave
                ),
                "discord_id": str(
                    registro.get("discord_id", "")
                ),
                "nivel_ultimo_login": registro.get(
                    "nivel_ultimo_login",
                    "revalidado no login"
                ),
            })

        usuarios.sort(
            key=lambda item: item[
                "usuario"
            ].casefold()
        )

    nota_atualizacao = carregar_nota_atualizacao_site()
    futuras_atualizacoes = carregar_futuras_atualizacoes_site()
    solicitacoes_ban = obter_solicitacoes_ban_sync() if aba == "central" and acesso_total() else []

    logs_admin = (
        carregar_logs_administrativos()
        if aba == "central"
        and acesso_total()
        else []
    )

    return {
        "servidores_eventos": servidores_disponiveis_eventos(),
        "servidor_eventos_config": carregar_servidores_config(),
        "aba": aba,
        "config": config,
        "canais": canais,
        "canal_id": canal_id,
        "canal_atual": canal_atual,
        "menus_configurados": menus_configurados,
        "canal_teste_id": CANAL_TESTE_ID,
        "bot_conectado": (
            bot.is_ready()
            if TOKEN
            else False
        ),
        "max_botoes": MAX_BOTOES,
        "nivel": nivel_sessao(),
        "acesso_total": acesso_total(),
        "usuario_logado": session.get(
            "usuario",
            "Usuário"
        ),
        "discord_nome": session.get(
            "discord_nome"
        ),
        "modelos_mensagens": MODELOS_MENSAGENS,
        "logs_admin": logs_admin,
        "nota_atualizacao": nota_atualizacao,
        "futuras_atualizacoes": futuras_atualizacoes,
        "solicitacoes_ban": solicitacoes_ban,
        "usuarios_painel": usuarios,
        "cargo_eventos_configurado": bool(
            CARGO_EVENTOS_ID
        ),
        "canal_eventos_configurado": bool(
            CANAL_EVENTOS_ID
        ),
    }


@app.route("/", methods=["GET", "POST"])
@login_obrigatorio
def painel():
    aba = request.args.get(
        "aba",
        "menus"
    )

    if request.method == "GET":
        canal_id = request.args.get(
            "canal"
        )

        return render_template(
            "index.html",
            **contexto_painel(
                canal_id=canal_id,
                aba=aba
            )
        )

    # Somente a aba de menus usa o POST principal.
    canal_id = request.form.get(
        "canal_id",
        ""
    ).strip()

    if not canal_id:
        flash(
            "❌ Selecione um canal antes de editar o menu."
        )
        return redirect(
            url_for(
                "painel",
                aba="menus"
            )
        )

    canais = filtrar_canais_por_permissao(
        obter_canais_texto_sync()
    )

    if not canal_permitido_para_sessao(
        canal_id,
        canais
    ):
        flash(
            "❌ Você não possui permissão "
            "para editar esse canal."
        )
        return redirect(
            url_for(
                "painel",
                aba="menus"
            )
        )

    acao = request.form.get(
        "acao",
        "salvar"
    )

    try:
        novo_config = config_do_formulario(
            request.form
        )

    except ValueError as erro:
        flash(f"❌ {erro}")

        return render_template(
            "index.html",
            **contexto_painel(
                canal_id=canal_id,
                config_temporaria=request.form,
                aba="menus"
            )
        )

    canal_atual = next(
        (
            canal
            for canal in canais
            if canal["id"] == canal_id
        ),
        None
    )

    if canais and canal_atual is None:
        flash(
            "❌ O canal selecionado não foi "
            "encontrado no servidor."
        )
        return redirect(
            url_for(
                "painel",
                aba="menus"
            )
        )

    if acao == "testar":
        if not TOKEN:
            flash(
                "❌ TOKEN não configurado. "
                "Não foi possível enviar a prévia."
            )
            return render_template(
                "index.html",
                **contexto_painel(
                    canal_id=canal_id,
                    config_temporaria=novo_config,
                    aba="menus"
                )
            )

        if (
            not bot.is_ready()
            or BOT_LOOP is None
        ):
            flash(
                "❌ O bot ainda não está "
                "conectado ao Discord."
            )
            return render_template(
                "index.html",
                **contexto_painel(
                    canal_id=canal_id,
                    config_temporaria=novo_config,
                    aba="menus"
                )
            )

        try:
            futuro = asyncio.run_coroutine_threadsafe(
                enviar_teste_discord(
                    novo_config
                ),
                BOT_LOOP
            )
            futuro.result(timeout=15)

            flash(
                "🧪 Prévia enviada ao canal de testes. "
                "Nenhum menu oficial foi alterado."
            )

        except Exception as erro:
            print(
                "Erro ao enviar prévia pelo painel: "
                f"{repr(erro)}"
            )

            flash(
                "❌ Não foi possível enviar a prévia: "
                f"{erro}"
            )

        return render_template(
            "index.html",
            **contexto_painel(
                canal_id=canal_id,
                config_temporaria=novo_config,
                aba="menus"
            )
        )

    if acao == "excluir":
        dados = carregar_menus()

        if canal_id in dados["menus"]:
            del dados["menus"][canal_id]
            salvar_menus(dados)
            flash(
                "🗑️ Menu removido deste canal."
            )

        else:
            flash(
                "ℹ️ Este canal ainda não "
                "possuía um menu salvo."
            )

        return redirect(
            url_for(
                "painel",
                aba="menus",
                canal=canal_id
            )
        )

    canal_destino_id = request.form.get(
        "salvar_destino_id",
        canal_id
    ).strip()

    if not canal_permitido_para_sessao(
        canal_destino_id,
        canais
    ):
        flash(
            "❌ Você não possui permissão "
            "para salvar nesse canal."
        )

        return render_template(
            "index.html",
            **contexto_painel(
                canal_id=canal_id,
                config_temporaria=novo_config,
                aba="menus"
            )
        )

    canal_destino = next(
        (
            canal
            for canal in canais
            if canal["id"] == canal_destino_id
        ),
        None
    )

    if canais and canal_destino is None:
        flash(
            "❌ O canal escolhido para salvar "
            "não foi encontrado no servidor."
        )
        return render_template(
            "index.html",
            **contexto_painel(
                canal_id=canal_id,
                config_temporaria=novo_config,
                aba="menus"
            )
        )

    dados = carregar_menus()

    menu_salvo = deepcopy(
        novo_config
    )

    menu_salvo["canal_id"] = (
        canal_destino_id
    )

    menu_salvo["canal_nome"] = (
        canal_destino["nome"]
        if canal_destino
        else f"Canal {canal_destino_id}"
    )

    dados["menus"][
        canal_destino_id
    ] = menu_salvo

    salvar_menus(dados)

    flash(
        "✅ Menu salvo em "
        f"#{menu_salvo['canal_nome']}. "
        "Use /menu nesse canal para publicá-lo."
    )

    return redirect(
        url_for(
            "painel",
            aba="menus",
            canal=canal_destino_id
        )
    )



@app.route("/servidores", methods=["POST"])
@somente_full
def configurar_servidor_pelo_site():
    guild_id = request.form.get("guild_id", "").strip()
    if not guild_id.isdigit():
        flash("❌ Servidor inválido.")
        return redirect(url_for("painel", aba="servidores"))

    try:
        if not TOKEN or not bot.is_ready() or BOT_LOOP is None:
            raise RuntimeError("O bot do painel ainda não está conectado ao Discord.")

        futuro = asyncio.run_coroutine_threadsafe(
            configurar_servidor_eventos(int(guild_id)),
            BOT_LOOP
        )
        resultado = futuro.result(timeout=120)
        flash(
            "✅ Servidor do Departamento de Eventos configurado: "
            f"{resultado['guild_name']}."
        )
    except Exception as erro:
        print(f"Erro ao configurar servidor de eventos: {repr(erro)}")
        flash(f"❌ Não foi possível configurar o servidor: {erro}")

    return redirect(url_for("painel", aba="servidores"))

@app.route(
    "/usuarios/criar",
    methods=["POST"]
)
@somente_full
def criar_usuario_painel():
    usuario = request.form.get(
        "novo_usuario",
        ""
    ).strip()

    senha = request.form.get(
        "nova_senha",
        ""
    )

    discord_id = request.form.get(
        "discord_id",
        ""
    ).strip()

    if len(usuario) < 3:
        flash(
            "❌ O usuário precisa ter pelo menos 3 caracteres."
        )
        return redirect(
            url_for(
                "painel",
                aba="central"
            )
        )

    if len(senha) < 6:
        flash(
            "❌ A senha precisa ter pelo menos 6 caracteres."
        )
        return redirect(
            url_for(
                "painel",
                aba="central"
            )
        )

    permissao = verificar_permissao_discord_sync(
        discord_id
    )

    if not permissao["ok"]:
        flash(
            "❌ Conta não criada: "
            f"{permissao['erro']}"
        )
        return redirect(
            url_for(
                "painel",
                aba="central"
            )
        )

    dados = carregar_usuarios()
    chave = usuario.casefold()

    if chave in dados["usuarios"]:
        flash(
            "❌ Já existe um usuário com esse nome."
        )
        return redirect(
            url_for(
                "painel",
                aba="central"
            )
        )

    dados["usuarios"][chave] = {
        "usuario": usuario,
        "discord_id": str(discord_id),
        "senha_hash": generate_password_hash(
            senha
        ),
        "nivel_ultimo_login": permissao[
            "nivel"
        ],
    }

    salvar_usuarios(dados)

    flash(
        "✅ Usuário criado: "
        f"{usuario} • Discord: {permissao['nome']} • "
        f"Nível: {permissao['nivel']}."
    )

    return redirect(
        url_for(
            "painel",
            aba="central"
        )
    )


@app.route(
    "/usuarios/excluir/<usuario>",
    methods=["POST"]
)
@somente_full
def excluir_usuario_painel(usuario):
    dados = carregar_usuarios()
    chave = usuario.casefold()

    if chave in dados["usuarios"]:
        del dados["usuarios"][chave]
        salvar_usuarios(dados)

        flash(
            f"🗑️ Usuário {usuario} removido do painel."
        )

    return redirect(
        url_for(
            "painel",
            aba="central"
        )
    )


@app.route("/atualizacoes/futuras/adicionar", methods=["POST"])
@somente_full
def adicionar_futura_atualizacao():
    texto = request.form.get("texto", "").strip()
    if not texto:
        flash("❌ Informe a futura atualização.")
        return redirect(url_for("painel", aba="atualizacoes"))
    caminho = Path(__file__).parent / "FUTURAS_ATUALIZACOES.json"
    try:
        dados = json.loads(caminho.read_text(encoding="utf-8")) if caminho.exists() else []
        if not isinstance(dados, list):
            dados = []
        if texto not in dados:
            dados.append(texto)
            caminho.write_text(json.dumps(dados, ensure_ascii=False, indent=2), encoding="utf-8")
            flash("✅ Futura atualização adicionada.")
        else:
            flash("ℹ️ Essa futura atualização já está cadastrada.")
    except Exception as erro:
        flash(f"❌ Não foi possível salvar: {erro}")
    return redirect(url_for("painel", aba="atualizacoes"))

@app.route("/atualizacoes/futuras/remover", methods=["POST"])
@somente_full
def remover_futura_atualizacao():
    texto = request.form.get("texto", "").strip()
    caminho = Path(__file__).parent / "FUTURAS_ATUALIZACOES.json"
    try:
        dados = json.loads(caminho.read_text(encoding="utf-8")) if caminho.exists() else []
        if not isinstance(dados, list): dados = []
        dados = [item for item in dados if str(item) != texto]
        caminho.write_text(json.dumps(dados, ensure_ascii=False, indent=2), encoding="utf-8")
        flash("🗑️ Futura atualização removida.")
    except Exception as erro:
        flash(f"❌ Não foi possível remover: {erro}")
    return redirect(url_for("painel", aba="atualizacoes"))

# =========================================================
# ROBLOX — API INTERNA + OAUTH
# =========================================================

@app.route(
    "/api/roblox/criar-vinculo",
    methods=["POST"],
)
def api_roblox_criar_vinculo():
    payload = request.get_json(silent=True) or {}
    if not _autorizado_roblox(payload):
        return jsonify({
            "ok": False,
            "erro": "Não autorizado.",
        }), 401

    if not _roblox_configurado():
        return jsonify({
            "ok": False,
            "erro": (
                "OAuth Roblox ainda não configurado no SITE. "
                "Defina ROBLOX_CLIENT_ID, ROBLOX_CLIENT_SECRET "
                "e ROBLOX_REDIRECT_URI."
            ),
        }), 503

    discord_id = str(
        payload.get("discord_id")
        or ""
    ).strip()
    guild_id = str(
        payload.get("guild_id")
        or ""
    ).strip()
    discord_nome = str(
        payload.get("discord_nome")
        or ""
    ).strip()[:150]

    if not discord_id.isdigit():
        return jsonify({
            "ok": False,
            "erro": "Discord ID inválido.",
        }), 400

    dados = carregar_roblox_vinculos()

    # Mantém somente a solicitação mais recente do usuário.
    for token_antigo, pendente in list(
        dados["pendentes"].items()
    ):
        if str(
            pendente.get("discord_id")
            or ""
        ) == discord_id:
            dados["pendentes"].pop(
                token_antigo,
                None,
            )

    token = secrets.token_urlsafe(24)
    oauth_state = secrets.token_urlsafe(32)
    nonce = secrets.token_urlsafe(24)
    agora = datetime.now(timezone.utc)
    expira = agora + timedelta(
        minutes=ROBLOX_PENDENCIA_MINUTOS
    )

    dados["pendentes"][token] = {
        "discord_id": discord_id,
        "discord_nome": discord_nome,
        "guild_id": guild_id,
        "oauth_state": oauth_state,
        "nonce": nonce,
        "criado_em": agora.isoformat(),
        "expira_em": expira.isoformat(),
    }
    salvar_roblox_vinculos(dados)

    url = (
        request.url_root.rstrip("/")
        + url_for(
            "roblox_iniciar",
            token=token,
        )
    )
    return jsonify({
        "ok": True,
        "url": url,
        "expira_em": expira.isoformat(),
    })


@app.route("/roblox/iniciar/<token>")
def roblox_iniciar(token):
    dados = carregar_roblox_vinculos()
    pendente = dados["pendentes"].get(
        str(token)
    )
    if not pendente:
        return (
            _pagina_roblox(
                "Link expirado",
                (
                    "Este link de vinculação não existe mais "
                    "ou passou do prazo de 15 minutos.\n\n"
                    "Use /roblox vincular novamente no Discord."
                ),
            ),
            410,
        )

    if not _roblox_configurado():
        return (
            _pagina_roblox(
                "Roblox ainda não configurado",
                (
                    "O sistema foi instalado, mas as credenciais "
                    "OAuth do Roblox ainda não foram configuradas "
                    "no site."
                ),
            ),
            503,
        )

    parametros = {
        "client_id": ROBLOX_CLIENT_ID,
        "redirect_uri": ROBLOX_REDIRECT_URI,
        "scope": "openid profile",
        "response_type": "code",
        "state": pendente["oauth_state"],
        "nonce": pendente["nonce"],
    }
    destino = (
        ROBLOX_AUTHORIZE_URL
        + "?"
        + urllib.parse.urlencode(parametros)
    )
    return redirect(destino)


@app.route("/roblox/callback")
def roblox_callback():
    erro_oauth = str(
        request.args.get("error")
        or ""
    ).strip()
    if erro_oauth:
        descricao = str(
            request.args.get("error_description")
            or "A autorização foi cancelada ou recusada."
        )
        return (
            _pagina_roblox(
                "Vinculação cancelada",
                descricao,
            ),
            400,
        )

    code = str(
        request.args.get("code")
        or ""
    ).strip()
    state = str(
        request.args.get("state")
        or ""
    ).strip()
    if not code or not state:
        return (
            _pagina_roblox(
                "Resposta inválida",
                "O Roblox não devolveu o código de autorização esperado.",
            ),
            400,
        )

    dados = carregar_roblox_vinculos()
    token_pendente = None
    pendente = None
    for token, item in dados["pendentes"].items():
        if secrets.compare_digest(
            str(item.get("oauth_state") or ""),
            state,
        ):
            token_pendente = token
            pendente = item
            break

    if not pendente:
        return (
            _pagina_roblox(
                "Sessão expirada",
                (
                    "Não encontrei uma solicitação válida para "
                    "esta autorização. Use /roblox vincular novamente."
                ),
            ),
            410,
        )

    try:
        tokens = _post_form_roblox(
            ROBLOX_TOKEN_URL,
            {
                "code": code,
                "grant_type": "authorization_code",
                "client_id": ROBLOX_CLIENT_ID,
                "client_secret": ROBLOX_CLIENT_SECRET,
                "redirect_uri": ROBLOX_REDIRECT_URI,
            },
        )
        access_token = str(
            tokens.get("access_token")
            or ""
        ).strip()
        if not access_token:
            raise RuntimeError(
                "O Roblox não devolveu access_token."
            )

        perfil = _userinfo_roblox(
            access_token
        )
    except (
        urllib.error.HTTPError,
        urllib.error.URLError,
        TimeoutError,
        json.JSONDecodeError,
        RuntimeError,
    ) as erro:
        print(
            "Erro no OAuth Roblox: "
            f"{type(erro).__name__}: {erro}"
        )
        return (
            _pagina_roblox(
                "Falha ao verificar a conta",
                (
                    "Não consegui concluir a verificação com o "
                    "Roblox agora. Tente novamente pelo Discord."
                ),
            ),
            502,
        )

    roblox_id = str(
        perfil.get("sub")
        or ""
    ).strip()
    if not roblox_id.isdigit():
        return (
            _pagina_roblox(
                "Perfil Roblox inválido",
                (
                    "O Roblox autenticou a sessão, mas não enviou "
                    "um User ID válido."
                ),
            ),
            502,
        )

    discord_id = str(
        pendente.get("discord_id")
        or ""
    )

    # Uma conta Roblox não pode representar dois Discords ao mesmo tempo.
    for outro_discord_id, vinculo in dados[
        "vinculos"
    ].items():
        if (
            outro_discord_id != discord_id
            and str(
                vinculo.get("roblox_id")
                or ""
            ) == roblox_id
        ):
            return (
                _pagina_roblox(
                    "Conta já vinculada",
                    (
                        "Essa conta Roblox já está vinculada a "
                        "outro membro do Discord.\n\n"
                        "Se isso estiver incorreto, procure a administração."
                    ),
                ),
                409,
            )

    username = str(
        perfil.get("preferred_username")
        or perfil.get("nickname")
        or perfil.get("name")
        or roblox_id
    )[:80]
    display_name = str(
        perfil.get("name")
        or perfil.get("nickname")
        or username
    )[:80]
    profile_url = str(
        perfil.get("profile")
        or (
            f"https://www.roblox.com/users/"
            f"{roblox_id}/profile"
        )
    )[:500]
    picture = str(
        perfil.get("picture")
        or ""
    )[:1000]

    dados["vinculos"][discord_id] = {
        "discord_id": discord_id,
        "discord_nome": str(
            pendente.get("discord_nome")
            or ""
        )[:150],
        "guild_id": str(
            pendente.get("guild_id")
            or ""
        ),
        "roblox_id": roblox_id,
        "username": username,
        "display_name": display_name,
        "profile": profile_url,
        "picture": picture,
        "verificado_em": _agora_utc_iso(),
    }
    if token_pendente:
        dados["pendentes"].pop(
            token_pendente,
            None,
        )
    salvar_roblox_vinculos(dados)

    return _pagina_roblox(
        "Conta Roblox vinculada!",
        (
            f"Roblox: @{username}\n"
            f"Display: {display_name}\n\n"
            "Volte ao Discord e clique em “Já vinculei”. "
            "Depois disso, a conta aparecerá no /perfil."
        ),
        sucesso=True,
    )


@app.route("/api/roblox/vinculo/<discord_id>")
def api_roblox_vinculo(discord_id):
    if not _autorizado_roblox():
        return jsonify({
            "ok": False,
            "erro": "Não autorizado.",
        }), 401

    discord_id = str(
        discord_id
        or ""
    ).strip()
    if not discord_id.isdigit():
        return jsonify({
            "ok": False,
            "erro": "Discord ID inválido.",
        }), 400

    dados = carregar_roblox_vinculos()
    vinculo = dados["vinculos"].get(
        discord_id
    )
    return jsonify({
        "ok": True,
        "vinculado": bool(vinculo),
        "vinculo": vinculo,
    })


@app.route(
    "/api/roblox/desvincular",
    methods=["POST"],
)
def api_roblox_desvincular():
    payload = request.get_json(silent=True) or {}
    if not _autorizado_roblox(payload):
        return jsonify({
            "ok": False,
            "erro": "Não autorizado.",
        }), 401

    discord_id = str(
        payload.get("discord_id")
        or ""
    ).strip()
    if not discord_id.isdigit():
        return jsonify({
            "ok": False,
            "erro": "Discord ID inválido.",
        }), 400

    dados = carregar_roblox_vinculos()
    removido = dados["vinculos"].pop(
        discord_id,
        None,
    )
    for token, item in list(
        dados["pendentes"].items()
    ):
        if str(
            item.get("discord_id")
            or ""
        ) == discord_id:
            dados["pendentes"].pop(
                token,
                None,
            )

    salvar_roblox_vinculos(dados)

    if removido is None:
        return jsonify({
            "ok": False,
            "erro": "Nenhum vínculo encontrado.",
        }), 404

    return jsonify({
        "ok": True,
        "removido": removido,
    })

# =========================================================
# ROBLOX — PERFIL PARA O JOGO / CARGOS DISCORD
# =========================================================

# Servidor principal oficial da RESENHA MÁXIMA.
ROBLOX_GAME_MAIN_GUILD_ID = int(
    os.getenv("ROBLOX_GAME_MAIN_GUILD_ID", "1532613054703997012")
    or "1532613054703997012"
)

# Prioridade dos cargos visuais do servidor principal.
ROBLOX_GAME_MAIN_ROLES = [
    (1532613934883016704, "ADM_G"),
    (1540876101763600424, "CF_DPT"),
    (1533624911912767629, "ADM_DC"),
    (1540987356520251482, "MOD_DC"),
    (1532614113346453724, "MEM"),
]

# Prioridade dos cargos do servidor de Eventos.
ROBLOX_GAME_EVENT_ROLES = [
    (1541624067256352868, "CF_DPT_EVT"),
    (1541624066396651580, "DIR_EVT"),
    (1541624065054482472, "GER_EVT"),
    (1541624064081268878, "COO_EVT"),
    (1541624062910922843, "SUP_EVT"),
    (1541624062298685530, "AP_EVT"),
]


async def _roblox_game_fetch_member(guild_id, discord_id):
    guild_id = int(guild_id)
    discord_id = int(discord_id)

    guild = bot.get_guild(guild_id)

    if guild is None:
        print(
            f"[ROBLOX GAME] Servidor {guild_id} não está no cache do bot. "
            "Tentando buscar diretamente pela API do Discord..."
        )

        try:
            guild = await bot.fetch_guild(guild_id)
        except discord.NotFound:
            print(
                f"[ROBLOX GAME] Servidor {guild_id} não foi encontrado "
                "pela conta do bot."
            )
            return None, False
        except discord.Forbidden:
            print(
                f"[ROBLOX GAME] Bot sem acesso ao servidor {guild_id}."
            )
            return None, False
        except discord.HTTPException as erro:
            print(
                f"[ROBLOX GAME] Erro HTTP ao buscar servidor {guild_id}: {erro}"
            )
            return None, False

    # Primeiro tenta o cache, quando o objeto suporta get_member.
    get_member = getattr(guild, "get_member", None)
    if callable(get_member):
        membro = get_member(discord_id)
        if membro is not None:
            print(
                f"[ROBLOX GAME] Membro {discord_id} encontrado no cache "
                f"do servidor {guild_id}."
            )
            return membro, True

    # Depois consulta DIRETAMENTE a API do Discord.
    # Isso funciona mesmo quando o membro não está carregado no cache.
    try:
        membro = await guild.fetch_member(discord_id)

        print(
            f"[ROBLOX GAME] Membro {discord_id} confirmado pela API "
            f"no servidor {guild_id}."
        )

        return membro, True

    except discord.NotFound:
        print(
            f"[ROBLOX GAME] Membro {discord_id} NÃO está no servidor {guild_id}."
        )
        return None, True

    except discord.Forbidden:
        print(
            f"[ROBLOX GAME] Bot sem permissão para consultar membros "
            f"do servidor {guild_id}."
        )
        return None, False

    except discord.HTTPException as erro:
        print(
            f"[ROBLOX GAME] Erro HTTP ao consultar membro {discord_id} "
            f"no servidor {guild_id}: {erro}"
        )
        return None, False


async def _roblox_game_discord_profile(discord_id):
    cfg = carregar_servidores_config()

    # Usa SEMPRE o servidor principal oficial do jogo.
    # Não sobrescreve com GUILD_ID, porque essa variável pode apontar
    # para outro servidor usado por partes antigas do bot.
    principal_id = ROBLOX_GAME_MAIN_GUILD_ID

    eventos_id = int(
        cfg.get("eventos_guild_id")
        or EVENTOS_GUILD_ID
        or 1541541588122079283
    )

    membro_principal, principal_checked = await _roblox_game_fetch_member(
        principal_id,
        discord_id,
    )

    membro_eventos, eventos_checked = await _roblox_game_fetch_member(
        eventos_id,
        discord_id,
    )

    if not principal_checked:
        raise RuntimeError(
            "Não consegui confirmar a presença no servidor principal do Discord."
        )

    main_role = None
    second_role = None

    if membro_principal is not None:
        ids = {cargo.id for cargo in membro_principal.roles}
        for cargo_id, chave in ROBLOX_GAME_MAIN_ROLES:
            if cargo_id in ids:
                main_role = chave
                break

    if membro_eventos is not None:
        ids = {cargo.id for cargo in membro_eventos.roles}
        for cargo_id, chave in ROBLOX_GAME_EVENT_ROLES:
            if cargo_id in ids:
                second_role = chave
                break

    return {
        "in_main_server": membro_principal is not None,
        "in_event_server": membro_eventos is not None if eventos_checked else False,
        "main_role": main_role,
        "second_role": second_role,
    }


def _roblox_game_discord_profile_sync(discord_id):
    if not bot.is_ready() or BOT_LOOP is None:
        raise RuntimeError("O bot ainda não está conectado ao Discord.")

    futuro = asyncio.run_coroutine_threadsafe(
        _roblox_game_discord_profile(discord_id),
        BOT_LOOP,
    )
    return futuro.result(timeout=12)


@app.route("/api/roblox/game-profile/<roblox_id>")
def api_roblox_game_profile(roblox_id):
    roblox_id = str(roblox_id or "").strip()

    if not roblox_id.isdigit():
        return jsonify({
            "ok": False,
            "erro": "Roblox ID inválido.",
        }), 400

    dados = carregar_roblox_vinculos()

    discord_id = None
    vinculo = None

    for possivel_discord_id, item in dados.get("vinculos", {}).items():
        if str(item.get("roblox_id") or "") == roblox_id:
            discord_id = str(possivel_discord_id)
            vinculo = item
            break

    if not vinculo or not discord_id:
        return jsonify({
            "ok": True,
            "linked": False,
            "in_main_server": False,
            "in_event_server": False,
            "main_role": None,
            "second_role": None,
        })

    try:
        discord_profile = _roblox_game_discord_profile_sync(discord_id)
    except Exception as erro:
        print(
            "Erro ao consultar Discord para Roblox game-profile: "
            f"{type(erro).__name__}: {erro}"
        )
        return jsonify({
            "ok": False,
            "erro": "Não consegui confirmar o membro no Discord agora.",
        }), 503

    return jsonify({
        "ok": True,
        "linked": True,
        "in_main_server": bool(discord_profile["in_main_server"]),
        "in_event_server": bool(discord_profile["in_event_server"]),
        "main_role": discord_profile["main_role"],
        "second_role": discord_profile["second_role"],
    })


@app.route("/status")
def status():
    dados = carregar_menus()

    return {
        "site": "online",
        "bot_configurado": bool(TOKEN),
        "bot_conectado": (
            bot.is_ready()
            if TOKEN
            else False
        ),
        "canal_teste_id": CANAL_TESTE_ID,
        "menus_configurados": len(
            dados["menus"]
        ),
        "max_botoes": MAX_BOTOES,
        "views_persistentes": bool(
            getattr(
                bot,
                "_views_menus_registradas",
                False
            )
        ),
        "usuarios_painel": len(
            carregar_usuarios()["usuarios"]
        ),
    }, 200


if __name__ == "__main__":
    thread_bot = threading.Thread(
        target=iniciar_bot,
        daemon=True
    )
    thread_bot.start()

    porta = int(
        os.getenv(
            "PORT",
            "5000"
        )
    )

    app.run(
        host="0.0.0.0",
        port=porta,
        debug=False,
        use_reloader=False
    )
