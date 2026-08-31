# Candidatura — Departamento de Eventos

Esta atualização altera somente o fluxo de candidatura.

## Discord

- Servidor principal: o bot publica/atualiza o menu no canal `1541035337709649990`.
- Servidor de Eventos: ao configurar pelo painel, o bot cria somente `📜・𝑪𝒂𝒏𝒅𝒊𝒅𝒂𝒕𝑼𝑹𝒂` se ele ainda não existir. Ele não recria os demais canais/categorias.
- O menu possui `Informações` e `Fazer candidatura`.
- `Fazer candidatura` abre o Forms configurado por `GOOGLE_FORMS_URL`.

## Depois do Forms

O site expõe `POST /api/candidatura-eventos`. O arquivo `GOOGLE_FORMS_WEBHOOK.gs` envia as respostas da planilha do Google Forms para esse endpoint.

O formulário precisa possuir uma pergunta com "Discord" e "ID" no título, por exemplo: `Qual é o seu ID do Discord?`.

No Apps Script:
1. Troque `https://SEU-DOMINIO` pelo domínio do site.
2. Opcionalmente configure `FORM_WEBHOOK_SECRET` no site e o mesmo valor em `WEBHOOK_SECRET` no script.
3. Crie um gatilho instalável para `enviarCandidaturaAoBot` em `Ao enviar formulário`/`On form submit` da planilha de respostas.

## Fluxo de avaliação

1. Resposta recebida -> ticket temporário no servidor principal.
2. Ticket visível apenas ao candidato, Diretor de Eventos e bot.
3. Respostas são exibidas em embeds no ticket.
4. Diretor aprova a prova -> call privada é criada com as mesmas pessoas.
5. Diretor aprova após a call -> cargo `Departamento de Eventos` é adicionado no servidor principal.
6. Se o candidato também estiver no servidor de Eventos, recebe `Aprendiz de Eventos` e perde `Intruso` quando esses cargos existirem.
7. Ticket e call são removidos após a conclusão.
