# RESENHA MÁXIMA — Correção Call + Servidor Ativo

Esta atualização foi aplicada sobre o ZIP enviado pelo responsável do projeto, preservando a estrutura e as funções existentes.

## Correções críticas
- Removido o `@bot.event` indevido de `localizar_guild_eventos()`, que causava `TypeError: event registered must be a coroutine function` e derrubava o bot antes do login.
- Restaurado `@bot.event` em `on_member_remove()`.
- `bot.py` principal e a cópia usada no projeto do painel foram alinhados para não ficarem em versões diferentes.

## Call
- Entrada automática na call de desenvolvimento continua ativa.
- Ao entrar em call, o bot não fica mais com `self_mute=True` nem `self_deaf=True`.
- O estado visual de microfone e fone permanece ligado.
- O bot pode entrar silenciosamente em qualquer call via `/chamarcall` ou pela frase natural configurada.
- A playlist da call de desenvolvimento continua em `audios_call/PLAYLIST.json`.
- Se a playlist não tiver arquivos válidos, o bot apenas entra e permanece em silêncio.
- Zoarcall permanece limitado a 2 áudios e com cooldown individual.

## Painel / troca de servidor
- Adicionado seletor de servidor ativo no painel.
- O painel lista todos os servidores onde o bot está presente.
- O servidor principal continua disponível para configuração.
- O servidor do Departamento de Eventos pode ser selecionado depois de criado.
- Cargos, canais e permissões consultados pelo painel passam a usar o servidor ativo da sessão.
- Ao criar/configurar o servidor de Eventos, ele passa a ser selecionado automaticamente.

## Segurança
- O `.env` do ZIP original não é incluído neste pacote de atualização.
- Não substitua variáveis/secrets do ambiente do Railway pelo conteúdo do ZIP.

## Validação
`bot.py`, `painel-menu-bot/main.py` e a cópia do bot foram verificados com `py_compile`.
