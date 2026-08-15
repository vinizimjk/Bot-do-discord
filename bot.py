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


# ==========================================================
# CONFIGURAÇÕES PRINCIPAIS
# ==========================================================

DONO_ID = 1455937306400653344
CANAL_APROVACAO_ID = 1536073451633254420
PAINEL_MENU_URL = "https://painel-menu-bot-production.up.railway.app"

CARGO_MINECRAFT_ID = 1534006899371147304
CANAL_STATUS_MINECRAFT_ID = 1538109074779144253
CARGO_DESENVOLVIMENTO_ID = 1533625836874498181
MINECRAFT_HOST = "resenha-DpsX.aternos.me"
MINECRAFT_PORTA = 20710

CASTIGO_DIAS = 28

INTERVALO_MINECRAFT_SEGUNDOS = 60
FALHAS_OFFLINE_NECESSARIAS = 3
SUCESSOS_ONLINE_NECESSARIOS = 2


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

    return membro.guild_permissions.administrator


def pode_usar_sistema_ban(membro):
    # Painéis e botões de moderação seguem a mesma regra:
    # dono autorizado ou administrador do servidor.
    return pode_usar_comando_admin(membro)


async def negar_se_nao_admin(interaction):
    if pode_usar_comando_admin(interaction.user):
        return False

    await interaction.response.send_message(
        "❌ Apenas administradores do servidor e o dono autorizado podem usar este comando.",
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
        descricao = (
            "O servidor de Minecraft da **Resenha Máxima** "
            "está disponível agora.\n\n"
            "🎮 Pode entrar e jogar normalmente."
        )
        cor = discord.Color.green()
    else:
        titulo = "🔴 SERVIDOR MINECRAFT OFFLINE"
        descricao = (
            "O servidor de Minecraft da **Resenha Máxima** "
            "está offline no momento.\n\n"
            "A mensagem será atualizada automaticamente "
            "quando o servidor voltar."
        )
        cor = discord.Color.red()

    embed = discord.Embed(
        title=titulo,
        description=descricao,
        color=cor,
        timestamp=datetime.now(timezone.utc)
    )

    embed.add_field(
        name="Status",
        value="🟢 Online" if online else "🔴 Offline",
        inline=True
    )

    embed.add_field(
        name="Monitoramento",
        value="Atualização automática",
        inline=True
    )

    embed.set_footer(
        text="Resenha Máxima • Minecraft • Última verificação"
    )

    return embed


async def obter_canal_status_minecraft():
    canal = bot.get_channel(
        CANAL_STATUS_MINECRAFT_ID
    )

    if canal is None:
        try:
            canal = await bot.fetch_channel(
                CANAL_STATUS_MINECRAFT_ID
            )

        except (
            discord.NotFound,
            discord.Forbidden,
            discord.HTTPException
        ) as erro:
            print(
                "Não consegui acessar o canal "
                "de status do Minecraft | "
                f"Erro: {erro}"
            )
            return None

    if not hasattr(canal, "send"):
        print(
            "O canal de status do Minecraft "
            "não aceita mensagens."
        )
        return None

    return canal


async def atualizar_mensagem_status_minecraft(online):
    canal = await obter_canal_status_minecraft()

    if canal is None:
        return

    embed = criar_embed_status_minecraft(
        online
    )

    mensagem_id = obter_estado(
        "minecraft_status_message_id"
    )

    mensagem = None

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

    if mensagem is None:
        try:
            mensagem = await canal.send(
                embed=embed
            )

            salvar_estado(
                "minecraft_status_message_id",
                str(mensagem.id)
            )

            print(
                "Mensagem de status Minecraft "
                f"criada: {mensagem.id}"
            )

        except discord.HTTPException as erro:
            print(
                "Erro ao criar mensagem de "
                f"status Minecraft: {erro}"
            )

        return

    try:
        await mensagem.edit(
            embed=embed
        )

    except discord.HTTPException as erro:
        print(
            "Erro ao atualizar mensagem de "
            f"status Minecraft: {erro}"
        )


# ==========================================================
# MINECRAFT - CHECAR SERVIDOR
# ==========================================================

async def minecraft_esta_online():
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(
                MINECRAFT_HOST,
                MINECRAFT_PORTA
            ),
            timeout=6
        )

        writer.close()

        try:
            await writer.wait_closed()

        except Exception:
            pass

        return True

    except (
        asyncio.TimeoutError,
        ConnectionError,
        OSError
    ):
        return False


