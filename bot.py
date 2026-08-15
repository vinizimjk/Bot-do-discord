import asyncio
import json
import os
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands, tasks
from dotenv import load_dotenv
from mcstatus import JavaServer, BedrockServer


# ==========================================================
# CONFIGURAÇÕES PRINCIPAIS
# ==========================================================

DONO_ID = 1455937306400653344
CANAL_APROVACAO_ID = 1536073451633254420
PAINEL_MENU_URL = "https://painel-menu-bot-production.up.railway.app"

CARGO_MINECRAFT_ID = 1534006899371147304
CANAL_STATUS_MINECRAFT_ID = 1538109074779144253
CANAL_NICKNAMES_MINECRAFT_ID = 1534423515183448155
CARGO_DESENVOLVIMENTO_ID = 1533625836874498181
MINECRAFT_HOST = "resenha-DpsX.aternos.me"
MINECRAFT_PORTA = 20710
MINECRAFT_EDICAO = "bedrock"  # servidor atual é Bedrock

CASTIGO_DIAS = 28

INTERVALO_MINECRAFT_SEGUNDOS = 60
FALHAS_OFFLINE_NECESSARIAS = 3
SUCESSOS_ONLINE_NECESSARIOS = 2

AVISOS_NICK_HORAS = (12, 24, 36, 48)
INTERVALO_NICKS_MINUTOS = 10
TEMPO_REMOCAO_NICK_APOS_SAIDA_HORAS = 48

NICK_MIN_CARACTERES = 3
NICK_MAX_CARACTERES = 32

# Nicknames que já tinham sido informados antes da automação.
# O bot importa esses cadastros uma única vez no banco e avisa por DM.
NICKS_PRE_CADASTRADOS = {
    1455937306400653344: "vinizim_dajk",
    1089629818628349962: "Darck1777",
}


# ==========================================================
# PASTAS / ARQUIVOS
# ==========================================================

PASTA_BOT = Path(__file__).parent
PASTA_VOLUME = Path("/data")

if PASTA_VOLUME.exists():
    PASTA_DADOS = PASTA_VOLUME
else:
    PASTA_DADOS = PASTA_BOT

ARQUIVO_ENV = PASTA_BOT / ".env"
ARQUIVO_CONFIG = PASTA_DADOS / "config.json"

BANCO_NOVO = PASTA_DADOS / "bot.db"
BANCO_ANTIGO = PASTA_DADOS / "enquetes.db"

if BANCO_NOVO.exists():
    ARQUIVO_BANCO = BANCO_NOVO
elif BANCO_ANTIGO.exists():
    ARQUIVO_BANCO = BANCO_ANTIGO
else:
    ARQUIVO_BANCO = BANCO_NOVO

load_dotenv(dotenv_path=ARQUIVO_ENV)




# ==========================================================
# BANCO
# ==========================================================

def conectar_banco():
    banco = sqlite3.connect(
        ARQUIVO_BANCO,
        timeout=10
    )

    banco.row_factory = sqlite3.Row

    return banco


def coluna_existe(cursor, tabela, coluna):
    cursor.execute(
        f"PRAGMA table_info({tabela})"
    )

    return any(
        linha["name"] == coluna
        for linha in cursor.fetchall()
    )


def criar_banco():
    with conectar_banco() as banco:
        cursor = banco.cursor()

        # --------------------------------------------------
        # ENQUETES
        # --------------------------------------------------

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS enquetes_v2 (
                id TEXT PRIMARY KEY,
                pergunta TEXT NOT NULL,
                opcao1 TEXT NOT NULL,
                opcao2 TEXT NOT NULL,
                opcao3 TEXT,
                canal_id INTEGER,
                mensagem_id INTEGER,
                ativa INTEGER DEFAULT 1
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS votos_v2 (
                enquete_id TEXT NOT NULL,
                usuario_id INTEGER NOT NULL,
                opcao INTEGER NOT NULL,

                PRIMARY KEY (
                    enquete_id,
                    usuario_id
                )
            )
        """)

        # --------------------------------------------------
        # BAN / HACKBAN
        # --------------------------------------------------

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS solicitacoes_ban (
                id TEXT PRIMARY KEY,
                guild_id INTEGER NOT NULL,
                usuario_id INTEGER NOT NULL,
                solicitante_id INTEGER NOT NULL,
                motivo TEXT NOT NULL,
                data_solicitacao TEXT NOT NULL,
                canal_id INTEGER,
                mensagem_id INTEGER,
                status TEXT DEFAULT 'pendente',
                decisor_id INTEGER,
                data_decisao TEXT
            )
        """)

        novas_colunas = {
            "tipo": "TEXT DEFAULT 'ban'",
            "usuario_nome": "TEXT",
            "castigo_aplicado": "INTEGER DEFAULT 0",
            "modo_motivo": "TEXT DEFAULT 'escrito'",
        }

        for coluna, definicao in novas_colunas.items():
            if not coluna_existe(
                cursor,
                "solicitacoes_ban",
                coluna
            ):
                cursor.execute(
                    "ALTER TABLE solicitacoes_ban "
                    f"ADD COLUMN {coluna} {definicao}"
                )

        # Solicitação que ficou presa no meio de uma decisão.
        cursor.execute("""
            UPDATE solicitacoes_ban
            SET status = 'pendente'
            WHERE status = 'processando'
        """)

        # Remove o modo antigo "call" de pedidos ainda pendentes.
        cursor.execute("""
            UPDATE solicitacoes_ban
            SET
                modo_motivo = 'informado',
                motivo = 'Motivo já informado.'
            WHERE
                status = 'pendente'
                AND modo_motivo = 'call'
        """)

        # --------------------------------------------------
        # ESTADO DO BOT
        # --------------------------------------------------

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS estado_bot (
                chave TEXT PRIMARY KEY,
                valor TEXT
            )
        """)

        # --------------------------------------------------
        # PREFERÊNCIAS DE NOTIFICAÇÃO DO MINECRAFT
        # --------------------------------------------------

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS minecraft_notificacoes (
                usuario_id INTEGER PRIMARY KEY,
                receber INTEGER NOT NULL DEFAULT 1
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS minecraft_nicknames (
                guild_id INTEGER NOT NULL,
                usuario_id INTEGER NOT NULL,
                nickname TEXT,
                status TEXT NOT NULL DEFAULT 'pendente',
                pendente_desde TEXT,
                avisos_enviados INTEGER NOT NULL DEFAULT 0,
                solicitacao_enviada INTEGER NOT NULL DEFAULT 0,
                castigo_aplicado INTEGER NOT NULL DEFAULT 0,
                mensagem_id INTEGER,
                saiu_em TEXT,
                atualizado_em TEXT,
                PRIMARY KEY (guild_id, usuario_id)
            )
        """)

        banco.commit()


criar_banco()


# ==========================================================
# ESTADOS DO BOT
# ==========================================================

def obter_estado(chave):
    with conectar_banco() as banco:
        linha = banco.execute(
            """
            SELECT valor
            FROM estado_bot
            WHERE chave = ?
            """,
            (chave,)
        ).fetchone()

    if linha is None:
        return None

    return linha["valor"]


def salvar_estado(chave, valor):
    with conectar_banco() as banco:
        banco.execute(
            """
            INSERT INTO estado_bot (
                chave,
                valor
            )
            VALUES (?, ?)

            ON CONFLICT(chave)
            DO UPDATE SET
                valor = excluded.valor
            """,
            (
                chave,
                str(valor)
            )
        )

        banco.commit()


# ==========================================================
# PREFERÊNCIAS DE NOTIFICAÇÃO DO MINECRAFT
# ==========================================================

def salvar_preferencia_minecraft(usuario_id, receber):
    with conectar_banco() as banco:
        banco.execute(
            """
            INSERT INTO minecraft_notificacoes (
                usuario_id,
                receber
            )
            VALUES (?, ?)

            ON CONFLICT(usuario_id)
            DO UPDATE SET
                receber = excluded.receber
            """,
            (
                usuario_id,
                int(bool(receber))
            )
        )
        banco.commit()


def deve_receber_minecraft(usuario_id):
    """Sem preferência salva = recebe por padrão, preservando o comportamento atual."""
    with conectar_banco() as banco:
        linha = banco.execute(
            """
            SELECT receber
            FROM minecraft_notificacoes
            WHERE usuario_id = ?
            """,
            (usuario_id,)
        ).fetchone()

    if linha is None:
        return True

    return bool(linha["receber"])


# ==========================================================
# NICKNAMES DO MINECRAFT - BANCO
# ==========================================================

def buscar_cadastro_nick(guild_id, usuario_id):
    with conectar_banco() as banco:
        return banco.execute(
            "SELECT * FROM minecraft_nicknames WHERE guild_id = ? AND usuario_id = ?",
            (guild_id, usuario_id)
        ).fetchone()


def buscar_pendencias_nick_usuario(usuario_id):
    with conectar_banco() as banco:
        return banco.execute(
            "SELECT * FROM minecraft_nicknames WHERE usuario_id = ? AND status = 'pendente'",
            (usuario_id,)
        ).fetchall()


def iniciar_pendencia_nick(guild_id, usuario_id):
    agora = datetime.now(timezone.utc).isoformat()
    cadastro = buscar_cadastro_nick(guild_id, usuario_id)
    with conectar_banco() as banco:
        if cadastro is None:
            banco.execute(
                """INSERT INTO minecraft_nicknames
                (guild_id, usuario_id, status, pendente_desde, atualizado_em)
                VALUES (?, ?, 'pendente', ?, ?)""",
                (guild_id, usuario_id, agora, agora)
            )
        elif not cadastro['nickname']:
            banco.execute(
                """UPDATE minecraft_nicknames
                SET status='pendente', pendente_desde=COALESCE(pendente_desde, ?),
                    saiu_em=NULL, atualizado_em=?
                WHERE guild_id=? AND usuario_id=?""",
                (agora, agora, guild_id, usuario_id)
            )
        else:
            banco.execute(
                """UPDATE minecraft_nicknames
                SET status='ativo', saiu_em=NULL, atualizado_em=?
                WHERE guild_id=? AND usuario_id=?""",
                (agora, guild_id, usuario_id)
            )
        banco.commit()


def atualizar_cadastro_nick(guild_id, usuario_id, **campos):
    if not campos:
        return
    campos['atualizado_em'] = datetime.now(timezone.utc).isoformat()
    partes = ', '.join(f"{chave} = ?" for chave in campos)
    valores = list(campos.values()) + [guild_id, usuario_id]
    with conectar_banco() as banco:
        banco.execute(
            f"UPDATE minecraft_nicknames SET {partes} WHERE guild_id = ? AND usuario_id = ?",
            valores
        )
        banco.commit()


def listar_nicks_por_status(status):
    with conectar_banco() as banco:
        return banco.execute(
            "SELECT * FROM minecraft_nicknames WHERE status = ?",
            (status,)
        ).fetchall()


def excluir_cadastro_nick(guild_id, usuario_id):
    with conectar_banco() as banco:
        banco.execute(
            "DELETE FROM minecraft_nicknames WHERE guild_id = ? AND usuario_id = ?",
            (guild_id, usuario_id)
        )
        banco.commit()


def tem_ban_pendente_com_castigo(guild_id, usuario_id):
    with conectar_banco() as banco:
        linha = banco.execute(
            """SELECT 1 FROM solicitacoes_ban
            WHERE guild_id=? AND usuario_id=? AND status='pendente' AND castigo_aplicado=1
            LIMIT 1""",
            (guild_id, usuario_id)
        ).fetchone()
    return linha is not None


# ==========================================================
# ENQUETES - BANCO
# ==========================================================

