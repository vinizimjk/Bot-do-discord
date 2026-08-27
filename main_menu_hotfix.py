import asyncio
import os
import json
import threading
import secrets
from copy import deepcopy
from pathlib import Path
from functools import wraps
from datetime import datetime, timezone

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
MAX_BOTOES = 25

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
intents.members = True
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
                "style": discord.ButtonStyle.primary,
                "row": indice // 5
            }

            # Emoji inválido não pode derrubar o /menu inteiro.
            if emoji:
                try:
                    argumentos["emoji"] = discord.PartialEmoji.from_str(
                        str(emoji)
                    )
                except Exception as erro:
                    print(
                        "Emoji inválido ignorado | "
                        f"canal={self.canal_id} "
                        f"indice={indice} "
                        f"emoji={emoji!r} "
                        f"erro={erro!r}",
                        flush=True
                    )

            if persistente:
                argumentos["custom_id"] = (
                    f"rm_menu:{self.canal_id}:{indice}"
                )

            try:
                botao = discord.ui.Button(**argumentos)
            except Exception as erro:
                print(
                    "Falha ao criar botão com emoji; "
                    "tentando sem emoji | "
                    f"canal={self.canal_id} "
                    f"indice={indice} "
                    f"erro={erro!r}",
                    flush=True
                )
                argumentos.pop("emoji", None)
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

    try:
        embed = discord.Embed(
            title=config.get("titulo") or "Menu",
            description=config.get("descricao") or "Escolha uma opção.",
            color=cor_da_config(config)
        )

        view = MenuView(
            config,
            str(interaction.channel_id),
            persistente=True
        )

        await interaction.response.send_message(
            embed=embed,
            view=view
        )

    except Exception as erro:
        print(
            "Erro real do /menu | "
            f"{type(erro).__name__}: {erro!r}",
            flush=True
        )

        mensagem = (
            "❌ Não consegui montar esse menu. "
            f"Erro: `{type(erro).__name__}`"
        )

        if interaction.response.is_done():
            await interaction.followup.send(
                mensagem,
                ephemeral=True
            )
        else:
            await interaction.response.send_message(
                mensagem,
                ephemeral=True
            )



def agora_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def carregar_entradas():
    if not ENTRADAS_FILE.exists():
        return []

    try:
        with ENTRADAS_FILE.open("r", encoding="utf-8") as arquivo:
            dados = json.load(arquivo)
    except (OSError, json.JSONDecodeError):
        return []

    return dados if isinstance(dados, list) else []


def salvar_entradas(dados):
    with _entradas_lock:
        temporario = ENTRADAS_FILE.with_suffix(".tmp")
        with temporario.open("w", encoding="utf-8") as arquivo:
            json.dump(dados[-5000:], arquivo, ensure_ascii=False, indent=2)
        temporario.replace(ENTRADAS_FILE)


def registrar_entrada(registro):
    dados = carregar_entradas()
    dados.append(registro)
    salvar_entradas(dados)


async def atualizar_cache_convites(guild):
    try:
        convites = await guild.invites()
        _invites_cache[guild.id] = {
            convite.code: convite.uses or 0
            for convite in convites
        }
    except (discord.Forbidden, discord.HTTPException):
        _invites_cache.setdefault(guild.id, {})

    try:
        vanity = await guild.vanity_invite()
        if vanity:
            _vanity_cache[guild.id] = {
                "code": vanity.code,
                "uses": vanity.uses or 0,
            }
    except (discord.Forbidden, discord.HTTPException):
        _vanity_cache.pop(guild.id, None)


async def descobrir_origem_entrada(guild):
    anterior = _invites_cache.get(guild.id, {})

    try:
        atuais = await guild.invites()
    except (discord.Forbidden, discord.HTTPException):
        atuais = []

    usado = None
    for convite in atuais:
        usos_antes = anterior.get(convite.code, 0)
        usos_agora = convite.uses or 0
        if usos_agora > usos_antes:
            usado = convite
            break

    _invites_cache[guild.id] = {
        convite.code: convite.uses or 0
        for convite in atuais
    }

    if usado is not None:
        convidador = usado.inviter
        return {
            "origem": "convite",
            "convite_codigo": usado.code,
            "convidador_id": str(convidador.id) if convidador else "",
            "convidador_nome": str(convidador) if convidador else "Desconhecido",
            "detalhe": f"Convite {usado.code}",
        }

    # Vanity URL é detectável separadamente quando o servidor possui uma.
    anterior_vanity = _vanity_cache.get(guild.id)
    try:
        vanity = await guild.vanity_invite()
    except (discord.Forbidden, discord.HTTPException):
        vanity = None

    if vanity:
        usos_agora = vanity.uses or 0
        usos_antes = (anterior_vanity or {}).get("uses", usos_agora)
        _vanity_cache[guild.id] = {"code": vanity.code, "uses": usos_agora}
        if usos_agora > usos_antes:
            return {
                "origem": "vanity",
                "convite_codigo": vanity.code,
                "convidador_id": "",
                "convidador_nome": "",
                "detalhe": f"Link personalizado /{vanity.code}",
            }

    return {
        "origem": "desconhecida",
        "convite_codigo": "",
        "convidador_id": "",
        "convidador_nome": "",
        "detalhe": "O Discord não informou qual origem foi usada.",
    }


