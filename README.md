# Atualização IA + Site — Resenha Máxima

## Arquivos
- `bot/bot.py`: atualização do bot principal.
- `site/main.py`: atualização do backend do painel.
- `site/templates/ia.html`: nova tela de configuração da IA.

## Nova tela
Depois do deploy do site:
`https://resenha-maxima.up.railway.app/ia`

A tela exige acesso total do painel.

## Como o bot recebe as configurações
O bot consulta:
`https://resenha-maxima.up.railway.app/api/ia-config`

Se o domínio mudar, defina no Railway do bot:
`IA_CONFIG_URL=https://NOVO-DOMINIO/api/ia-config`

A chave da Groq NÃO é enviada pelo site e continua nas variáveis do Railway.

## Minecraft
Atualizado para:
- Host: Rmax-j8Un.aternos.me
- Porta: 16184

## Observação
O emoji de erro interno é um recurso de diagnóstico e não foi incluído nas notas públicas do bot.