def salvar_enquete(
    enquete_id,
    pergunta,
    opcoes
):
    opcao3 = (
        opcoes[2]
        if len(opcoes) >= 3
        else None
    )

    with conectar_banco() as banco:
        banco.execute(
            """
            INSERT INTO enquetes_v2 (
                id,
                pergunta,
                opcao1,
                opcao2,
                opcao3,
                ativa
            )
            VALUES (?, ?, ?, ?, ?, 1)
            """,
            (
                enquete_id,
                pergunta,
                opcoes[0],
                opcoes[1],
                opcao3
            )
        )

        banco.commit()


def atualizar_mensagem_enquete(
    enquete_id,
    canal_id,
    mensagem_id
):
    with conectar_banco() as banco:
        banco.execute(
            """
            UPDATE enquetes_v2
            SET
                canal_id = ?,
                mensagem_id = ?
            WHERE id = ?
            """,
            (
                canal_id,
                mensagem_id,
                enquete_id
            )
        )

        banco.commit()


def registrar_voto(
    enquete_id,
    usuario_id,
    opcao
):
    with conectar_banco() as banco:
        banco.execute(
            """
            INSERT INTO votos_v2 (
                enquete_id,
                usuario_id,
                opcao
            )
            VALUES (?, ?, ?)

            ON CONFLICT(
                enquete_id,
                usuario_id
            )
            DO UPDATE SET
                opcao = excluded.opcao
            """,
            (
                enquete_id,
                usuario_id,
                opcao
            )
        )

        banco.commit()


def remover_voto(
    enquete_id,
    usuario_id
):
    with conectar_banco() as banco:
        cursor = banco.execute(
            """
            DELETE FROM votos_v2
            WHERE
                enquete_id = ?
                AND usuario_id = ?
            """,
            (
                enquete_id,
                usuario_id
            )
        )

        banco.commit()

        return cursor.rowcount > 0


def contar_votos(
    enquete_id,
    quantidade_opcoes
):
    contagem = [0] * quantidade_opcoes

    with conectar_banco() as banco:
        resultados = banco.execute(
            """
            SELECT
                opcao,
                COUNT(*) AS quantidade
            FROM votos_v2
            WHERE enquete_id = ?
            GROUP BY opcao
            """,
            (enquete_id,)
        ).fetchall()

    for linha in resultados:
        opcao = linha["opcao"]

        if 0 <= opcao < quantidade_opcoes:
            contagem[opcao] = linha["quantidade"]

    return contagem


def buscar_votos(enquete_id):
    with conectar_banco() as banco:
        return banco.execute(
            """
            SELECT
                usuario_id,
                opcao
            FROM votos_v2
            WHERE enquete_id = ?
            ORDER BY
                opcao,
                usuario_id
            """,
            (enquete_id,)
        ).fetchall()


def buscar_enquetes_ativas():
    with conectar_banco() as banco:
        return banco.execute(
            """
            SELECT
                id,
                pergunta,
                opcao1,
                opcao2,
                opcao3,
                canal_id,
                mensagem_id
            FROM enquetes_v2
            WHERE
                ativa = 1
                AND mensagem_id IS NOT NULL
            """
        ).fetchall()


# ==========================================================
# ENQUETE - EMBED
# ==========================================================

def gerar_embed_enquete(
    enquete_id,
    pergunta,
    opcoes
):
    emojis = [
        "1️⃣",
        "2️⃣",
        "3️⃣"
    ]

    contagem = contar_votos(
        enquete_id,
        len(opcoes)
    )

    total = sum(contagem)

    embed = discord.Embed(
        title="📊 Enquete",
        description=f"## {pergunta}",
        color=discord.Color.blurple()
    )

    for indice, texto in enumerate(opcoes):
        votos = contagem[indice]

        porcentagem = (
            votos / total * 100
            if total
            else 0
        )

        embed.add_field(
            name=f"{emojis[indice]} {texto}",
            value=(
                f"**{votos} voto(s)** "
                f"— {porcentagem:.1f}%"
            ),
            inline=False
        )

    embed.set_footer(
        text=f"Total de votos: {total}"
    )

    return embed



# ==========================================================
# ENQUETE - VIEW
# ==========================================================

class EnqueteView(discord.ui.View):

    def __init__(
        self,
        enquete_id,
        pergunta,
        opcoes
    ):
        super().__init__(
            timeout=None
        )

        self.enquete_id = enquete_id
        self.pergunta = pergunta
        self.opcoes = opcoes

        emojis = [
            "1️⃣",
            "2️⃣",
            "3️⃣"
        ]

        for indice, opcao in enumerate(opcoes):
            botao = discord.ui.Button(
                label=opcao,
                emoji=emojis[indice],
                style=discord.ButtonStyle.primary,
                custom_id=f"voto_{enquete_id}_{indice}"
            )

            async def votar(
                interaction: discord.Interaction,
                indice_opcao=indice
            ):
                registrar_voto(
                    self.enquete_id,
                    interaction.user.id,
                    indice_opcao
                )

                embed = gerar_embed_enquete(
                    self.enquete_id,
                    self.pergunta,
                    self.opcoes
                )

                await interaction.response.edit_message(
                    embed=embed,
                    view=self
                )

                await interaction.followup.send(
                    "✅ Seu voto foi registrado.",
                    ephemeral=True
                )

            botao.callback = votar
            self.add_item(botao)

        remover = discord.ui.Button(
            label="Remover meu voto",
            emoji="🗑️",
            style=discord.ButtonStyle.danger,
            custom_id=f"remover_{enquete_id}"
        )

        async def remover_callback(
            interaction: discord.Interaction
        ):
            removido = remover_voto(
                self.enquete_id,
                interaction.user.id
            )

            if not removido:
                await interaction.response.send_message(
                    "❌ Você ainda não votou.",
                    ephemeral=True
                )
                return

            embed = gerar_embed_enquete(
                self.enquete_id,
                self.pergunta,
                self.opcoes
            )

            await interaction.response.edit_message(
                embed=embed,
                view=self
            )

            await interaction.followup.send(
                "🗑️ Seu voto foi removido.",
                ephemeral=True
            )

        remover.callback = remover_callback
        self.add_item(remover)

        ver = discord.ui.Button(
            label="Ver votos",
            emoji="👁️",
            style=discord.ButtonStyle.secondary,
            custom_id=f"ver_{enquete_id}"
        )

        async def ver_callback(
            interaction: discord.Interaction
        ):
            if not (
                isinstance(
                    interaction.user,
                    discord.Member
                )
                and interaction.user
                .guild_permissions
                .administrator
            ):
                await interaction.response.send_message(
                    "❌ Apenas administradores "
                    "podem ver os votos.",
                    ephemeral=True
                )
                return

            votos = buscar_votos(
                self.enquete_id
            )

            if not votos:
                await interaction.response.send_message(
                    "📭 Ninguém votou ainda.",
                    ephemeral=True
                )
                return

            linhas = []

            for linha in votos:
                opcao = linha["opcao"]

                if 0 <= opcao < len(self.opcoes):
                    linhas.append(
                        f"{emojis[opcao]} "
                        f"<@{linha['usuario_id']}> "
                        f"→ **{self.opcoes[opcao]}**"
                    )

            texto = "\n".join(linhas)

            if len(texto) > 1900:
                texto = texto[:1900] + "\n..."

            await interaction.response.send_message(
                "## 👁️ Votos\n\n"
                + texto,
                ephemeral=True
            )

        ver.callback = ver_callback
        self.add_item(ver)


# ==========================================================
# ENQUETE - MODAL
# ==========================================================

class EnqueteModal(
    discord.ui.Modal,
    title="Criar enquete"
):

    pergunta = discord.ui.TextInput(
        label="Pergunta da enquete",
        max_length=200
    )

    opcao1 = discord.ui.TextInput(
        label="Opção 1",
        max_length=80
    )

    opcao2 = discord.ui.TextInput(
        label="Opção 2",
        max_length=80
    )

    opcao3 = discord.ui.TextInput(
        label="Opção 3 (opcional)",
        required=False,
        max_length=80
    )

    async def on_submit(
        self,
        interaction: discord.Interaction
    ):
        opcoes = [
            self.opcao1.value,
            self.opcao2.value
        ]

        if self.opcao3.value:
            opcoes.append(
                self.opcao3.value
            )

        enquete_id = (
            uuid.uuid4().hex[:12]
        )

        salvar_enquete(
            enquete_id,
            self.pergunta.value,
            opcoes
        )

        embed = gerar_embed_enquete(
            enquete_id,
            self.pergunta.value,
            opcoes
        )

        view = EnqueteView(
            enquete_id,
            self.pergunta.value,
            opcoes
        )

        await interaction.response.send_message(
            "✅ Enquete criada!",
            ephemeral=True
        )

        mensagem = await interaction.channel.send(
            embed=embed,
            view=view
        )

        atualizar_mensagem_enquete(
            enquete_id,
            interaction.channel.id,
            mensagem.id
        )


# ==========================================================
# BAN / HACKBAN - BANCO
# ==========================================================

def criar_solicitacao_ban(
    solicitacao_id,
    guild_id,
    tipo,
    usuario_id,
    usuario_nome,
    solicitante_id,
    motivo,
    modo_motivo,
    data_solicitacao,
    castigo_aplicado
):
    with conectar_banco() as banco:
        banco.execute(
            """
            INSERT INTO solicitacoes_ban (
                id,
                guild_id,
                tipo,
                usuario_id,
                usuario_nome,
                solicitante_id,
                motivo,
                modo_motivo,
                data_solicitacao,
                status,
                castigo_aplicado
            )

            VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?,
                'pendente',
                ?
            )
            """,
            (
                solicitacao_id,
                guild_id,
                tipo,
                usuario_id,
                usuario_nome,
                solicitante_id,
                motivo,
                modo_motivo,
                data_solicitacao,
                int(castigo_aplicado)
            )
        )

        banco.commit()


def salvar_mensagem_solicitacao(
    solicitacao_id,
    canal_id,
    mensagem_id
):
    with conectar_banco() as banco:
        banco.execute(
            """
            UPDATE solicitacoes_ban
            SET
                canal_id = ?,
                mensagem_id = ?
            WHERE id = ?
            """,
            (
                canal_id,
                mensagem_id,
                solicitacao_id
            )
        )

        banco.commit()


def buscar_solicitacao_ban(
    solicitacao_id
):
    with conectar_banco() as banco:
        return banco.execute(
            """
            SELECT
                id,
                guild_id,
                tipo,
                usuario_id,
                usuario_nome,
                solicitante_id,
                motivo,
                modo_motivo,
                data_solicitacao,
                canal_id,
                mensagem_id,
                status,
                decisor_id,
                data_decisao,
                castigo_aplicado
            FROM solicitacoes_ban
            WHERE id = ?
            """,
            (solicitacao_id,)
        ).fetchone()


def buscar_solicitacoes_pendentes():
    with conectar_banco() as banco:
        return banco.execute(
            """
            SELECT
                id,
                mensagem_id
            FROM solicitacoes_ban
            WHERE
                status = 'pendente'
                AND mensagem_id IS NOT NULL
            """
        ).fetchall()


def buscar_castigos_pendentes():
    with conectar_banco() as banco:
        return banco.execute(
            """
            SELECT
                id,
                guild_id,
                usuario_id
            FROM solicitacoes_ban
            WHERE
                status = 'pendente'
                AND castigo_aplicado = 1
            """
        ).fetchall()


def buscar_pendente_para_usuario(
    guild_id,
    usuario_id
):
    with conectar_banco() as banco:
        return banco.execute(
            """
            SELECT id
            FROM solicitacoes_ban
            WHERE
                guild_id = ?
                AND usuario_id = ?
                AND status = 'pendente'
            LIMIT 1
            """,
            (
                guild_id,
                usuario_id
            )
        ).fetchone()


def existe_solicitacao_pendente(
    guild_id,
    usuario_id
):
    return (
        buscar_pendente_para_usuario(
            guild_id,
            usuario_id
        )
        is not None
    )