@bot.event
async def on_member_join(member):
    origem = await descobrir_origem_entrada(member.guild)

    registrar_entrada({
        "data": agora_iso(),
        "membro_id": str(member.id),
        "membro_nome": str(member),
        "membro_exibicao": member.display_name,
        **origem,
    })


@bot.event
async def on_invite_create(invite):
    if invite.guild:
        await atualizar_cache_convites(invite.guild)


@bot.event
async def on_invite_delete(invite):
    if invite.guild:
        await atualizar_cache_convites(invite.guild)


@bot.event
async def on_ready():
    global BOT_LOOP
    BOT_LOOP = asyncio.get_running_loop()

    for guild in bot.guilds:
        await atualizar_cache_convites(guild)

    registrar_views_persistentes()

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
ENTRADAS_FILE = DATA_DIR / "entradas.json"

# Cargos reconhecidos pelo painel
CARGO_BANIMENTOS_ID = 1536734408277491863
CARGO_ENTRADA_ID = 1536894123657658468
CARGO_PATENTE_MINECRAFT_ID = 1537595632817012846

_entradas_lock = threading.Lock()
_invites_cache = {}
_vanity_cache = {}

# Equipe de Desenvolvimento já definida acima:
# CARGO_DESENVOLVIMENTO_ID = 1533625836874498181
#
# Para o Departamento de Eventos e canal de Eventos, você pode
# definir os IDs diretamente na Railway. Se não definir, o painel
# tenta localizar pelo nome no Discord.
CARGO_EVENTOS_ID = int(os.getenv("CARGO_EVENTOS_ID", "0") or "0")
CANAL_EVENTOS_ID = int(os.getenv("CANAL_EVENTOS_ID", "0") or "0")

_users_lock = threading.Lock()

MODELOS_MENSAGENS = [
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

    if CARGO_BANIMENTOS_ID in ids_cargos:
        return {
            "ok": True,
            "nivel": "banimentos",
            "nome": str(membro),
            "erro": None
        }

    if CARGO_ENTRADA_ID in ids_cargos:
        return {
            "ok": True,
            "nivel": "entrada",
            "nome": str(membro),
            "erro": None
        }

    if CARGO_PATENTE_MINECRAFT_ID in ids_cargos:
        return {
            "ok": True,
            "nivel": "minecraft",
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
            "O usuário não possui nenhum dos cargos autorizados para o painel."
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



def resumo_entradas():
    registros = list(reversed(carregar_entradas()[-500:]))
    ranking = {}

    for item in registros:
        if item.get("origem") != "convite":
            continue
        convidador_id = str(item.get("convidador_id") or "")
        if not convidador_id:
            continue
        atual = ranking.setdefault(convidador_id, {
            "id": convidador_id,
            "nome": item.get("convidador_nome") or "Desconhecido",
            "total": 0,
        })
        atual["total"] += 1

    top = sorted(
        ranking.values(),
        key=lambda item: (-item["total"], item["nome"].casefold())
    )[:10]

    return registros, top


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
        "entradas",
    }

    if aba not in abas_validas:
        aba = "menus"

    if aba == "entradas" and nivel_sessao() not in {"full", "entrada"}:
        aba = "menus"

    if aba == "central" and nivel_sessao() not in {"full", "banimentos", "entrada", "minecraft"}:
        aba = "menus"

    if aba == "modelos" and nivel_sessao() not in {"full", "banimentos", "entrada", "minecraft"}:
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

    logs_admin = (
        carregar_logs_administrativos()
        if aba == "central"
        and nivel_sessao() in {"full", "banimentos", "entrada", "minecraft"}
        else []
    )

    entradas, ranking_convites = resumo_entradas() if aba == "entradas" else ([], [])

    return {
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
        "pode_ver_modelos": nivel_sessao() in {"full", "banimentos", "entrada", "minecraft"},
        "pode_ver_central": nivel_sessao() in {"full", "banimentos", "entrada", "minecraft"},
        "pode_ver_entradas": nivel_sessao() in {"full", "entrada"},
        "usuario_logado": session.get(
            "usuario",
            "Usuário"
        ),
        "discord_nome": session.get(
            "discord_nome"
        ),
        "modelos_mensagens": MODELOS_MENSAGENS,
        "logs_admin": logs_admin,
        "entradas": entradas,
        "ranking_convites": ranking_convites,
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
