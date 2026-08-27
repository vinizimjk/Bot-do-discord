# Resenha Máxima — reconstrução completa do painel

Este pacote foi reconstruído a partir da versão atual do painel e consolida:

- Menus do Discord com configurações salvas por canal.
- Hotfix do `/menu`: emoji inválido não derruba o menu inteiro.
- Mensagens do Bot.
- Central Administrativa.
- Entradas e ranking de convites.
- Central de Atualizações e futuras atualizações com stickers.
- Aba `🤖 IA` apontando para a rota real `/ia`.
- Página de configuração da IA.
- API `/api/ia-config` usada pelo bot principal.
- Login e identidade visual existentes.

## Substituição
Copie TODO o conteúdo deste pacote para a raiz de `painel-menu-bot`, aceitando substituir os arquivos existentes.

## Deploy
```powershell
git add .
git commit -m "Reconstruir painel completo e corrigir IA e menus"
git push
```

## Testes rápidos
Depois do deploy:
- `/` abre o painel normalmente.
- `/ia` abre a configuração da IA (para acesso total).
- `/api/ia-config` retorna JSON.
- O botão 🤖 IA aponta diretamente para `/ia`.