def marcar_castigo(
    solicitacao_id,
    aplicado
):
    with conectar_banco() as banco:
        banco.execute(
            """
            UPDATE solicitacoes_ban
            SET castigo_aplicado = ?
            WHERE id = ?
            """,
            (
                int(aplicado),
                solicitacao_id
            )
        )

        banco.commit()


def iniciar_decisao_ban(
    solicitacao_id
):
    with conectar_banco() as banco:
        cursor = banco.execute(
            """
            UPDATE solicitacoes_ban
            SET status = 'processando'
            WHERE
                id = ?
                AND status = 'pendente'
            """,
            (solicitacao_id,)
        )

        banco.commit()

        return cursor.rowcount == 1


def finalizar_solicitacao_ban(
    solicitacao_id,
    status,
    decisor_id
):
    agora = datetime.now(
        timezone.utc
    ).isoformat()

    with conectar_banco() as banco:
        banco.execute(
            """
            UPDATE solicitacoes_ban
            SET
                status = ?,
                decisor_id = ?,
                data_decisao = ?
            WHERE id = ?
            """,
            (
                status,
                decisor_id,
                agora,
                solicitacao_id
            )
        )

        banco.commit()


def voltar_solicitacao_para_pendente(
    solicitacao_id
):
    with conectar_banco() as banco:
        banco.execute(
            """
            UPDATE solicitacoes_ban
            SET status = 'pendente'
            WHERE
                id = ?
                AND status = 'processando'
            """,
            (solicitacao_id,)
        )

        banco.commit()


# ==========================================================
# PERMISSÃO DOS COMANDOS ADMINISTRATIVOS
# ==========================================================

def pode_usar_comando_admin(membro):
    if not isinstance(membro, discord.Member):
        return False

    if membro.id == DONO_ID:
        return True

    return any(
        cargo.id == CARGO_DESENVOLVIMENTO_ID
        for cargo in membro.roles
    )


def pode_usar_sistema_ban(membro):
    return pode_usar_comando_admin(membro)


async def negar_se_nao_admin(interaction):
    if pode_usar_comando_admin(interaction.user):
        return False

    await interaction.response.send_message(
        "❌ Apenas a Equipe de Desenvolvimento e o dono autorizado podem usar este comando.",
        ephemeral=True
    )
    return True

# ==========================================================
# UTILIDADES DE MODERAÇÃO
# ==========================================================

async def obter_membro(
    guild,
    usuario_id
):
    membro = guild.get_member(
        usuario_id
    )

    if membro is not None:
        return membro

    try:
        return await guild.fetch_member(
            usuario_id
        )

    except (
        discord.NotFound,
        discord.Forbidden,
        discord.HTTPException
    ):
        return None


async def usuario_ja_banido(
    guild,
    usuario_id
):
    try:
        await guild.fetch_ban(
            discord.Object(
                id=usuario_id
            )
        )

        return True

    except discord.NotFound:
        return False

    except discord.HTTPException:
        return False


def nome_salvo_usuario(
    usuario
):
    username = getattr(
        usuario,
        "name",
        None
    )

    global_name = getattr(
        usuario,
        "global_name",
        None
    )

    display_name = getattr(
        usuario,
        "display_name",
        None
    )

    if global_name and username:
        return (
            f"{global_name} (@{username})"
        )

    if display_name and username:
        if display_name != username:
            return (
                f"{display_name} (@{username})"
            )

    if username:
        return f"@{username}"

    return str(usuario)


async def aplicar_castigo(
    membro,
    solicitante_id,
    motivo
):
    guild = membro.guild
    bot_member = guild.me

    if membro.id == guild.owner_id:
        return (
            False,
            "O dono do servidor não pode receber castigo."
        )

    if (
        bot_member is None
        or not bot_member
        .guild_permissions
        .moderate_members
    ):
        return (
            False,
            "O bot não possui **Moderar membros**."
        )

    if (
        bot_member.top_role
        <= membro.top_role
    ):
        return (
            False,
            "A hierarquia dos cargos impede o castigo."
        )

    try:
        ate = (
            datetime.now(
                timezone.utc
            )
            + timedelta(
                days=CASTIGO_DIAS
            )
        )

        await membro.timeout(
            ate,
            reason=(
                "Solicitação de ban pendente. "
                f"Solicitante: {solicitante_id}. "
                f"Motivo: {motivo}"
            )
        )

        return (
            True,
            None
        )

    except discord.Forbidden:
        return (
            False,
            "O Discord recusou o castigo."
        )

    except discord.HTTPException as erro:
        return (
            False,
            f"Erro ao aplicar castigo: `{erro}`"
        )


async def remover_castigo(
    membro,
    decisor_id
):
    try:
        await membro.timeout(
            None,
            reason=(
                "Solicitação de ban negada. "
                f"Castigo removido por ID {decisor_id}."
            )
        )

        return (
            True,
            None
        )

    except discord.Forbidden:
        return (
            False,
            "Não consegui remover o castigo "
            "por permissão ou hierarquia."
        )

    except discord.HTTPException as erro:
        return (
            False,
            f"Erro ao remover castigo: `{erro}`"
        )


async def localizar_canal_aprovacao(
    guild
):
    canal = guild.get_channel(
        CANAL_APROVACAO_ID
    )

    if canal is None:
        try:
            canal = await guild.fetch_channel(
                CANAL_APROVACAO_ID
            )

        except (
            discord.NotFound,
            discord.Forbidden,
            discord.HTTPException
        ):
            canal = None

    if not isinstance(
        canal,
        discord.TextChannel
    ):
        return None

    return canal


# ==========================================================
# EMBED DA SOLICITAÇÃO
# ==========================================================

def timestamp_iso(valor):
    try:
        return int(
            datetime
            .fromisoformat(valor)
            .timestamp()
        )

    except (
        TypeError,
        ValueError
    ):
        return int(
            datetime.now(
                timezone.utc
            ).timestamp()
        )


def criar_embed_solicitacao(
    linha,
    status_texto="🟡 **Aguardando decisão**"
):
    tipo_texto = (
        "Ban"
        if linha["tipo"] == "ban"
        else "Hackban"
    )

    modos = {
        "escrito": "✍️ Motivo escrito",
        "informado": "✅ Motivo já informado",
    }

    embed = discord.Embed(
        title=(
            f"⚠️ Solicitação de {tipo_texto}"
        ),
        color=discord.Color.orange()
    )

    # Mantém a menção para identificação rápida.
    embed.add_field(
        name="👤 Menção",
        value=f"<@{linha['usuario_id']}>",
        inline=False
    )

    # Mantém o username salvo mesmo depois do ban.
    embed.add_field(
        name="🏷️ Username salvo",
        value=(
            f"`{linha['usuario_nome'] or 'Nome indisponível'}`"
        ),
        inline=False
    )

    embed.add_field(
        name="🆔 ID do usuário",
        value=f"`{linha['usuario_id']}`",
        inline=False
    )

    embed.add_field(
        name="🛡️ Solicitante",
        value=(
            f"<@{linha['solicitante_id']}>\n"
            f"`{linha['solicitante_id']}`"
        ),
        inline=False
    )

    embed.add_field(
        name="📝 Forma do motivo",
        value=modos.get(
            linha["modo_motivo"],
            linha["modo_motivo"]
        ),
        inline=False
    )

    embed.add_field(
        name="📄 Motivo",
        value=linha["motivo"],
        inline=False
    )

    embed.add_field(
        name="🕐 Data",
        value=(
            f"<t:"
            f"{timestamp_iso(linha['data_solicitacao'])}"
            f":F>"
        ),
        inline=False
    )

    embed.add_field(
        name="🔒 Castigo",
        value=(
            "Aplicado enquanto aguarda."
            if linha["castigo_aplicado"]
            else "Não aplicado / não aplicável."
        ),
        inline=False
    )

    embed.add_field(
        name="📌 Status",
        value=status_texto,
        inline=False
    )

    embed.set_footer(
        text=f"Solicitação: {linha['id']}"
    )

    return embed


# ==========================================================
# EDITAR MENSAGEM DA SOLICITAÇÃO
# ==========================================================

async def editar_mensagem_solicitacao(
    guild,
    solicitacao,
    embed,
    view
):
    canal = guild.get_channel(
        solicitacao["canal_id"]
    )

    if canal is None:
        try:
            canal = await guild.fetch_channel(
                solicitacao["canal_id"]
            )

        except (
            discord.NotFound,
            discord.Forbidden,
            discord.HTTPException
        ):
            return

    if not isinstance(
        canal,
        discord.TextChannel
    ):
        return

    try:
        mensagem = await canal.fetch_message(
            solicitacao["mensagem_id"]
        )

        await mensagem.edit(
            embed=embed,
            view=view
        )

    except (
        discord.NotFound,
        discord.Forbidden,
        discord.HTTPException
    ):
        pass


# ==========================================================
# APROVAR
# ==========================================================

async def processar_aprovacao(
    interaction,
    solicitacao_id
):
    solicitacao = buscar_solicitacao_ban(
        solicitacao_id
    )

    if (
        solicitacao is None
        or solicitacao["status"]
        != "pendente"
    ):
        if interaction.response.is_done():
            await interaction.followup.send(
                "⚠️ Solicitação já decidida.",
                ephemeral=True
            )

        else:
            await interaction.response.send_message(
                "⚠️ Solicitação já decidida.",
                ephemeral=True
            )

        return

    if not iniciar_decisao_ban(
        solicitacao_id
    ):
        if interaction.response.is_done():
            await interaction.followup.send(
                "⚠️ Solicitação já está sendo processada.",
                ephemeral=True
            )

        else:
            await interaction.response.send_message(
                "⚠️ Solicitação já está sendo processada.",
                ephemeral=True
            )

        return

    if not interaction.response.is_done():
        await interaction.response.defer(
            ephemeral=True,
            thinking=True
        )

    guild = interaction.guild

    if guild is None:
        voltar_solicitacao_para_pendente(
            solicitacao_id
        )

        await interaction.followup.send(
            "❌ Servidor não encontrado.",
            ephemeral=True
        )
        return

    usuario_id = solicitacao["usuario_id"]

    if usuario_id == guild.owner_id:
        voltar_solicitacao_para_pendente(
            solicitacao_id
        )

        await interaction.followup.send(
            "❌ O dono do servidor não pode ser banido.",
            ephemeral=True
        )
        return

    bot_member = guild.me

    if (
        bot_member is None
        or not bot_member
        .guild_permissions
        .ban_members
    ):
        voltar_solicitacao_para_pendente(
            solicitacao_id
        )

        await interaction.followup.send(
            "❌ O bot não possui **Banir membros**.",
            ephemeral=True
        )
        return

    membro = await obter_membro(
        guild,
        usuario_id
    )

    if (
        membro is not None
        and bot_member.top_role
        <= membro.top_role
    ):
        voltar_solicitacao_para_pendente(
            solicitacao_id
        )

        await interaction.followup.send(
            "❌ Hierarquia impede o banimento.",
            ephemeral=True
        )
        return

    try:
        await guild.ban(
            discord.Object(
                id=usuario_id
            ),
            reason=(
                f"{solicitacao['tipo'].upper()} aprovado | "
                f"Solicitante: {solicitacao['solicitante_id']} | "
                f"Aprovado por: {interaction.user.id} | "
                f"Motivo: {solicitacao['motivo']}"
            )
        )

    except discord.Forbidden:
        voltar_solicitacao_para_pendente(
            solicitacao_id
        )

        await interaction.followup.send(
            "❌ O Discord recusou o banimento.",
            ephemeral=True
        )
        return

    except discord.HTTPException as erro:
        voltar_solicitacao_para_pendente(
            solicitacao_id
        )

        await interaction.followup.send(
            f"❌ Erro ao banir: `{erro}`",
            ephemeral=True
        )
        return

    finalizar_solicitacao_ban(
        solicitacao_id,
        "aprovado",
        interaction.user.id
    )

    solicitacao = buscar_solicitacao_ban(
        solicitacao_id
    )

    agora = int(
        datetime.now(
            timezone.utc
        ).timestamp()
    )

    embed = criar_embed_solicitacao(
        solicitacao,
        status_texto=(
            "✅ **BANIMENTO APROVADO**\n\n"
            f"Solicitado por: "
            f"<@{solicitacao['solicitante_id']}>\n"
            f"Aprovado por: "
            f"<@{interaction.user.id}>\n"
            f"Decisão: <t:{agora}:F>"
        )
    )

    embed.color = (
        discord.Color.green()
    )

    await editar_mensagem_solicitacao(
        guild,
        solicitacao,
        embed,
        BanApprovalView(
            solicitacao_id,
            desativado=True
        )
    )

    await interaction.followup.send(
        "✅ Banimento executado.",
        ephemeral=True
    )