# ==========================================================
# MINECRAFT - MONITOR
# ==========================================================

falhas_minecraft = 0
sucessos_minecraft = 0


@tasks.loop(
    seconds=INTERVALO_MINECRAFT_SEGUNDOS
)
async def monitorar_minecraft():
    global falhas_minecraft
    global sucessos_minecraft

    online_agora = (
        await minecraft_esta_online()
    )

    estado_salvo = obter_estado(
        "minecraft_online"
    )

    # ------------------------------------------------------
    # PRIMEIRA CHECAGEM
    # ------------------------------------------------------

    if estado_salvo is None:
        salvar_estado(
            "minecraft_online",
            "1" if online_agora else "0"
        )

        falhas_minecraft = 0
        sucessos_minecraft = 0

        print(
            "Estado inicial Minecraft: "
            + (
                "ONLINE"
                if online_agora
                else "OFFLINE"
            )
        )

        await atualizar_mensagem_status_minecraft(
            online_agora
        )
        return

    estava_online = (
        estado_salvo == "1"
    )

    # ------------------------------------------------------
    # RESPOSTA POSITIVA
    # ------------------------------------------------------

    if online_agora:
        falhas_minecraft = 0

        if estava_online:
            sucessos_minecraft = 0

            # Mantém a mensagem viva e atualiza
            # o horário da última verificação.
            await atualizar_mensagem_status_minecraft(
                True
            )
            return

        sucessos_minecraft += 1

        if (
            sucessos_minecraft
            < SUCESSOS_ONLINE_NECESSARIOS
        ):
            print(
                "Possível Minecraft ONLINE | "
                f"confirmação "
                f"{sucessos_minecraft}/"
                f"{SUCESSOS_ONLINE_NECESSARIOS}"
            )
            return

        sucessos_minecraft = 0

        salvar_estado(
            "minecraft_online",
            "1"
        )

        print(
            "Minecraft mudou de "
            "OFFLINE para ONLINE."
        )

        await atualizar_mensagem_status_minecraft(
            True
        )
        return

    # ------------------------------------------------------
    # RESPOSTA NEGATIVA
    # ------------------------------------------------------

    sucessos_minecraft = 0

    if not estava_online:
        falhas_minecraft = 0

        await atualizar_mensagem_status_minecraft(
            False
        )
        return

    falhas_minecraft += 1

    if (
        falhas_minecraft
        < FALHAS_OFFLINE_NECESSARIAS
    ):
        print(
            "Possível Minecraft OFFLINE | "
            f"confirmação "
            f"{falhas_minecraft}/"
            f"{FALHAS_OFFLINE_NECESSARIAS}"
        )
        return

    falhas_minecraft = 0

    salvar_estado(
        "minecraft_online",
        "0"
    )

    print(
        "Minecraft mudou de "
        "ONLINE para OFFLINE."
    )

    await atualizar_mensagem_status_minecraft(
        False
    )


@monitorar_minecraft.before_loop
async def antes_de_monitorar_minecraft():
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
async def on_member_join(
    member: discord.Member
):
    pendente = (
        buscar_pendente_para_usuario(
            member.guild.id,
            member.id
        )
    )

    if pendente is None:
        return

    ok, erro = await aplicar_castigo(
        member,
        0,
        (
            "Existe uma solicitação de "
            "ban pendente para este usuário."
        )
    )

    if ok:
        marcar_castigo(
            pendente["id"],
            True
        )

    else:
        print(
            "Não consegui reaplicar castigo "
            f"para {member.id}: {erro}"
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
            "Somente administradores e o dono autorizado "
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