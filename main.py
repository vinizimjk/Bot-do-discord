import asyncio
import os
import json
import threading
import secrets
from copy import deepcopy
from pathlib import Path
from functools import wraps

import discord
from discord.ext import commands
from flask import Flask, render_template, request, redirect, url_for, session, flash
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
GOOGLE_FORMS_URL = "https://forms.gle/h4kt2Cp7fduGG4Pc8"
EVENTOS_GUILD_ID = 1541541588122079283
CANAL_CANDIDATURA_PRINCIPAL_ID = 1541035337709649990
CARGO_ROBLOX_ID = 1540858217301549176
CARGO_MINECRAFT_ID = 1534006899371147304

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

class CandidaturaEventosSelect(discord.ui.Select):
    def __init__(self):
        super().__init__(
            placeholder="Selecione a prova que deseja fazer...",
            min_values=1,
            max_values=1,
            custom_id="eventos_candidatura_prova_select",
            options=[
                discord.SelectOption(
                    label="Verificar Roblox",
                    value="roblox",
                    emoji="🎮",
                    description="Para quem possui o cargo de Roblox."
                ),
                discord.SelectOption(
                    label="Verificar Minecraft",
                    value="minecraft",
                    emoji="⛏️",
                    description="Para quem possui o cargo de Minecraft."
                ),
                discord.SelectOption(
                    label="Verificar os dois",
                    value="ambos",
                    emoji="🎮",
                    description="Para quem possui os cargos de Roblox e Minecraft."
                ),
            ],
        )

    async def callback(self, interaction: discord.Interaction):
        membro = interaction.guild.get_member(interaction.user.id) if interaction.guild else None
        if membro is None:
            try:
                membro = await interaction.guild.fetch_member(interaction.user.id)
            except Exception:
                membro = None

        if membro is None:
            await interaction.response.send_message("❌ Não consegui identificar seu usuário.", ephemeral=True)
            return

        valor = self.values[0]
        tem_roblox = any(r.id == CARGO_ROBLOX_ID for r in membro.roles)
        tem_minecraft = any(r.id == CARGO_MINECRAFT_ID for r in membro.roles)

        if valor == "roblox" and not tem_roblox:
            await interaction.response.send_message(
                "❌ Você precisa ter o cargo de Roblox para fazer a verificação de Roblox.",
                ephemeral=True,
            )
            return
        if valor == "minecraft" and not tem_minecraft:
            await interaction.response.send_message(
                "❌ Você precisa ter o cargo de Minecraft para fazer a verificação de Minecraft.",
                ephemeral=True,
            )
            return
        if valor == "ambos" and not (tem_roblox and tem_minecraft):
            await interaction.response.send_message(
                "❌ Para escolher os dois, você precisa ter os cargos de Roblox e Minecraft.",
                ephemeral=True,
            )
            return

        if valor == "roblox":
            descricao = "Você selecionou **Verificar Roblox**."
        elif valor == "minecraft":
            descricao = "Você selecionou **Verificar Minecraft**."
        else:
            descricao = "Você selecionou **Verificar os dois**."

        await interaction.response.send_message(
            f"{descricao}\n\n🎓 **Faça sua prova aqui:** {GOOGLE_FORMS_URL}\n\n"
            "Depois de enviar o formulário, siga as instruções que o bot informar para concluir sua candidatura.",
            ephemeral=True,
        )


class CandidaturaEventosView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(CandidaturaEventosSelect())


async def _sincronizar_cargo_departamento_e_hierarquia(guild_eventos, roles, canais_por_chave):
    """Cria o cargo agregador do Departamento e sincroniza membros já reconhecidos no principal.

    A sincronização é conservadora: usa CARGO_EVENTOS_ID quando configurado e não remove
    cargos de hierarquia já existentes no servidor de Eventos.
    """
    cargo_departamento = await _buscar_ou_criar_role(
        guild_eventos,
        "Departamento de Eventos",
        permissions=discord.Permissions.none(),
        hoist=True,
    )

    principal = bot.get_guild(int(GUILD_ID)) if str(GUILD_ID or "").isdigit() else None
    cargo_origem = None
    if principal is not None and CARGO_EVENTOS_ID:
        cargo_origem = principal.get_role(CARGO_EVENTOS_ID)

    if cargo_origem is not None:
        ids_departamento = {m.id for m in cargo_origem.members if not m.bot}
        for membro_id in ids_departamento:
            membro_eventos = guild_eventos.get_member(membro_id)
            if membro_eventos is None:
                try:
                    membro_eventos = await guild_eventos.fetch_member(membro_id)
                except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                    continue
            adicionar = []
            if cargo_departamento not in membro_eventos.roles:
                adicionar.append(cargo_departamento)
            # Quem já pertence ao Departamento e ainda não tem nível definido começa como Aprendiz.
            if not any(role in membro_eventos.roles for role in roles.values()):
                adicionar.append(roles["aprendiz"])
            if adicionar:
                try:
                    await membro_eventos.add_roles(
                        *adicionar,
                        reason="Sincronização com o servidor principal RESENHA MÁXIMA",
                    )
                except (discord.Forbidden, discord.HTTPException):
                    pass

    return cargo_departamento