# ==========================================================
# NEGAR
# ==========================================================

async def processar_negacao(
    interaction,
    solicitacao_id
):
    solicitacao = buscar_solicitacao_ban(
        solicitacao_id
    )

    if (
        solicitacao is None
        or solicitacao["status"]
        != "pendente"
    ):
        await interaction.response.send_message(
            "⚠️ Solicitação já decidida.",
            ephemeral=True
        )
        return

    if not iniciar_decisao_ban(
        solicitacao_id
    ):
        await interaction.response.send_message(
            "⚠️ Solicitação já está sendo processada.",
            ephemeral=True
        )
        return

    await interaction.response.defer(
        ephemeral=True,
        thinking=True
    )

    guild = interaction.guild

    if guild is None:
        voltar_solicitacao_para_pendente(
            solicitacao_id
        )

        await interaction.followup.send(
            "❌ Servidor não encontrado.",
            ephemeral=True
        )
        return

    if solicitacao["castigo_aplicado"]:
        membro = await obter_membro(
            guild,
            solicitacao["usuario_id"]
        )

        if membro is not None:
            ok, erro = await remover_castigo(
                membro,
                interaction.user.id
            )

            if not ok:
                voltar_solicitacao_para_pendente(
                    solicitacao_id
                )

                await interaction.followup.send(
                    f"❌ {erro}",
                    ephemeral=True
                )
                return

        marcar_castigo(
            solicitacao_id,
            False
        )

        if membro is not None:
            cadastro_nick = buscar_cadastro_nick(guild.id, membro.id)
            if cadastro_nick and cadastro_nick["castigo_aplicado"]:
                await aplicar_castigo_nick(membro)

    finalizar_solicitacao_ban(
        solicitacao_id,
        "negado",
        interaction.user.id
    )

    solicitacao = buscar_solicitacao_ban(
        solicitacao_id
    )

    agora = int(
        datetime.now(
            timezone.utc
        ).timestamp()
    )

    embed = criar_embed_solicitacao(
        solicitacao,
        status_texto=(
            "❌ **SOLICITAÇÃO NEGADA**\n\n"
            f"Solicitado por: "
            f"<@{solicitacao['solicitante_id']}>\n"
            f"Negado por: "
            f"<@{interaction.user.id}>\n"
            f"Decisão: <t:{agora}:F>"
        )
    )

    embed.color = (
        discord.Color.red()
    )

    await editar_mensagem_solicitacao(
        guild,
        solicitacao,
        embed,
        BanApprovalView(
            solicitacao_id,
            desativado=True
        )
    )

    await interaction.followup.send(
        "❌ Solicitação negada e castigo removido.",
        ephemeral=True
    )


# ==========================================================
# BOTÕES APROVAR / NEGAR
# ==========================================================

class BanApprovalView(
    discord.ui.View
):

    def __init__(
        self,
        solicitacao_id,
        desativado=False
    ):
        super().__init__(
            timeout=None
        )

        self.solicitacao_id = (
            solicitacao_id
        )

        aprovar = discord.ui.Button(
            label="Aprovar banimento",
            emoji="✅",
            style=discord.ButtonStyle.success,
            custom_id=(
                f"ban_aprovar_{solicitacao_id}"
            ),
            disabled=desativado
        )

        negar = discord.ui.Button(
            label="Negar banimento",
            emoji="❌",
            style=discord.ButtonStyle.danger,
            custom_id=(
                f"ban_negar_{solicitacao_id}"
            ),
            disabled=desativado
        )

        async def aprovar_callback(
            interaction: discord.Interaction
        ):
            if interaction.user.id != DONO_ID:
                await interaction.response.send_message(
                    "❌ Você não possui autorização.",
                    ephemeral=True
                )
                return

            await processar_aprovacao(
                interaction,
                self.solicitacao_id
            )

        async def negar_callback(
            interaction: discord.Interaction
        ):
            if interaction.user.id != DONO_ID:
                await interaction.response.send_message(
                    "❌ Você não possui autorização.",
                    ephemeral=True
                )
                return

            await processar_negacao(
                interaction,
                self.solicitacao_id
            )

        aprovar.callback = aprovar_callback
        negar.callback = negar_callback

        self.add_item(aprovar)
        self.add_item(negar)


# ==========================================================
# PREPARAR SOLICITAÇÃO
# ==========================================================

async def preparar_e_enviar_solicitacao(
    interaction,
    usuario_id,
    tipo,
    modo_motivo,
    motivo
):
    guild = interaction.guild

    if guild is None:
        await interaction.response.send_message(
            "❌ Só funciona em servidor.",
            ephemeral=True
        )
        return

    if not pode_usar_sistema_ban(
        interaction.user
    ):
        await interaction.response.send_message(
            "❌ Você não possui autorização "
            "para usar o sistema da Equipe de Ban.",
            ephemeral=True
        )
        return

    if not interaction.response.is_done():
        await interaction.response.defer(
            ephemeral=True,
            thinking=True
        )

    membro_alvo = await obter_membro(
        guild,
        usuario_id
    )

    usuario_nome = (
        "Nome indisponível"
    )

    # ------------------------------------------------------
    # BAN NORMAL
    # ------------------------------------------------------

    if tipo == "ban":
        if membro_alvo is None:
            await interaction.followup.send(
                "❌ Esse usuário não está mais "
                "no servidor.\n"
                "Use Hackban pelo ID.",
                ephemeral=True
            )
            return

        usuario_nome = nome_salvo_usuario(
            membro_alvo
        )

    # ------------------------------------------------------
    # HACKBAN
    # ------------------------------------------------------

    else:
        try:
            usuario_global = (
                await interaction.client.fetch_user(
                    usuario_id
                )
            )

            usuario_nome = nome_salvo_usuario(
                usuario_global
            )

        except discord.NotFound:
            await interaction.followup.send(
                "❌ ID de usuário não encontrado.",
                ephemeral=True
            )
            return

        except discord.HTTPException:
            usuario_nome = (
                "Nome não pôde ser consultado"
            )

    # ------------------------------------------------------
    # PROTEÇÕES
    # ------------------------------------------------------

    if usuario_id == interaction.user.id:
        await interaction.followup.send(
            "❌ Você não pode solicitar ban de si mesmo.",
            ephemeral=True
        )
        return

    if usuario_id == guild.owner_id:
        await interaction.followup.send(
            "❌ O dono do servidor não pode ser alvo.",
            ephemeral=True
        )
        return

    if (
        interaction.client.user
        and usuario_id
        == interaction.client.user.id
    ):
        await interaction.followup.send(
            "❌ O bot não pode ser alvo.",
            ephemeral=True
        )
        return

    if await usuario_ja_banido(
        guild,
        usuario_id
    ):
        await interaction.followup.send(
            "⚠️ Esse usuário já está banido.",
            ephemeral=True
        )
        return

    if existe_solicitacao_pendente(
        guild.id,
        usuario_id
    ):
        await interaction.followup.send(
            "⚠️ Já existe uma solicitação "
            "pendente para esse usuário.",
            ephemeral=True
        )
        return

    bot_member = guild.me

    if (
        bot_member is None
        or not bot_member
        .guild_permissions
        .ban_members
    ):
        await interaction.followup.send(
            "❌ O bot não possui **Banir membros**.",
            ephemeral=True
        )
        return

    if (
        membro_alvo is not None
        and bot_member.top_role
        <= membro_alvo.top_role
    ):
        await interaction.followup.send(
            "❌ Hierarquia impede a punição.",
            ephemeral=True
        )
        return

    canal = await localizar_canal_aprovacao(
        guild
    )

    if canal is None:
        await interaction.followup.send(
            "❌ Canal de aprovação não encontrado.",
            ephemeral=True
        )
        return

    # ------------------------------------------------------
    # CASTIGO
    # ------------------------------------------------------

    castigo_aplicado = False

    if membro_alvo is not None:
        motivo_castigo = (
            motivo
            if modo_motivo == "escrito"
            else "Solicitação aguardando análise."
        )

        ok, erro = await aplicar_castigo(
            membro_alvo,
            interaction.user.id,
            motivo_castigo
        )

        if not ok:
            await interaction.followup.send(
                f"❌ Não consegui aplicar "
                f"castigo: {erro}",
                ephemeral=True
            )
            return

        castigo_aplicado = True

    # ------------------------------------------------------
    # SALVAR SOLICITAÇÃO
    # ------------------------------------------------------

    solicitacao_id = (
        uuid.uuid4().hex[:12]
    )

    data_iso = (
        datetime.now(
            timezone.utc
        )
        .isoformat()
    )

    criar_solicitacao_ban(
        solicitacao_id,
        guild.id,
        tipo,
        usuario_id,
        usuario_nome,
        interaction.user.id,
        motivo,
        modo_motivo,
        data_iso,
        castigo_aplicado
    )

    solicitacao = buscar_solicitacao_ban(
        solicitacao_id
    )

    embed = criar_embed_solicitacao(
        solicitacao
    )

    view = BanApprovalView(
        solicitacao_id
    )

    try:
        mensagem = await canal.send(
            embed=embed,
            view=view
        )

    except (
        discord.Forbidden,
        discord.HTTPException
    ) as erro:
        if (
            castigo_aplicado
            and membro_alvo is not None
        ):
            await remover_castigo(
                membro_alvo,
                interaction.user.id
            )

        finalizar_solicitacao_ban(
            solicitacao_id,
            "cancelado",
            interaction.user.id
        )

        await interaction.followup.send(
            f"❌ Não consegui enviar "
            f"a solicitação: `{erro}`",
            ephemeral=True
        )
        return

    salvar_mensagem_solicitacao(
        solicitacao_id,
        canal.id,
        mensagem.id
    )

    tipo_nome = (
        "Ban"
        if tipo == "ban"
        else "Hackban"
    )

    await interaction.followup.send(
        f"✅ Solicitação de "
        f"**{tipo_nome}** enviada.",
        ephemeral=True
    )


# ==========================================================
# MOTIVO ESCRITO
# ==========================================================

class MotivoEscritoModal(
    discord.ui.Modal,
    title="Motivo da solicitação"
):

    motivo = discord.ui.TextInput(
        label="Motivo",
        placeholder=(
            "Explique o motivo da solicitação"
        ),
        style=discord.TextStyle.paragraph,
        required=True,
        min_length=1,
        max_length=1000
    )

    def __init__(
        self,
        usuario_id,
        tipo
    ):
        super().__init__()

        self.usuario_id = usuario_id
        self.tipo = tipo

    async def on_submit(
        self,
        interaction: discord.Interaction
    ):
        if not pode_usar_sistema_ban(
            interaction.user
        ):
            await interaction.response.send_message(
                "❌ Você não possui autorização.",
                ephemeral=True
            )
            return

        motivo = self.motivo.value.strip()

        if not motivo:
            await interaction.response.send_message(
                "❌ O motivo é obrigatório.",
                ephemeral=True
            )
            return

        await preparar_e_enviar_solicitacao(
            interaction,
            self.usuario_id,
            self.tipo,
            "escrito",
            motivo
        )


