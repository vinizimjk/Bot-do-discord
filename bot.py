import json
import os
from pathlib import Path

import discord
from discord.ext import commands
from dotenv import load_dotenv


# ==========================================================
# CAMINHOS DOS ARQUIVOS
# ==========================================================

PASTA_BOT = Path(__file__).parent
ARQUIVO_ENV = PASTA_BOT / ".env"
ARQUIVO_CONFIG = PASTA_BOT / "config.json"

load_dotenv(dotenv_path=ARQUIVO_ENV)


# ==========================================================
# CONFIGURAÇÃO DA INTERFACE PRINCIPAL
# ==========================================================

CONFIG_PADRAO = {
    "mensagem_principal": (
        "## 🎉 Evento Sub Civil\n"
        "Selecione uma das opções abaixo para saber mais."
    )
}


def salvar_config(configuracao):
    with open(ARQUIVO_CONFIG, "w", encoding="utf-8") as arquivo:
        json.dump(
            configuracao,
            arquivo,
            ensure_ascii=False,
            indent=4
        )


def carregar_config():
    if not ARQUIVO_CONFIG.exists():
        salvar_config(CONFIG_PADRAO.copy())
        return CONFIG_PADRAO.copy()

    try:
        with open(ARQUIVO_CONFIG, "r", encoding="utf-8") as arquivo:
            configuracao = json.load(arquivo)

    except (json.JSONDecodeError, OSError):
        salvar_config(CONFIG_PADRAO.copy())
        return CONFIG_PADRAO.copy()

    if "mensagem_principal" not in configuracao:
        configuracao["mensagem_principal"] = CONFIG_PADRAO["mensagem_principal"]
        salvar_config(configuracao)

    return configuracao


carregar_config()


# ==========================================================
# CONFIGURAÇÃO DO BOT
# ==========================================================

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(
    command_prefix="!",
    intents=intents,
    help_command=None
)


# ==========================================================
# MENU DE SELEÇÃO
# ==========================================================

class MenuSubCivil(discord.ui.Select):

    def __init__(self):
        opcoes = [
            discord.SelectOption(
                label="Quais são as vantagens de ter Sub Civil?",
                description="Veja todos os benefícios do cargo.",
                emoji="⭐",
                value="vantagens"
            ),

            discord.SelectOption(
                label="Por onde interagir?",
                description="Saiba onde as interações serão contabilizadas.",
                emoji="💬",
                value="interagir"
            ),

            discord.SelectOption(
                label="Como saberemos quem mais interagiu?",
                description="Entenda como o vencedor será escolhido.",
                emoji="🏆",
                value="ranking"
            )
        ]

        super().__init__(
            placeholder="Selecione uma opção",
            custom_id="menu_sub_civil",
            min_values=1,
            max_values=1,
            options=opcoes
        )

    async def callback(self, interaction: discord.Interaction):
        opcao = self.values[0]

        if opcao == "vantagens":
            mensagem = (
                "## ⭐ Vantagens de ter Sub Civil\n\n"
                "• 🎵 Utilizar efeitos sonoros.\n"
                "• 📹 Abrir câmera.\n"
                "• 🖥️ Transmitir tela.\n"
                "• 🚀 Ignorar o modo lento.\n"
                "• 🎨 Cor exclusiva no nome.\n"
                "• ⭐ Cargo destacado na lista de membros.\n"
                "• 🔊 Prioridade em canais de voz.\n"
                "• 💬 Acesso a um chat exclusivo."
            )

        elif opcao == "interagir":
            mensagem = (
                "## 💬 Por onde interagir?\n\n"
                "A interação deverá ser feita por meio de conversas nos "
                "canais de texto do servidor, para que a Loritta consiga "
                "reconhecer e contabilizar a atividade."
            )

        elif opcao == "ranking":
            mensagem = (
                "## 🏆 Como saberemos quem mais interagiu?\n\n"
                "Cinco minutos antes do início do prazo, iremos reiniciar "
                "o XP de todos os membros.\n\n"
                "Quando o prazo terminar, verificaremos o ranking da Loritta. "
                "O membro que estiver no topo será o vencedor e receberá "
                "o cargo de **Sub Civil**."
            )

        else:
            mensagem = "❌ Opção não encontrada."

        await interaction.response.send_message(
            mensagem,
            ephemeral=True
        )


class MenuView(discord.ui.View):

    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(MenuSubCivil())


# ==========================================================
# BOT ONLINE
# ==========================================================

menu_registrado = False


@bot.event
async def on_ready():
    global menu_registrado

    if not menu_registrado:
        bot.add_view(MenuView())
        menu_registrado = True

    print("--------------------------------")
    print(f"Bot conectado como: {bot.user}")
    print("--------------------------------")


# ==========================================================
# COMANDO PARA ENVIAR O MENU
# ==========================================================

@bot.command()
@commands.has_permissions(administrator=True)
async def menu(ctx: commands.Context):
    configuracao = carregar_config()

    await ctx.send(
        configuracao["mensagem_principal"],
        view=MenuView()
    )


# ==========================================================
# EDITAR SOMENTE A INTERFACE PRINCIPAL
# ==========================================================

@bot.command()
@commands.has_permissions(administrator=True)
async def editar_interface(
    ctx: commands.Context,
    *,
    novo_texto: str = None
):
    if novo_texto is None:
        await ctx.send(
            "❌ Escreva o novo texto da interface principal.\n\n"
            "**Exemplo:**\n"
            "`!editar_interface Participe do nosso evento e concorra ao Sub Civil!`"
        )
        return

    configuracao = carregar_config()
    configuracao["mensagem_principal"] = novo_texto
    salvar_config(configuracao)

    await ctx.send(
        "✅ A interface principal foi alterada com sucesso.\n\n"
        f"**Novo texto:**\n{novo_texto}\n\n"
        "Use `!menu` para enviar o painel atualizado."
    )


# ==========================================================
# COMANDO DE AJUDA
# ==========================================================

@bot.command()
@commands.has_permissions(administrator=True)
async def comandos(ctx: commands.Context):
    await ctx.send(
        "## ⚙️ Comandos do bot\n\n"
        "`!menu` — Envia o menu do evento.\n\n"
        "`!editar_interface texto` — Altera somente o texto principal.\n\n"
        "`!comandos` — Mostra esta lista."
    )


# ==========================================================
# TRATAMENTO DE ERROS
# ==========================================================

@bot.event
async def on_command_error(ctx: commands.Context, erro):
    if isinstance(erro, commands.MissingPermissions):
        await ctx.send(
            "❌ Você precisa ter permissão de Administrador."
        )
        return

    if isinstance(erro, commands.CommandNotFound):
        return

    print(f"Erro encontrado: {erro}")

    await ctx.send(
        "❌ Ocorreu um erro. Verifique o terminal do bot."
    )


# ==========================================================
# INICIAR O BOT
# ==========================================================

token = os.environ.get("TOKEN")

print("Variável TOKEN encontrada:", bool(token))

if not token:
    raise ValueError(
        "O token não foi encontrado nas variáveis da Railway."
    )

bot.run(token)