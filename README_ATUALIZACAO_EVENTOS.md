# Correção de sincronização — Painel de Eventos

Incluído:
- Eventos e acesso total editam o mesmo registro de menu por canal.
- O painel mostra quem fez a última edição e quando.
- O `/menu` sempre recarrega o JSON salvo no momento da execução.
- Emoji inválido continua sendo ignorado sem derrubar o menu.
- Erros de publicação do `/menu` agora retornam mensagem em vez de falhar silenciosamente.

Instalação:
Substitua os arquivos do projeto pelos deste pacote e rode:

git add .
git commit -m "Corrigir sincronizacao do painel de eventos e menu"
git push