# ==========================================================
# ESCOLHER FORMA DO MOTIVO
# ==========================================================

class EscolherMotivoSelect(
    discord.ui.Select
):

    def __init__(
        self,
        usuario_id,
        tipo
    ):
        self.usuario_id = usuario_id
        self.tipo = tipo

        opcoes = [
            discord.SelectOption(
                label="Escrever o motivo",
                description="Escreva o motivo agora.",
                emoji="✍️",
                value="escrito"
            ),

            discord.SelectOption(
                label="Motivo já informado",
                description=(
                    "O motivo já foi informado anteriormente."
                ),
                emoji="✅",
                value="informado"
            )
        ]

        super().__init__(
            placeholder=(
                "Como será informado o motivo?"
            ),
            min_values=1,
            max_values=1,
            options=opcoes
        )

    async def callback(
        self,
        interaction: discord.Interaction
    ):
        if not pode_usar_sistema_ban(
            interaction.user
        ):
            await interaction.response.send_message(
                "❌ Você não possui autorização.",
                ephemeral=True
            )
            return

        modo = self.values[0]

        if modo == "escrito":
            await interaction.response.send_modal(
                MotivoEscritoModal(
                    self.usuario_id,
                    self.tipo
                )
            )
            return

        await preparar_e_enviar_solicitacao(
            interaction,
            self.usuario_id,
            self.tipo,
            "informado",
            "Motivo já informado."
        )


class EscolherMotivoView(
    discord.ui.View
):

    def __init__(
        self,
        usuario_id,
        tipo
    ):
        super().__init__(
            timeout=300
        )

        self.add_item(
            EscolherMotivoSelect(
                usuario_id,
                tipo
            )
        )


# ==========================================================
# BAN - SELETOR NATIVO
# ==========================================================

class SelecionarUsuarioBan(
    discord.ui.UserSelect
):

    def __init__(self):
        super().__init__(
            placeholder=(
                "Clique aqui e escolha o usuário"
            ),
            min_values=1,
            max_values=1
        )

    async def callback(
        self,
        interaction: discord.Interaction
    ):
        if not pode_usar_sistema_ban(
            interaction.user
        ):
            await interaction.response.send_message(
                "❌ Você não possui autorização.",
                ephemeral=True
            )
            return

        usuario = self.values[0]

        membro = await obter_membro(
            interaction.guild,
            usuario.id
        )

        if membro is None:
            await interaction.response.send_message(
                "❌ Esse usuário não está mais "
                "no servidor.\n"
                "Use Hackban pelo ID.",
                ephemeral=True
            )
            return

        await interaction.response.edit_message(
            content=(
                f"👤 **Usuário selecionado:** "
                f"{membro.mention}\n\n"
                "Agora escolha como o motivo "
                "será informado:"
            ),
            view=EscolherMotivoView(
                membro.id,
                "ban"
            )
        )


class SelecionarUsuarioBanView(
    discord.ui.View
):

    def __init__(self):
        super().__init__(
            timeout=300
        )

        self.add_item(
            SelecionarUsuarioBan()
        )


# ==========================================================
# HACKBAN
# ==========================================================

class HackbanIdModal(
    discord.ui.Modal,
    title="Solicitar Hackban"
):

    usuario_id = discord.ui.TextInput(
        label="ID do usuário",
        placeholder="123456789012345678",
        required=True,
        min_length=15,
        max_length=25
    )

    async def on_submit(
        self,
        interaction: discord.Interaction
    ):
        if not pode_usar_sistema_ban(
            interaction.user
        ):
            await interaction.response.send_message(
                "❌ Você não possui autorização.",
                ephemeral=True
            )
            return

        valor = (
            self.usuario_id
            .value
            .strip()
        )

        if not valor.isdigit():
            await interaction.response.send_message(
                "❌ O ID deve conter somente números.",
                ephemeral=True
            )
            return

        usuario_id = int(valor)

        await interaction.response.send_message(
            content=(
                f"🆔 **ID informado:** "
                f"`{usuario_id}`\n\n"
                "Agora escolha como o motivo "
                "será informado:"
            ),
            view=EscolherMotivoView(
                usuario_id,
                "hackban"
            ),
            ephemeral=True
        )


# ==========================================================
# PAINEL BAN
# ==========================================================

class PainelBanView(
    discord.ui.View
):

    def __init__(self):
        super().__init__(
            timeout=None
        )

    @discord.ui.button(
        label="Solicitar Ban",
        emoji="👤",
        style=discord.ButtonStyle.danger,
        custom_id="painel_ban_normal_v6"
    )
    async def solicitar_ban(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button
    ):
        if not pode_usar_sistema_ban(
            interaction.user
        ):
            await interaction.response.send_message(
                "❌ Você não possui autorização "
                "para usar o sistema de Ban.",
                ephemeral=True
            )
            return

        await interaction.response.send_message(
            content=(
                "👤 **Solicitar Ban**\n\n"
                "Clique na caixa abaixo e "
                "escolha o membro."
            ),
            view=SelecionarUsuarioBanView(),
            ephemeral=True
        )

    @discord.ui.button(
        label="Solicitar Hackban",
        emoji="🆔",
        style=discord.ButtonStyle.secondary,
        custom_id="painel_hackban_v6"
    )
    async def solicitar_hackban(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button
    ):
        if not pode_usar_sistema_ban(
            interaction.user
        ):
            await interaction.response.send_message(
                "❌ Você não possui autorização "
                "para usar o sistema de Ban.",
                ephemeral=True
            )
            return

        await interaction.response.send_modal(
            HackbanIdModal()
        )


# ==========================================================
# MINECRAFT - STATUS NO CANAL
# ==========================================================

def criar_embed_status_minecraft(online):
    if online:
        titulo = "🟢 SERVIDOR MINECRAFT ONLINE"
        descricao = "O servidor de Minecraft da **Resenha Máxima** está disponível agora."
        cor = discord.Color.green()
    else:
        titulo = "🔴 SERVIDOR MINECRAFT OFFLINE"
        descricao = "O servidor de Minecraft da **Resenha Máxima** está offline no momento."
        cor = discord.Color.red()

    embed = discord.Embed(
        title=titulo,
        description=descricao,
        color=cor,
        timestamp=datetime.now(timezone.utc)
    )
    embed.add_field(name="Status", value="🟢 Online" if online else "🔴 Offline", inline=True)
    embed.add_field(name="Verificação", value="Ping real do Minecraft", inline=True)
    embed.set_footer(text="Resenha Máxima • Minecraft • Última mudança de status")
    return embed


async def obter_canal_por_id(canal_id):
    canal = bot.get_channel(canal_id)
    if canal is None:
        try:
            canal = await bot.fetch_channel(canal_id)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            return None
    return canal if hasattr(canal, 'send') else None


async def _localizar_paineis_status_minecraft(canal):
    encontrados = []

    try:
        async for mensagem in canal.history(
            limit=100
        ):
            if (
                bot.user is not None
                and mensagem.author.id != bot.user.id
            ):
                continue

            if not mensagem.embeds:
                continue

            titulo = (
                mensagem.embeds[0].title
                or ""
            ).upper()

            if (
                "SERVIDOR MINECRAFT ONLINE" in titulo
                or "SERVIDOR MINECRAFT OFFLINE" in titulo
            ):
                encontrados.append(mensagem)

    except (
        discord.Forbidden,
        discord.HTTPException
    ):
        pass

    encontrados.sort(
        key=lambda msg: msg.created_at,
        reverse=True
    )

    return encontrados


async def atualizar_mensagem_status_minecraft(
    online
):
    canal = await obter_canal_por_id(
        CANAL_STATUS_MINECRAFT_ID
    )

    if canal is None:
        print(
            "Canal de status Minecraft "
            "não encontrado."
        )
        return

    mensagem = None
    mensagem_id = obter_estado(
        "minecraft_status_message_id"
    )

    if mensagem_id:
        try:
            mensagem = await canal.fetch_message(
                int(mensagem_id)
            )
        except (
            ValueError,
            discord.NotFound,
            discord.Forbidden,
            discord.HTTPException
        ):
            mensagem = None

    paineis = []

    if mensagem is None:
        paineis = await _localizar_paineis_status_minecraft(
            canal
        )

        if paineis:
            mensagem = paineis[0]

            salvar_estado(
                "minecraft_status_message_id",
                mensagem.id
            )

    embed = criar_embed_status_minecraft(
        online
    )

    if mensagem is None:
        mensagem = await canal.send(
            embed=embed
        )

        salvar_estado(
            "minecraft_status_message_id",
            mensagem.id
        )

        print(
            "Mensagem de status Minecraft "
            f"criada: {mensagem.id}"
        )

    else:
        await mensagem.edit(
            embed=embed
        )

    # Se existirem painéis duplicados antigos,
    # mantém somente o painel oficial mais recente.
    if not paineis:
        paineis = await _localizar_paineis_status_minecraft(
            canal
        )

    for duplicada in paineis:
        if duplicada.id == mensagem.id:
            continue

        try:
            await duplicada.delete()
            print(
                "Painel Minecraft duplicado removido: "
                f"{duplicada.id}"
            )
        except (
            discord.Forbidden,
            discord.HTTPException
        ):
            pass


# ==========================================================
# MINECRAFT - PING REAL
# ==========================================================

async def minecraft_esta_online():
    """
    Verifica exclusivamente o servidor Bedrock do Aternos.

    O Aternos pode responder ao ping mesmo desligado.
    Quando isso acontece, o MOTD retorna "Offline".
    """
    try:
        servidor = BedrockServer(
            MINECRAFT_HOST,
            MINECRAFT_PORTA,
            timeout=5
        )

        status = await asyncio.wait_for(
            servidor.async_status(
                tries=1
            ),
            timeout=7
        )

        motd = str(
            status.motd
        ).strip().casefold()

        if "offline" in motd:
            print(
                "Ping Bedrock respondeu, "
                "mas o MOTD indica OFFLINE."
            )
            return False

        print(
            "Ping Minecraft Bedrock OK | "
            f"{MINECRAFT_HOST}:{MINECRAFT_PORTA} | "
            f"MOTD: {status.motd}"
        )

        return True

    except (
        asyncio.TimeoutError,
        TimeoutError,
        ConnectionError,
        OSError
    ) as erro:
        print(
            "Ping Minecraft Bedrock falhou | "
            f"{type(erro).__name__}: {erro}"
        )
        return False

    except Exception as erro:
        print(
            "Erro no ping Minecraft Bedrock | "
            f"{type(erro).__name__}: {erro}"
        )
        return False


falhas_minecraft = 0
sucessos_minecraft = 0
status_minecraft_inicializado = False


@tasks.loop(seconds=INTERVALO_MINECRAFT_SEGUNDOS)
async def monitorar_minecraft():
    global falhas_minecraft, sucessos_minecraft, status_minecraft_inicializado

    online_agora = await minecraft_esta_online()
    estado_salvo = obter_estado('minecraft_online')

    if estado_salvo is None:
        salvar_estado('minecraft_online', '1' if online_agora else '0')
        status_minecraft_inicializado = True
        await atualizar_mensagem_status_minecraft(online_agora)
        return

    estava_online = estado_salvo == '1'
    if not status_minecraft_inicializado:
        status_minecraft_inicializado = True
        await atualizar_mensagem_status_minecraft(estava_online)

    if online_agora:
        falhas_minecraft = 0
        if estava_online:
            sucessos_minecraft = 0
            return
        sucessos_minecraft += 1
        if sucessos_minecraft < SUCESSOS_ONLINE_NECESSARIOS:
            return
        sucessos_minecraft = 0
        salvar_estado('minecraft_online', '1')
        await atualizar_mensagem_status_minecraft(True)
        print('Minecraft mudou de OFFLINE para ONLINE.')
        return

    sucessos_minecraft = 0
    if not estava_online:
        falhas_minecraft = 0
        return
    falhas_minecraft += 1
    if falhas_minecraft < FALHAS_OFFLINE_NECESSARIAS:
        return
    falhas_minecraft = 0
    salvar_estado('minecraft_online', '0')
    await atualizar_mensagem_status_minecraft(False)
    print('Minecraft mudou de ONLINE para OFFLINE.')