async def configurar_servidor_eventos(guild_id):
    guild = bot.get_guild(int(guild_id))
    if guild is None:
        raise RuntimeError("O bot não está mais conectado a esse servidor.")

    principal = str(GUILD_ID or "")
    if str(guild.id) == principal:
        raise RuntimeError("O servidor principal não pode ser usado como Departamento de Eventos.")

    # Cargos em ordem do menor para o maior. O Chef fica acima de todos.
    roles = {}
    for nome, chave in reversed(CARGOS_EVENTOS):
        roles[chave] = await _buscar_ou_criar_role(
            guild,
            nome,
            permissions=_permissoes_eventos(chave),
            hoist=chave not in {"intruso"},
        )

    # Conta de teste: cargo próprio e nenhum cargo de hierarquia.
    try:
        membro_teste = guild.get_member(CONTA_TESTE_EVENTOS_ID)
        if membro_teste is None:
            membro_teste = await guild.fetch_member(CONTA_TESTE_EVENTOS_ID)
        if membro_teste is not None:
            cargo_teste = guild.get_role(CARGO_TESTE_EVENTOS_ID)
            if cargo_teste is None:
                cargo_teste = await guild.create_role(
                    name="Conta de Teste",
                    permissions=discord.Permissions.none(),
                    reason="Conta de teste da Resenha Máxima",
                )
            remover = [
                r for r in membro_teste.roles
                if r.id != guild.default_role.id and r.id != cargo_teste.id
                and r.id in {role.id for role in roles.values()}
            ]
            if remover:
                await membro_teste.remove_roles(*remover, reason="Modo teste do Departamento de Eventos")
            if cargo_teste not in membro_teste.roles:
                await membro_teste.add_roles(cargo_teste, reason="Modo teste do Departamento de Eventos")
    except (discord.NotFound, discord.Forbidden, discord.HTTPException):
        pass

    # Permissões base por categoria/canal. Sempre inclui histórico para quem pode ver.
    everyone = guild.default_role
    aprendiz = roles["aprendiz"]
    supervisor = roles["supervisor"]
    coordenador = roles["coordenador"]
    gerente = roles["gerente"]
    diretor = roles["diretor"]
    chef = roles["chef"]
    intruso = roles["intruso"]

    overwrites_geral = {
        everyone: _overwrite(deny=("view_channel",)),
        intruso: _overwrite(deny=("view_channel",)),
        aprendiz: _overwrite(allow=("view_channel", "read_message_history", "send_messages")),
        supervisor: _overwrite(allow=("view_channel", "read_message_history", "send_messages")),
        coordenador: _overwrite(allow=("view_channel", "read_message_history", "send_messages")),
        gerente: _overwrite(allow=("view_channel", "read_message_history", "send_messages")),
        diretor: _overwrite(allow=("view_channel", "read_message_history", "send_messages")),
        chef: _overwrite(allow=("view_channel", "read_message_history", "send_messages")),
    }

    overwrites_anuncios = dict(overwrites_geral)
    for role in (aprendiz, supervisor, coordenador, gerente):
        overwrites_anuncios[role] = _overwrite(allow=("view_channel", "read_message_history"), deny=("send_messages",))
    overwrites_anuncios[diretor] = _overwrite(allow=("view_channel", "read_message_history", "send_messages"))
    overwrites_anuncios[chef] = _overwrite(allow=("view_channel", "read_message_history", "send_messages"))

    overwrites_comandos = {
        everyone: _overwrite(deny=("view_channel",)),
        intruso: _overwrite(deny=("view_channel",)),
        aprendiz: _overwrite(deny=("view_channel",)),
        supervisor: _overwrite(deny=("view_channel",)),
        coordenador: _overwrite(deny=("view_channel",)),
        gerente: _overwrite(allow=("view_channel", "read_message_history", "send_messages")),
        diretor: _overwrite(allow=("view_channel", "read_message_history", "send_messages")),
        chef: _overwrite(allow=("view_channel", "read_message_history", "send_messages")),
    }

    overwrites_gerentes = {
        everyone: _overwrite(deny=("view_channel",)),
        intruso: _overwrite(deny=("view_channel",)),
        aprendiz: _overwrite(deny=("view_channel",)),
        supervisor: _overwrite(deny=("view_channel",)),
        coordenador: _overwrite(deny=("view_channel",)),
        gerente: _overwrite(allow=("view_channel", "read_message_history", "send_messages")),
        diretor: _overwrite(allow=("view_channel", "read_message_history", "send_messages")),
        chef: _overwrite(allow=("view_channel", "read_message_history", "send_messages")),
    }

    overwrites_diretoria = {
        everyone: _overwrite(deny=("view_channel",)),
        intruso: _overwrite(deny=("view_channel",)),
        aprendiz: _overwrite(deny=("view_channel",)),
        supervisor: _overwrite(deny=("view_channel",)),
        coordenador: _overwrite(deny=("view_channel",)),
        gerente: _overwrite(deny=("view_channel",)),
        diretor: _overwrite(allow=("view_channel", "read_message_history", "send_messages")),
        chef: _overwrite(allow=("view_channel", "read_message_history", "send_messages")),
    }

    overwrites_voz = {
        everyone: _overwrite(deny=("view_channel",)),
        intruso: _overwrite(deny=("view_channel",)),
        aprendiz: _overwrite(allow=("view_channel", "connect", "speak")),
        supervisor: _overwrite(allow=("view_channel", "connect", "speak")),
        coordenador: _overwrite(allow=("view_channel", "connect", "speak")),
        gerente: _overwrite(allow=("view_channel", "connect", "speak")),
        diretor: _overwrite(allow=("view_channel", "connect", "speak")),
        chef: _overwrite(allow=("view_channel", "connect", "speak")),
    }

    categorias = {}
    for nome_categoria, _ in CANAIS_EVENTOS:
        categorias[nome_categoria] = await _obter_categoria(guild, nome_categoria)

    criados = []
    canais_por_chave = {}
    for nome_categoria, canais in CANAIS_EVENTOS:
        categoria = categorias[nome_categoria]
        for nome_canal, chave in canais:
            canal = None
            if chave == "call":
                canal = await _obter_canal_voz(categoria, nome_canal)
            else:
                canal = await _obter_canal_texto(categoria, nome_canal)
            if chave in {"anuncios"}:
                overwrites = overwrites_anuncios
            elif chave in {"comandos"}:
                overwrites = overwrites_comandos
            elif chave in {"gerentes"}:
                overwrites = overwrites_gerentes
            elif chave in {"diretoria"}:
                overwrites = overwrites_diretoria
            elif chave == "call":
                overwrites = overwrites_voz
            elif chave == "candidatura":
                overwrites = dict(overwrites_geral)
                overwrites[intruso] = _overwrite(allow=("view_channel", "read_message_history"), deny=("send_messages",))
            else:
                overwrites = overwrites_geral
            try:
                await canal.edit(
                    overwrites=overwrites,
                    reason="Configuração automática do Departamento de Eventos"
                )
            except discord.HTTPException:
                pass
            criados.append(canal.name)
            canais_por_chave[chave] = canal

    cargo_departamento = await _sincronizar_cargo_departamento_e_hierarquia(
        guild,
        roles,
        canais_por_chave,
    )

    try:
        candidatura = canais_por_chave.get("candidatura")
        if candidatura is None:
            candidatura = discord.utils.get(
                guild.text_channels, name="📜・𝑪𝒂𝒏𝒅𝒊𝒅𝒂𝒕𝒖𝒓𝒂"
            )
        if candidatura:
            # Garante que a view sobreviva a reinícios/deploys.
            try:
                bot.add_view(CandidaturaEventosView())
            except Exception:
                pass

            embed = discord.Embed(
                title="📜・𝑪𝒂𝒏𝒅𝒊𝒅𝒂𝒕𝒖𝒓𝒂",
                description=(
                    "Quer entrar para o **Departamento de Eventos**?\n\n"
                    "Selecione abaixo a verificação que deseja fazer.\n\n"
                    "🎮 **Verificar Roblox** — disponível para quem possui o cargo de Roblox.\n"
                    "⛏️ **Verificar Minecraft** — disponível para quem possui o cargo de Minecraft.\n"
                    "🎮 **Verificar os dois** — disponível para quem possui os dois cargos.\n\n"
                    "Depois de selecionar, o bot enviará o link da prova.\n"
                    "A avaliação/ticket continua sendo processada no servidor principal."
                ),
                color=discord.Color.blurple(),
            )
            embed.set_footer(text="Departamento de Eventos • Candidatura")

            ultima = None
            async for msg in candidatura.history(limit=50):
                if msg.author.id == bot.user.id and msg.components:
                    ultima = msg
                    break

            view = CandidaturaEventosView()
            if ultima:
                await ultima.edit(embed=embed, view=view, content=None)
            else:
                await candidatura.send(embed=embed, view=view)
    except (discord.Forbidden, discord.HTTPException) as erro:
        print(f"Erro ao publicar menu de candidatura de Eventos: {erro}")

    dados = carregar_servidores_config()
    dados["eventos_guild_id"] = str(guild.id)
    dados["eventos_guild_nome"] = guild.name
    dados["roles"] = {chave: str(role.id) for chave, role in roles.items()}
    dados["role_departamento_id"] = str(cargo_departamento.id)
    dados["role_departamento_nome"] = cargo_departamento.name
    dados["role_names"] = {chave: role.name for chave, role in roles.items()}
    dados["configurado_em"] = __import__("datetime").datetime.now().isoformat()
    salvar_servidores_config(dados)

    # Atualiza também o canal de candidatura do servidor principal para apontar
    # diretamente para este canal do servidor de Eventos.
    try:
        if candidatura is not None:
            canal_principal = bot.get_channel(CANAL_CANDIDATURA_PRINCIPAL_ID)
            if canal_principal is None:
                canal_principal = await bot.fetch_channel(CANAL_CANDIDATURA_PRINCIPAL_ID)
            destino = f"https://discord.com/channels/{guild.id}/{candidatura.id}"
            embed_principal = discord.Embed(
                title="📝 Candidatura de Eventos",
                description=(
                    "As candidaturas do **Departamento de Eventos** agora são feitas no servidor de Eventos.\n\n"
                    "Clique no botão abaixo para ir ao canal oficial de candidatura."
                ),
                color=discord.Color.blurple(),
            )
            view_principal = discord.ui.View(timeout=None)
            view_principal.add_item(discord.ui.Button(
                label="Ir para Candidatura",
                emoji="📜",
                style=discord.ButtonStyle.link,
                url=destino,
            ))
            mensagem_existente = None
            async for msg in canal_principal.history(limit=50):
                if msg.author.id == bot.user.id and msg.embeds and msg.embeds[0].title == "📝 Candidatura de Eventos":
                    mensagem_existente = msg
                    break
            if mensagem_existente:
                await mensagem_existente.edit(content=None, embed=embed_principal, view=view_principal)
            else:
                await canal_principal.send(embed=embed_principal, view=view_principal)
    except Exception as erro:
        print(f"Erro ao redirecionar candidatura no servidor principal: {erro}")

    return {
        "guild_id": str(guild.id),
        "guild_name": guild.name,
        "roles": dados["role_names"],
        "channels": criados,
    }


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

    # Garante em TODO deploy/reinício que o servidor de Eventos esteja configurado,
    # que o canal 📜・𝑪𝒂𝒏𝒅𝒊𝒅𝒂𝒕𝒖𝒓𝒂 exista e que o canal principal esteja redirecionado.
    if not getattr(bot, "_eventos_auto_config_feito", False):
        try:
            await configurar_servidor_eventos(EVENTOS_GUILD_ID)
            bot._eventos_auto_config_feito = True
            print(f"Servidor de Eventos {EVENTOS_GUILD_ID} configurado automaticamente.")
        except Exception as erro:
            print(f"Erro na configuração automática do servidor de Eventos: {erro}")

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



# =========================================================
# SITE / PAINEL WEB
# =========================================================

app = Flask(__name__)
app.secret_key = os.getenv("PANEL_SECRET_KEY") or secrets.token_hex(32)

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