@monitorar_minecraft.before_loop
async def antes_de_monitorar_minecraft():
    await bot.wait_until_ready()


# ==========================================================
# MINECRAFT - NICKNAMES
# ==========================================================

def criar_embed_log_admin(texto):
    texto = str(texto)

    if texto.startswith("✅"):
        titulo = "✅ Ação concluída"
        cor = discord.Color.green()
    elif texto.startswith("⚠️"):
        titulo = "⚠️ Atenção"
        cor = discord.Color.orange()
    elif texto.startswith("🔒"):
        titulo = "🔒 Castigo de nickname"
        cor = discord.Color.red()
    elif texto.startswith("🗑️"):
        titulo = "🗑️ Cadastro removido"
        cor = discord.Color.dark_grey()
    elif texto.startswith("🚪"):
        titulo = "🚪 Membro saiu"
        cor = discord.Color.orange()
    elif texto.startswith("↩️"):
        titulo = "↩️ Membro retornou"
        cor = discord.Color.blue()
    elif texto.startswith("📣"):
        titulo = "📣 Aviso no servidor"
        cor = discord.Color.gold()
    elif texto.startswith("🎮"):
        titulo = "🎮 Cadastro Minecraft"
        cor = discord.Color.green()
    elif texto.startswith("🔄"):
        titulo = "🔄 Novo cadastro solicitado"
        cor = discord.Color.gold()
    else:
        titulo = "📋 Registro do bot"
        cor = discord.Color.blurple()

    embed = discord.Embed(
        title=titulo,
        description=texto,
        color=cor,
        timestamp=datetime.now(timezone.utc)
    )
    embed.set_footer(
        text="Resenha Máxima • Administração"
    )
    return embed


async def enviar_log_dono(texto):
    dono = bot.get_user(DONO_ID)

    if dono is None:
        try:
            dono = await bot.fetch_user(DONO_ID)
        except (
            discord.NotFound,
            discord.Forbidden,
            discord.HTTPException
        ):
            return

    try:
        await dono.send(
            embed=criar_embed_log_admin(texto)
        )
    except (
        discord.Forbidden,
        discord.HTTPException
    ):
        pass


def normalizar_nome_canal(nome):
    return (
        nome.lower()
        .replace("・", "-")
        .replace("•", "-")
        .replace(" ", "-")
        .replace("_", "-")
    )


async def obter_chat_geral(guild):
    """
    Procura automaticamente o chat geral da Resenha.
    Prioridade:
    1) canal cujo nome contenha 'chat-da-resenha'
    2) canal cujo nome contenha 'chat-geral'
    3) canal 'geral'
    4) system_channel do servidor
    """
    candidatos = []

    for canal in guild.text_channels:
        nome = normalizar_nome_canal(canal.name)

        if "chat-da-resenha" in nome:
            return canal

        if "chat-geral" in nome:
            candidatos.append((0, canal))
        elif nome == "geral" or nome.endswith("-geral"):
            candidatos.append((1, canal))

    if candidatos:
        candidatos.sort(key=lambda item: item[0])
        return candidatos[0][1]

    return guild.system_channel


async def avisar_dm_fechada_no_chat(membro):
    """
    Se a DM do membro estiver fechada, menciona a pessoa no chat geral
    pedindo para abrir as mensagens privadas.

    Há um bloqueio de 6 horas para não repetir menções em sequência.
    """
    chave = (
        "dm_nick_fechada_chat_"
        f"{membro.guild.id}_{membro.id}"
    )

    ultimo = obter_estado(chave)
    agora = datetime.now(timezone.utc)

    if ultimo:
        try:
            ultimo_dt = datetime.fromisoformat(ultimo)

            if agora - ultimo_dt < timedelta(hours=6):
                return
        except (TypeError, ValueError):
            pass

    canal = await obter_chat_geral(
        membro.guild
    )

    if canal is None:
        await enviar_log_dono(
            "⚠️ A DM de "
            f"{membro} ({membro.id}) está fechada e "
            "não encontrei o chat geral para avisá-lo."
        )
        return

    try:
        await canal.send(
            (
                f"{membro.mention}, preciso falar com você no privado "
                "para concluir seu cadastro do Minecraft. 🎮\n"
                "Por favor, **abra suas mensagens diretas (DMs)** "
                "do servidor e aguarde o próximo aviso do bot."
            ),
            allowed_mentions=discord.AllowedMentions(
                users=True,
                roles=False,
                everyone=False
            )
        )

        salvar_estado(
            chave,
            agora.isoformat()
        )

        await enviar_log_dono(
            "📣 DM fechada: mencionei "
            f"{membro} ({membro.id}) no chat geral."
        )

    except (
        discord.Forbidden,
        discord.HTTPException
    ) as erro:
        await enviar_log_dono(
            "⚠️ A DM de "
            f"{membro} ({membro.id}) está fechada e "
            f"não consegui avisar no chat geral: {erro}"
        )



def validar_formato_nickname(nickname):
    nickname = " ".join(
        nickname.strip().split()
    )

    if len(nickname) < NICK_MIN_CARACTERES:
        return False, "Esse nickname é curto demais."

    if len(nickname) > NICK_MAX_CARACTERES:
        return (
            False,
            f"O nickname pode ter no máximo "
            f"{NICK_MAX_CARACTERES} caracteres."
        )

    sem_espacos = nickname.replace(" ", "")

    if len(set(sem_espacos.lower())) <= 1:
        return (
            False,
            "Esse nickname não parece ser um gamertag real."
        )

    if not any(
        caractere.isalnum()
        for caractere in nickname
    ):
        return (
            False,
            "O nickname precisa conter letras ou números."
        )

    return True, None


async def responder_nick_invalido(
    canal_dm,
    motivo
):
    await canal_dm.send(
        "😭 **Tá de sacanagem? Bota a porra do nick certo.**\n\n"
        f"{motivo}\n"
        "Manda o seu **nickname completo do Minecraft** "
        "para eu cadastrar."
    )

async def enviar_pergunta_nick(membro, aviso=None):
    if aviso is None:
        texto = (
            "🎮 **Cadastro do Minecraft — Resenha Máxima**\n\n"
            "Você recebeu o cargo de Minecraft. Responda **esta DM** com o seu nickname no Minecraft."
        )
    else:
        texto = (
            f"⚠️ **Aviso {aviso}/4 — nickname pendente**\n\n"
            "Responda esta DM com o seu nickname no Minecraft. "
            "Após o 4º aviso, será aplicado timeout até o cadastro."
        )
    try:
        await membro.send(texto)
        return True

    except (
        discord.Forbidden,
        discord.HTTPException
    ):
        await avisar_dm_fechada_no_chat(
            membro
        )
        return False


async def iniciar_cadastro_nick(membro):
    iniciar_pendencia_nick(membro.guild.id, membro.id)
    cadastro = buscar_cadastro_nick(membro.guild.id, membro.id)
    if cadastro and cadastro['nickname']:
        return
    if cadastro and not cadastro['solicitacao_enviada']:
        enviado = await enviar_pergunta_nick(membro)
        atualizar_cadastro_nick(membro.guild.id, membro.id, solicitacao_enviada=1)
        if not enviado:
            await enviar_log_dono(f'⚠️ DM de cadastro bloqueada para {membro} ({membro.id}).')


def listar_nicknames_publicos(
    guild_id
):
    with conectar_banco() as banco:
        return banco.execute(
            """
            SELECT *
            FROM minecraft_nicknames
            WHERE
                guild_id = ?
                AND nickname IS NOT NULL
                AND TRIM(nickname) <> ''
                AND status IN ('ativo', 'ausente')
            ORDER BY LOWER(nickname), usuario_id
            """,
            (guild_id,)
        ).fetchall()


def criar_embed_tabela_nicknames(
    guild
):
    linhas = []

    for cadastro in listar_nicknames_publicos(
        guild.id
    ):
        membro = guild.get_member(
            cadastro["usuario_id"]
        )

        if membro is not None:
            usuario = membro.mention
        else:
            usuario = f"<@{cadastro['usuario_id']}>"

        linhas.append(
            f"{usuario} — `{cadastro['nickname']}`"
        )

    if not linhas:
        descricao = (
            "Nenhum nickname cadastrado ainda."
        )
    else:
        descricao = "\n".join(linhas)

        if len(descricao) > 4000:
            descricao = (
                descricao[:3950]
                + "\n\n… lista muito grande para exibir inteira."
            )

    embed = discord.Embed(
        title="🎮 NICKNAMES DA GALERA",
        description=descricao,
        color=discord.Color.gold(),
        timestamp=datetime.now(
            timezone.utc
        )
    )

    embed.set_footer(
        text=(
            "Resenha Máxima • "
            "Tabela atualizada automaticamente"
        )
    )

    return embed


async def _localizar_tabelas_nicknames(
    canal
):
    encontrados = []

    try:
        async for mensagem in canal.history(
            limit=100
        ):
            if (
                bot.user is not None
                and mensagem.author.id != bot.user.id
            ):
                continue

            if not mensagem.embeds:
                continue

            titulo = (
                mensagem.embeds[0].title
                or ""
            ).upper()

            if "NICKNAMES DA GALERA" in titulo:
                encontrados.append(
                    mensagem
                )

    except (
        discord.Forbidden,
        discord.HTTPException
    ):
        pass

    encontrados.sort(
        key=lambda msg: msg.created_at,
        reverse=True
    )

    return encontrados


async def atualizar_tabela_nicknames(
    guild
):
    canal = await obter_canal_por_id(
        CANAL_NICKNAMES_MINECRAFT_ID
    )

    if canal is None:
        raise RuntimeError(
            "Canal de nicknames não encontrado."
        )

    mensagem = None
    mensagem_id = obter_estado(
        "minecraft_nicknames_table_message_id"
    )

    if mensagem_id:
        try:
            mensagem = await canal.fetch_message(
                int(mensagem_id)
            )
        except (
            ValueError,
            discord.NotFound,
            discord.Forbidden,
            discord.HTTPException
        ):
            mensagem = None

    tabelas = []

    if mensagem is None:
        tabelas = await _localizar_tabelas_nicknames(
            canal
        )

        if tabelas:
            mensagem = tabelas[0]

            salvar_estado(
                "minecraft_nicknames_table_message_id",
                mensagem.id
            )

    embed = criar_embed_tabela_nicknames(
        guild
    )

    if mensagem is None:
        mensagem = await canal.send(
            embed=embed
        )

        salvar_estado(
            "minecraft_nicknames_table_message_id",
            mensagem.id
        )

        print(
            "Tabela de nicknames criada: "
            f"{mensagem.id}"
        )

    else:
        await mensagem.edit(
            embed=embed
        )

    if not tabelas:
        tabelas = await _localizar_tabelas_nicknames(
            canal
        )

    for duplicada in tabelas:
        if duplicada.id == mensagem.id:
            continue

        try:
            await duplicada.delete()
        except (
            discord.Forbidden,
            discord.HTTPException
        ):
            pass

    return mensagem.id


async def publicar_nickname(
    membro,
    nickname
):
    # A tabela pública é única.
    # O cadastro individual fica apenas no banco.
    atualizar_cadastro_nick(
        membro.guild.id,
        membro.id,
        mensagem_id=None
    )

    await atualizar_tabela_nicknames(
        membro.guild
    )

    return None


async def aplicar_castigo_nick(membro):
    bot_member = membro.guild.me
    if bot_member is None or not bot_member.guild_permissions.moderate_members:
        return False, 'Bot sem permissão Moderar membros.'
    if membro.id == membro.guild.owner_id or bot_member.top_role <= membro.top_role:
        return False, 'Hierarquia impede o timeout.'
    try:
        ate = datetime.now(timezone.utc) + timedelta(days=CASTIGO_DIAS)
        await membro.timeout(ate, reason='Nickname Minecraft não informado após 4 avisos em 48h.')
        atualizar_cadastro_nick(membro.guild.id, membro.id, castigo_aplicado=1)
        return True, None
    except (discord.Forbidden, discord.HTTPException) as erro:
        return False, str(erro)


async def concluir_nickname(
    membro,
    nickname,
    origem="informado pelo membro"
):
    nickname = (
        nickname.strip()
        [:NICK_MAX_CARACTERES]
    )

    if not nickname:
        return False

    cadastro = buscar_cadastro_nick(
        membro.guild.id,
        membro.id
    )

    if (
        cadastro
        and cadastro["castigo_aplicado"]
        and not tem_ban_pendente_com_castigo(
            membro.guild.id,
            membro.id
        )
    ):
        try:
            await membro.timeout(
                None,
                reason=(
                    "Nickname Minecraft informado."
                )
            )
        except (
            discord.Forbidden,
            discord.HTTPException
        ):
            pass

    atualizar_cadastro_nick(
        membro.guild.id,
        membro.id,
        nickname=nickname,
        status="ativo",
        pendente_desde=None,
        avisos_enviados=0,
        solicitacao_enviada=1,
        castigo_aplicado=0,
        mensagem_id=None,
        saiu_em=None
    )

    await atualizar_tabela_nicknames(
        membro.guild
    )

    await enviar_log_dono(
        "🎮 **Nickname cadastrado**\n"
        f"Usuário: {membro} ({membro.id})\n"
        f"Nickname: `{nickname}`\n"
        f"Origem: {origem}"
    )

    return True


async def avisar_nick_pre_cadastrado(membro, nickname):
    """Envia a confirmação somente uma vez para cada usuário."""
    chave = (
        "nick_pre_cadastrado_dm_"
        f"{membro.guild.id}_{membro.id}"
    )

    if obter_estado(chave) == "1":
        return

    texto = (
        "✅ **Seu nickname do Minecraft já foi cadastrado**\n\n"
        f"🎮 Nickname: `{nickname}`\n\n"
        "Você já estava na lista antiga do servidor, então "
        "não precisa responder aos avisos de cadastro."
    )

    enviado = False

    try:
        await membro.send(texto)
        enviado = True

    except (
        discord.Forbidden,
        discord.HTTPException
    ):
        await enviar_log_dono(
            "⚠️ Não consegui enviar a confirmação do nickname "
            f"pré-cadastrado para {membro} ({membro.id})."
        )

    # Marca como processado para não ficar tentando/spamando
    # a cada reinicialização do bot.
    salvar_estado(chave, "1")

    if enviado:
        await enviar_log_dono(
            "✅ Confirmação de nickname pré-cadastrado enviada para "
            f"{membro} ({membro.id}) — `{nickname}`"
        )


async def importar_nicks_pre_cadastrados():
    """
    Importa cadastros antigos antes de iniciar os avisos automáticos.
    Isso impede que essas pessoas recebam cobranças de nickname.
    """
    importados = 0
    ausentes = 0

    for guild in bot.guilds:
        for usuario_id, nickname in NICKS_PRE_CADASTRADOS.items():
            membro = guild.get_member(usuario_id)

            if membro is None:
                try:
                    membro = await guild.fetch_member(usuario_id)

                except (
                    discord.NotFound,
                    discord.Forbidden,
                    discord.HTTPException
                ):
                    ausentes += 1
                    continue

            cadastro = buscar_cadastro_nick(
                guild.id,
                usuario_id
            )

            precisa_atualizar = (
                cadastro is None
                or cadastro["nickname"] != nickname
                or cadastro["status"] != "ativo"
                or bool(cadastro["castigo_aplicado"])
            )

            if precisa_atualizar:
                # Garante que existe uma linha no banco sem mandar
                # a pergunta de cadastro.
                iniciar_pendencia_nick(
                    guild.id,
                    usuario_id
                )

                await concluir_nickname(
                    membro,
                    nickname,
                    origem="pré-cadastrado"
                )

                importados += 1

            await avisar_nick_pre_cadastrado(
                membro,
                nickname
            )

    print(
        "Nicknames pré-cadastrados processados | "
        f"Atualizados: {importados} | "
        f"Não encontrados no servidor: {ausentes}"
    )



async def varrer_membros_minecraft():
    total = 0
    for guild in bot.guilds:
        cargo = guild.get_role(CARGO_MINECRAFT_ID)
        if cargo is None:
            continue
        for membro in cargo.members:
            if membro.bot:
                continue
            cadastro = buscar_cadastro_nick(guild.id, membro.id)
            if cadastro is None or not cadastro['nickname']:
                await iniciar_cadastro_nick(membro)
                total += 1
    return total


@tasks.loop(minutes=INTERVALO_NICKS_MINUTOS)
async def verificar_nicknames_minecraft():
    agora = datetime.now(timezone.utc)

    for cadastro in listar_nicks_por_status('pendente'):
        guild = bot.get_guild(cadastro['guild_id'])
        membro = guild.get_member(cadastro['usuario_id']) if guild else None
        if membro is None:
            continue

        try:
            inicio = datetime.fromisoformat(cadastro['pendente_desde'])
        except (TypeError, ValueError):
            inicio = agora

        horas = (agora - inicio).total_seconds() / 3600
        enviados = int(cadastro['avisos_enviados'] or 0)
        proximo = enviados + 1

        if proximo <= 4 and horas >= AVISOS_NICK_HORAS[proximo - 1]:
            dm = await enviar_pergunta_nick(membro, proximo)
            atualizar_cadastro_nick(guild.id, membro.id, avisos_enviados=proximo)
            await enviar_log_dono(
                f"⚠️ Aviso {proximo}/4 de nickname para {membro} ({membro.id}). "
                f"DM: {'enviada' if dm else 'falhou/bloqueada'}."
            )
            if proximo == 4:
                ok, erro = await aplicar_castigo_nick(membro)
                await enviar_log_dono(
                    f"🔒 Timeout de nickname para {membro}: " + ('aplicado.' if ok else f'falhou — {erro}')
                )

        atual = buscar_cadastro_nick(guild.id, membro.id)
        if atual and atual['castigo_aplicado']:
            limite = getattr(membro, 'timed_out_until', None)
            if limite is None or limite < agora + timedelta(days=7):
                await aplicar_castigo_nick(membro)

    for cadastro in listar_nicks_por_status('ausente'):
        try:
            saiu = datetime.fromisoformat(cadastro['saiu_em'])
        except (TypeError, ValueError):
            continue
        if agora - saiu < timedelta(hours=TEMPO_REMOCAO_NICK_APOS_SAIDA_HORAS):
            continue

        guild = bot.get_guild(cadastro['guild_id'])
        if guild and guild.get_member(cadastro['usuario_id']):
            atualizar_cadastro_nick(cadastro['guild_id'], cadastro['usuario_id'], status='ativo', saiu_em=None)
            continue

        await enviar_log_dono(
            f"🗑️ Nickname removido após 48h fora do servidor. "
            f"ID: {cadastro['usuario_id']} | "
            f"Nick: `{cadastro['nickname'] or 'sem nick'}`"
        )

        excluir_cadastro_nick(
            cadastro['guild_id'],
            cadastro['usuario_id']
        )

        if guild is not None:
            await atualizar_tabela_nicknames(
                guild
            )


@verificar_nicknames_minecraft.before_loop
async def antes_de_verificar_nicks():
    await bot.wait_until_ready()


# ==========================================================
# INTENTS
# ==========================================================

intents = discord.Intents.default()

intents.members = True
intents.message_content = True


# ==========================================================
# BOT
# ==========================================================

class MeuBot(commands.Bot):

    async def setup_hook(self):
        self.add_view(
            PainelBanView()
        )
        # --------------------------------------------------
        # RESTAURAR ENQUETES
        # --------------------------------------------------

        for linha in buscar_enquetes_ativas():
            opcoes = [
                linha["opcao1"],
                linha["opcao2"]
            ]

            if linha["opcao3"]:
                opcoes.append(
                    linha["opcao3"]
                )

            self.add_view(
                EnqueteView(
                    linha["id"],
                    linha["pergunta"],
                    opcoes
                ),
                message_id=(
                    linha["mensagem_id"]
                )
            )

        # --------------------------------------------------
        # RESTAURAR PEDIDOS DE BAN
        # --------------------------------------------------

        for linha in buscar_solicitacoes_pendentes():
            self.add_view(
                BanApprovalView(
                    linha["id"]
                ),
                message_id=(
                    linha["mensagem_id"]
                )
            )

        comandos = await self.tree.sync()

        print(
            "Comandos sincronizados:"
        )

        for comando in comandos:
            print(
                f"/{comando.name}"
            )


bot = MeuBot(
    command_prefix="!",
    intents=intents,
    help_command=None
)


# ==========================================================
# RENOVAR CASTIGOS PENDENTES
# ==========================================================

@tasks.loop(hours=168)
async def renovar_castigos_pendentes():
    for linha in buscar_castigos_pendentes():
        guild = bot.get_guild(
            linha["guild_id"]
        )

        if guild is None:
            continue

        membro = await obter_membro(
            guild,
            linha["usuario_id"]
        )

        if membro is None:
            continue

        try:
            ate = (
                datetime.now(
                    timezone.utc
                )
                + timedelta(
                    days=CASTIGO_DIAS
                )
            )

            await membro.timeout(
                ate,
                reason=(
                    "Solicitação de ban "
                    "ainda pendente."
                )
            )

        except (
            discord.Forbidden,
            discord.HTTPException
        ):
            pass


@renovar_castigos_pendentes.before_loop
async def antes_de_renovar():
    await bot.wait_until_ready()


# ==========================================================
# MEMBRO COM PEDIDO PENDENTE VOLTA
# ==========================================================

@bot.event
async def on_member_join(member: discord.Member):
    cadastro = buscar_cadastro_nick(member.guild.id, member.id)
    if cadastro and cadastro['status'] == 'ausente':
        atualizar_cadastro_nick(member.guild.id, member.id, status='ativo' if cadastro['nickname'] else 'pendente', saiu_em=None)
        await enviar_log_dono(f'↩️ {member} ({member.id}) voltou antes da limpeza do nickname.')

    pendente = buscar_pendente_para_usuario(member.guild.id, member.id)
    if pendente is not None:
        ok, erro = await aplicar_castigo(member, 0, 'Existe uma solicitação de ban pendente para este usuário.')
        if ok:
            marcar_castigo(pendente['id'], True)
        else:
            print(f'Não consegui reaplicar castigo para {member.id}: {erro}')


@bot.event
async def on_member_remove(member: discord.Member):
    cadastro = buscar_cadastro_nick(member.guild.id, member.id)
    if cadastro:
        atualizar_cadastro_nick(
            member.guild.id,
            member.id,
            status='ausente',
            saiu_em=datetime.now(timezone.utc).isoformat()
        )
        await enviar_log_dono(
            f'🚪 {member} ({member.id}) saiu. O nickname será removido se não voltar em 48h.'
        )


@bot.event
async def on_member_update(before: discord.Member, after: discord.Member):
    tinha = any(cargo.id == CARGO_MINECRAFT_ID for cargo in before.roles)
    tem = any(cargo.id == CARGO_MINECRAFT_ID for cargo in after.roles)
    if not tinha and tem and not after.bot:
        await iniciar_cadastro_nick(after)


@bot.event
async def on_message(
    message: discord.Message
):
    if message.author.bot:
        return

    if isinstance(
        message.channel,
        discord.DMChannel
    ):
        pendencias = (
            buscar_pendencias_nick_usuario(
                message.author.id
            )
        )

        if pendencias:
            cadastro = pendencias[0]
            guild = bot.get_guild(
                cadastro["guild_id"]
            )

            membro = (
                guild.get_member(
                    message.author.id
                )
                if guild
                else None
            )

            if membro is not None:
                nickname = " ".join(
                    message.content
                    .strip()
                    .split()
                )

                if nickname:
                    formato_ok, motivo = (
                        validar_formato_nickname(
                            nickname
                        )
                    )

                    if not formato_ok:
                        await responder_nick_invalido(
                            message.channel,
                            motivo
                        )
                        return

                    await concluir_nickname(
                        membro,
                        nickname,
                        origem="informado pelo membro"
                    )

                    await message.channel.send(
                        "✅ **Nickname cadastrado!**\n"
                        f"🎮 `{nickname}`\n\n"
                        "Se estiver errado, a equipe pode "
                        "solicitar um novo cadastro."
                    )
                    return

    await bot.process_commands(
        message
    )


# ==========================================================
# ONLINE
# ==========================================================

@bot.event
async def on_ready():
    if not (
        renovar_castigos_pendentes
        .is_running()
    ):
        renovar_castigos_pendentes.start()

    if not (
        monitorar_minecraft
        .is_running()
    ):
        monitorar_minecraft.start()

    # Importa os nicknames antigos antes de iniciar qualquer
    # cobrança automática. Assim eles não recebem avisos indevidos.
    if not getattr(bot, "_nicks_pre_cadastrados_importados", False):
        bot._nicks_pre_cadastrados_importados = True
        await importar_nicks_pre_cadastrados()

    # Garante uma única tabela de nicknames no canal.
    for guild in bot.guilds:
        try:
            await atualizar_tabela_nicknames(
                guild
            )
        except Exception as erro:
            print(
                "Erro ao atualizar tabela de nicknames "
                f"na inicialização: {erro}"
            )

    if not verificar_nicknames_minecraft.is_running():
        verificar_nicknames_minecraft.start()

    if not getattr(bot, '_scan_nicks_feito', False):
        bot._scan_nicks_feito = True
        asyncio.create_task(varrer_membros_minecraft())

    print("--------------------------------")
    print(f"Bot conectado como: {bot.user}")
    print("Monitor Minecraft: ATIVO")
    print(
        f"Servidor monitorado: "
        f"{MINECRAFT_HOST}:{MINECRAFT_PORTA}"
    )
    print(
        "Canal de status Minecraft: "
        f"{CANAL_STATUS_MINECRAFT_ID}"
    )
    print("--------------------------------")



# ==========================================================
# /ENQUETE
# ==========================================================

@bot.tree.command(
    name="enquete",
    description="Cria uma enquete"
)
async def enquete(
    interaction: discord.Interaction
):
    if await negar_se_nao_admin(interaction):
        return

    await interaction.response.send_modal(
        EnqueteModal()
    )


# ==========================================================
# /PAINELBAN
# ==========================================================

@bot.tree.command(
    name="painelban",
    description=(
        "Envia o painel da Equipe de Ban"
    )
)
async def painelban(
    interaction: discord.Interaction
):
    if await negar_se_nao_admin(interaction):
        return

    embed = discord.Embed(
        title=(
            "🛡️ Painel da Equipe de Ban"
        ),
        description=(
            "Escolha abaixo o tipo "
            "de solicitação.\n\n"

            "### 👤 Solicitar Ban\n"
            "Abre o seletor de usuários "
            "do Discord.\n\n"

            "### 🆔 Solicitar Hackban\n"
            "Use o ID do usuário, inclusive "
            "se ele já saiu do servidor.\n\n"

            "### 📝 Motivo\n"
            "✍️ **Escrever o motivo**\n"
            "✅ **Motivo já informado**"
        ),
        color=discord.Color.dark_red()
    )

    embed.set_footer(
        text=(
            "Somente a Equipe de Desenvolvimento e o dono autorizado "
            "podem utilizar este painel."
        )
    )

    await interaction.response.send_message(
        embed=embed,
        view=PainelBanView()
    )


# ==========================================================
# /SOLICITARBAN
# ==========================================================

@bot.tree.command(
    name="solicitarban",
    description="Solicita um Ban diretamente"
)
@app_commands.describe(
    usuario="Usuário que será banido",
    motivo="Motivo da solicitação"
)
async def solicitarban(
    interaction: discord.Interaction,
    usuario: discord.Member,
    motivo: str
):
    if not pode_usar_sistema_ban(
        interaction.user
    ):
        await interaction.response.send_message(
            "❌ Você não possui autorização.",
            ephemeral=True
        )
        return

    motivo = motivo.strip()

    if not motivo:
        await interaction.response.send_message(
            "❌ O motivo é obrigatório.",
            ephemeral=True
        )
        return

    await preparar_e_enviar_solicitacao(
        interaction,
        usuario.id,
        "ban",
        "escrito",
        motivo
    )


# ==========================================================
# /SINCRONIZARNICKS
# ==========================================================

@bot.tree.command(
    name="sincronizarnicks",
    description="Verifica membros do cargo Minecraft sem nickname cadastrado"
)
async def sincronizarnicks(interaction: discord.Interaction):
    if await negar_se_nao_admin(interaction):
        return
    await interaction.response.defer(ephemeral=True, thinking=True)
    total = await varrer_membros_minecraft()
    await interaction.followup.send(
        f"✅ Varredura concluída. {total} cadastro(s) pendente(s) processado(s).",
        ephemeral=True
    )


# ==========================================================
# /SOLICITARNICKNOVAMENTE
# ==========================================================

@bot.tree.command(
    name="solicitarnicknovamente",
    description=(
        "Invalida o nickname atual "
        "e pede um novo cadastro"
    )
)
@app_commands.describe(
    usuario=(
        "Membro que precisa informar "
        "o nickname novamente"
    )
)
async def solicitarnicknovamente(
    interaction: discord.Interaction,
    usuario: discord.Member
):
    if await negar_se_nao_admin(
        interaction
    ):
        return

    possui_cargo = any(
        cargo.id == CARGO_MINECRAFT_ID
        for cargo in usuario.roles
    )

    if not possui_cargo:
        await interaction.response.send_message(
            "❌ Esse membro não possui o cargo Minecraft.",
            ephemeral=True
        )
        return

    await interaction.response.defer(
        ephemeral=True,
        thinking=True
    )

    iniciar_pendencia_nick(
        interaction.guild.id,
        usuario.id
    )

    atualizar_cadastro_nick(
        interaction.guild.id,
        usuario.id,
        nickname=None,
        status="pendente",
        pendente_desde=datetime.now(
            timezone.utc
        ).isoformat(),
        avisos_enviados=0,
        solicitacao_enviada=0,
        castigo_aplicado=0,
        mensagem_id=None,
        saiu_em=None
    )

    enviado = await enviar_pergunta_nick(
        usuario
    )

    atualizar_cadastro_nick(
        interaction.guild.id,
        usuario.id,
        solicitacao_enviada=1
    )

    await enviar_log_dono(
        "🔄 **Nickname solicitado novamente**\n"
        f"Usuário: {usuario} ({usuario.id})\n"
        f"Solicitado por: {interaction.user} "
        f"({interaction.user.id})\n"
        f"DM: "
        f"{'enviada' if enviado else 'fechada/bloqueada'}"
    )

    await interaction.followup.send(
        (
            "✅ Novo cadastro solicitado para "
            f"{usuario.mention}."
            + (
                "\nA DM foi enviada normalmente."
                if enviado
                else
                "\nA DM está fechada; "
                "o bot avisou a pessoa no chat geral."
            )
        ),
        ephemeral=True
    )



# ==========================================================
# /ADICIONARNICKMANUAL
# ==========================================================

@bot.tree.command(
    name="adicionarnickmanual",
    description=(
        "Cadastra um nickname manualmente "
        "sem validação externa"
    )
)
@app_commands.describe(
    usuario="Membro que receberá o nickname",
    nickname="Nickname correto do Minecraft"
)
async def adicionarnickmanual(
    interaction: discord.Interaction,
    usuario: discord.Member,
    nickname: str
):
    if await negar_se_nao_admin(
        interaction
    ):
        return

    possui_cargo = any(
        cargo.id == CARGO_MINECRAFT_ID
        for cargo in usuario.roles
    )

    if not possui_cargo:
        await interaction.response.send_message(
            "❌ Esse membro não possui "
            "o cargo Minecraft.",
            ephemeral=True
        )
        return

    nickname = " ".join(
        nickname.strip().split()
    )

    formato_ok, motivo = (
        validar_formato_nickname(
            nickname
        )
    )

    if not formato_ok:
        await interaction.response.send_message(
            f"❌ {motivo}",
            ephemeral=True
        )
        return

    await interaction.response.defer(
        ephemeral=True,
        thinking=True
    )

    iniciar_pendencia_nick(
        interaction.guild.id,
        usuario.id
    )

    await concluir_nickname(
        usuario,
        nickname,
        origem=(
            "cadastro manual por "
            f"{interaction.user} "
            f"({interaction.user.id})"
        )
    )

    try:
        await usuario.send(
            "✅ **Seu nickname do Minecraft "
            "foi cadastrado manualmente pela equipe.**\n\n"
            f"🎮 Nickname: `{nickname}`"
        )
    except (
        discord.Forbidden,
        discord.HTTPException
    ):
        await avisar_dm_fechada_no_chat(
            usuario
        )

    await interaction.followup.send(
        "✅ Nickname cadastrado manualmente "
        f"para {usuario.mention}: `{nickname}`",
        ephemeral=True
    )


# ==========================================================
# /STATUSMINECRAFT
# ==========================================================

@bot.tree.command(
    name="statusminecraft",
    description=(
        "Verifica se o servidor Minecraft "
        "está acessível agora"
    )
)
async def statusminecraft(
    interaction: discord.Interaction
):
    if await negar_se_nao_admin(interaction):
        return

    await interaction.response.defer(
        ephemeral=True,
        thinking=True
    )

    online = await minecraft_esta_online()

    if online:
        mensagem = (
            "🟢 O servidor Minecraft "
            "está acessível agora."
        )

    else:
        mensagem = (
            "🔴 O servidor Minecraft "
            "parece estar offline agora."
        )

    await interaction.followup.send(
        mensagem,
        ephemeral=True
    )


# ==========================================================
# ERROS
# ==========================================================

@bot.event
async def on_command_error(
    ctx,
    erro
):
    if isinstance(
        erro,
        commands.CommandNotFound
    ):
        return

    print(
        f"Erro comando !: {erro}"
    )


@bot.tree.error
async def erro_slash(
    interaction,
    erro
):
    print(
        f"Erro comando /: {repr(erro)}"
    )

    if isinstance(
        erro,
        app_commands.MissingPermissions
    ):
        mensagem = (
            "❌ Você não possui permissão."
        )

    else:
        mensagem = (
            "❌ Ocorreu um erro ao "
            "executar esse comando."
        )

    try:
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

    except discord.HTTPException:
        pass


# ==========================================================
# TOKEN
# ==========================================================

token = (
    os.environ.get("TOKEN")
    or os.getenv("TOKEN")
)

print(
    "Variável TOKEN encontrada:",
    bool(token)
)

if not token:
    raise ValueError(
        "O token não foi encontrado."
    )


# ==========================================================
# INICIAR
# ==========================================================

bot.run(token)